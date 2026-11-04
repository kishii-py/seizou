"""送れなかった発注の知らせと送り直しの後の画面・受入の残りの数・断られた発注と取消の発注の受入・発注の取消と送り直しの釦の字 (N96。精査 A7 の 1-1 の S2・S15・S6・S9・不具合 7)。

相手の受け口への HTTP は requests.post を偽の物に差し替えて見る (tests.test_purchasing_views の FakePost)。
右上の知らせは get_messages(response.wsgi_request) の (level_tag, 字) で見る。取引先と品目は作り物。
"""

from datetime import date
from decimal import Decimal

import pytest
import requests
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.purchasing.forms import CancelForm, SendForm
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder
from seizou.purchasing.models import PurchaseOrderLine
from tests import test_purchasing_shipping_notice, test_purchasing_views
from tests.test_purchasing_shipping_notice import _order
from tests.test_purchasing_shipping_notice import _detail, _notice, _notice_cell
from tests.test_purchasing_views import FakePost, _post_data

# 発注の画面の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
# user = 普通の利用者 tester、subcontractor と items は作り物、send_connection は向き「送る」のつながり (鍵は試験ごとに作る)。
user = test_purchasing_views.user
client_logged_in = test_purchasing_views.client_logged_in
subcontractor = test_purchasing_views.subcontractor
items = test_purchasing_views.items
send_connection = test_purchasing_views.send_connection
# 数量 10 の発注の部品 (作り物の軸) と、相手への HTTP を呼んだら落とす requests.post (つながりの無い協力企業の発注は送らない)。
item = test_purchasing_shipping_notice.item
fake_post = test_purchasing_shipping_notice.fake_post


def _notices(response):
    """その要求で足した右上の知らせ (level_tag と字) の並び。"""
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def _detail_url(po):
    return reverse("purchasing:purchaseorder_detail", args=[po.pk])


# ---------------------------------------------------------------- 1 送れなかった発注の知らせと送り直しの後の画面


@pytest.mark.django_db
def test_create_delivered_keeps_saved_message(monkeypatch, client_logged_in, subcontractor, items, send_connection):
    monkeypatch.setattr("requests.post", FakePost())

    response = client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items))

    po = PurchaseOrder.objects.get()
    assert response.status_code == 302
    assert response["Location"] == _detail_url(po)
    # 届いたときは今のまま
    assert _notices(response) == [("success", "保存しました。")]
    assert po.status == PurchaseOrder.STATUS_SENT


@pytest.mark.django_db
def test_create_undelivered_is_yellow_only(monkeypatch, client_logged_in, subcontractor, items, send_connection):
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))

    response = client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items))

    po = PurchaseOrder.objects.get()
    assert response.status_code == 302
    assert response["Location"] == _detail_url(po)
    notices = _notices(response)
    assert len(notices) == 1
    level, text = notices[0]
    assert level == "warning"
    assert text.startswith("注文は相手に届いていません。")
    assert text.endswith("直したら「再送」を押してください。")
    assert "success" not in [level for level, _text in notices]
    assert po.status == PurchaseOrder.STATUS_DRAFT


@pytest.mark.django_db
def test_resend_returns_to_detail(monkeypatch, client_logged_in, subcontractor, items, send_connection):
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))
    created = client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items))
    po = PurchaseOrder.objects.get()
    # 詳細を開いて保存の知らせを読み終える (読んでいない知らせは次の要求に持ち越される)
    client_logged_in.get(created["Location"])
    send_url = reverse("purchasing:purchaseorder_send", args=[po.pk])

    # 届かないまま送り直す: 発注の一覧でなく詳細へ戻り、黄の知らせだけ
    response = client_logged_in.post(send_url)

    assert response.status_code == 302
    assert response["Location"] == _detail_url(po)
    notices = _notices(response)
    assert len(notices) == 1
    assert notices[0][0] == "warning"
    assert notices[0][1].startswith("注文は相手に届いていません。")
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_DRAFT
    client_logged_in.get(response["Location"])

    # 届く形で送り直す: 詳細へ戻り、緑の「相手に送りました。」
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    response = client_logged_in.post(send_url)

    assert response.status_code == 302
    assert response["Location"] == _detail_url(po)
    assert _notices(response) == [("success", "相手に送りました。")]
    assert fake.calls == 1
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_SENT


# ---------------------------------------------------------------- 4 発注の取消と送り直しの釦の字


@pytest.mark.django_db
def test_cancel_and_send_buttons_say_what_they_do(client_logged_in, user, subcontractor, item, fake_post):
    po = _order(subcontractor, item, user, send=False)
    assert po.status == PurchaseOrder.STATUS_DRAFT

    cancel = client_logged_in.get(reverse("purchasing:purchaseorder_cancel", args=[po.pk])).content.decode()

    assert "取り消す</button>" in cancel
    assert "保存</button>" not in cancel

    send = client_logged_in.get(reverse("purchasing:purchaseorder_send", args=[po.pk])).content.decode()

    assert "送り直す</button>" in send
    assert "保存</button>" not in send
    assert SendForm.submit_label == "送り直す"
    assert CancelForm.submit_label == "取り消す"
    assert fake_post.calls == 0


# ---------------------------------------------------------------- 2 受入の残りの数 (出荷の知らせごと)


@pytest.mark.django_db
def test_receipt_link_qty_is_what_is_left(client_logged_in, user, subcontractor, item, fake_post):
    po = _order(subcontractor, item, user)
    line = po.lines.get()
    _notice(line, "4", date(2026, 11, 5), "SH-0001")
    services.receive(line, Decimal("3"), date(2026, 11, 6), user)
    url = reverse("purchasing:receipt_create")

    cell = _notice_cell(_detail(client_logged_in, po))

    # 知らせ 4 のうち 3 を受け入れたので、残りの 1
    assert f'href="{url}?line={line.pk}&amp;qty=1&amp;received_on=2026-11-05"' in cell
    assert "qty=4" not in cell

    _notice(line, "6", date(2026, 11, 7), "SH-0002")
    cell = _notice_cell(_detail(client_logged_in, po))

    assert cell.count("受入を記録する") == 2
    assert f"?line={line.pk}&amp;qty=1&amp;received_on=2026-11-05" in cell
    assert f"?line={line.pk}&amp;qty=6&amp;received_on=2026-11-07" in cell
    # 取り直した明細で呼ぶ (shipping_notice は cached_property で、前に読んだ明細は古い知らせのまま)
    rests = services.notice_rests(PurchaseOrderLine.objects.get(pk=line.pk))
    assert [entry["rest"] for entry in rests] == [Decimal("1"), Decimal("6")]
    assert fake_post.calls == 0


@pytest.mark.django_db
def test_receipt_link_hidden_when_nothing_is_left(client_logged_in, user, subcontractor, item, fake_post):
    po = _order(subcontractor, item, user)
    line = po.lines.get()
    _notice(line, "4", date(2026, 11, 5), "SH-0001")
    services.receive(line, Decimal("4"), date(2026, 11, 6), user)

    cell = _notice_cell(_detail(client_logged_in, po))

    assert "受入を記録する" not in cell
    assert "2026-11-05" in cell and "×4" in cell

    _notice(line, "6", date(2026, 11, 7), "SH-0002")
    body = _detail(client_logged_in, po)
    cell = _notice_cell(body)

    # 受け入れ終えた古い 4 の行には出さず、6 の行にだけ残りの 6
    assert cell.count("受入を記録する") == 1
    assert f"?line={line.pk}&amp;qty=6&amp;received_on=2026-11-07" in cell
    # 明細の pk だけを渡す「受入を登録」は残る (明細の残 6)
    assert f'href="{reverse("purchasing:receipt_create")}?line={line.pk}"' in body
    assert fake_post.calls == 0


# ---------------------------------------------------------------- 3 断られた発注と取消の発注の受入


@pytest.mark.django_db
def test_rejected_order_has_no_receipt_links(client_logged_in, user, subcontractor, item, fake_post):
    po = _order(subcontractor, item, user, send=False)
    line = po.lines.get()
    _notice(line, "4", date(2026, 11, 5), "SH-0001")
    sent = services.mark_sent(po, user)
    entry = f'href="{reverse("purchasing:receipt_create")}?line={line.pk}"'

    body = _detail(client_logged_in, sent)

    assert entry in body
    assert "受入を記録する" in _notice_cell(body)

    rejected = services.mark_rejected(sent, user, reason="作り物の断り")
    assert rejected.status == PurchaseOrder.STATUS_REJECTED
    body = _detail(client_logged_in, rejected)

    # 「受入を登録」も出荷の知らせの「受入を記録する」も出さない (知らせの行の字は残る)
    assert entry not in body
    assert "受入を記録する" not in body
    assert "2026-11-05" in _notice_cell(body)
    assert fake_post.calls == 0


@pytest.mark.django_db
def test_cancelled_order_has_no_remaining_in_list(client_logged_in, user, subcontractor, item, fake_post):
    kept = _order(subcontractor, item, user)
    dropped = _order(subcontractor, item, user)
    for po in (kept, dropped):
        services.receive(po.lines.get(), Decimal("3"), date(2026, 11, 6), user)
    assert services.cancel_purchase_order(dropped, user).status == PurchaseOrder.STATUS_CANCELLED

    orders = {po.pk: po for po in services.annotate_order_received(PurchaseOrder.objects.all())}

    assert Decimal(str(orders[kept.pk].receive_remaining)) == Decimal("7")
    assert Decimal(str(orders[dropped.pk].receive_remaining)) == Decimal("0")
    # 受入の数は取り消しても今のまま
    assert Decimal(str(orders[kept.pk].received_total)) == Decimal("3")
    assert Decimal(str(orders[dropped.pk].received_total)) == Decimal("3")

    response = client_logged_in.get(reverse("purchasing:purchaseorder_list"))
    labels = [col.label for col in response.context["columns"]]
    remaining = labels.index("受入の残")
    values = {row["obj"].pk: Decimal(str(row["cells"][remaining]["value"])) for row in response.context["rows"]}

    assert values == {kept.pk: Decimal("7"), dropped.pk: Decimal("0")}
    assert fake_post.calls == 0
