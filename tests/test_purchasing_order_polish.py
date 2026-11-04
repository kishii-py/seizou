"""発注の詳細の磨き (精査 A1 の不具合 10・想定外 S2・S6・S9、精査 A3 の不具合 5)。

- 出荷の知らせの合計は、行と同じく末尾の 0 を落とした字。
- 送れなかった「作成」の発注は、詳細の頭に黄の知らせで理由を出す (送った記録の最後の返事)。
- 出荷の知らせの「受入を記録する」は、その明細の知らせの合計が受入の合計より多い間だけ出す。
- 断られた発注は取り消せる (送り直すは「作成」だけ)。送った・相手が受け入れた発注は取り消せない。
- 取り消せない発注の文は、相手への連絡を促す (「会期後」の字を画面に出さない)。
Django の test client で HTML を見る。取引先と品目は作り物。相手への HTTP は requests.post を偽の物に差し替える。
"""

from datetime import date
from decimal import Decimal

import pytest
import requests
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder
from tests.test_connect_signing import _new_secret
from tests.test_purchasing_shipping_notice import FakePost as NoHttpPost
from tests.test_purchasing_shipping_notice import _detail, _lines_table, _notice, _notice_cell, _order
from tests.test_purchasing_views import ENV_NAME, PARTNER_URL, FakePost, FakeResponse, _post_data

NOT_DELIVERED = "注文は相手に届いていません。"
YELLOW = "alert alert-warning alert-soft"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def items(user, item):
    return [item, masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)]


@pytest.fixture
def send_connection(monkeypatch, subcontractor, user):
    monkeypatch.setenv(ENV_NAME, _new_secret())
    return connect_services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)


def _create(client, monkeypatch, partner, items, fake):
    """発注の新規登録の画面から保存し、移った先 (発注の詳細) の本文を返す。"""
    monkeypatch.setattr("requests.post", fake)
    response = client.post(reverse("purchasing:purchaseorder_create"), _post_data(partner, items), follow=True)
    assert response.status_code == 200
    return response.content.decode()


def _ops_cell(body):
    heads, cells = _lines_table(body)
    return cells[heads.index("操作")]


@pytest.fixture
def no_http(monkeypatch):
    """つながりの無い協力企業への発注で、HTTP を呼んだら落とす。"""
    fake = NoHttpPost()
    monkeypatch.setattr("requests.post", fake)
    return fake


@pytest.mark.django_db
def test_notice_total_drops_trailing_zeros(client_logged_in, user, subcontractor, item, no_http):
    whole = _order(subcontractor, item, user)
    _notice(whole.lines.get(), "4.00", date(2026, 11, 5), "SH-0001")
    _notice(whole.lines.get(), "6.00", date(2026, 11, 6), "SH-0002")
    half = _order(subcontractor, item, user)
    _notice(half.lines.get(), "2.50", date(2026, 11, 5), "SH-0003")
    _notice(half.lines.get(), "4", date(2026, 11, 6), "SH-0004")

    whole_cell = _notice_cell(_detail(client_logged_in, whole))
    half_cell = _notice_cell(_detail(client_logged_in, half))

    assert "合計 10<" in whole_cell
    assert "合計 10.00" not in whole_cell
    assert "合計 6.5<" in half_cell
    assert "合計 6.50" not in half_cell
    assert no_http.calls == 0


@pytest.mark.django_db
def test_create_unreachable_shows_yellow_notice(monkeypatch, client_logged_in, subcontractor, items, send_connection):
    body = _create(client_logged_in, monkeypatch, subcontractor, items, FakePost(raises=requests.ConnectionError()))

    assert PurchaseOrder.objects.get().status == PurchaseOrder.STATUS_DRAFT
    assert YELLOW in body
    assert NOT_DELIVERED in body
    assert "届きません: 相手につながりません。" in body


@pytest.mark.django_db
def test_create_refused_shows_yellow_notice(monkeypatch, client_logged_in, subcontractor, items, send_connection):
    refused = FakePost(reply=FakeResponse(400, {"status": "rejected", "message": "作り物の断る理由"}))

    body = _create(client_logged_in, monkeypatch, subcontractor, items, refused)

    assert YELLOW in body
    assert NOT_DELIVERED in body
    assert "断られた: 作り物の断る理由" in body


@pytest.mark.django_db
def test_sent_or_unconnected_order_has_no_yellow_notice(
    monkeypatch, client_logged_in, user, subcontractor, items, send_connection
):
    body = _create(client_logged_in, monkeypatch, subcontractor, items, FakePost())

    assert PurchaseOrder.objects.get().status == PurchaseOrder.STATUS_SENT
    assert NOT_DELIVERED not in body

    # つながりの無い協力企業への発注 (送った記録が 0 行) にも出ない
    other = masters_services.create_partner(
        {"code": "S002", "name": "作り物の別の協力企業", "role": "subcontractor"}, user
    )
    fake = FakePost()
    body = _create(client_logged_in, monkeypatch, other, items, fake)

    assert fake.calls == 0
    assert NOT_DELIVERED not in body


@pytest.mark.django_db
def test_resend_clears_yellow_notice(monkeypatch, client_logged_in, user, subcontractor, items, send_connection):
    _create(client_logged_in, monkeypatch, subcontractor, items, FakePost(raises=requests.ConnectionError()))
    po = PurchaseOrder.objects.get()
    monkeypatch.setattr("requests.post", FakePost())

    connect_services.send_order(po, user)

    body = _detail(client_logged_in, po)
    assert NOT_DELIVERED not in body
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_SENT


def _order_of_50(partner, item, user):
    return services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal("50"), "unit_price": Decimal("25"), "due_date": date(2026, 11, 20)}],
        user,
        order_date=date(2026, 11, 2),
    )


@pytest.mark.django_db
def test_receipt_link_follows_notice_total(client_logged_in, user, subcontractor, item, no_http):
    po = _order_of_50(subcontractor, item, user)
    line = po.lines.get()
    _notice(line, "30", date(2026, 11, 5), "SH-0001")
    services.receive(line, Decimal("28"), date(2026, 11, 6), user)

    # 知らせ 30 > 受入 28 の間は出る
    assert "受入を記録する" in _notice_cell(_detail(client_logged_in, po))

    services.receive(line, Decimal("2"), date(2026, 11, 7), user)
    body = _detail(client_logged_in, po)

    # 知らせの分を受け入れたら消える。明細の pk だけを渡す「受入を登録」は残る (受入の残 20)
    assert "受入を記録する" not in _notice_cell(body)
    assert "受入を登録" in _ops_cell(body)

    other = _order_of_50(subcontractor, item, user)
    other_line = other.lines.get()
    _notice(other_line, "40", date(2026, 11, 5), "SH-0002")
    _notice(other_line, "60", date(2026, 11, 6), "SH-0003")
    services.receive(other_line, Decimal("40"), date(2026, 11, 7), user)

    # 受入 40 は古い知らせ 40 に当たるので、まだ受け入れていない 60 の行にだけ出る (残りは知らせごと。決め 11-04 13:5x)
    assert _notice_cell(_detail(client_logged_in, other)).count("受入を記録する") == 1
    assert no_http.calls == 0


@pytest.mark.django_db
def test_rejected_order_can_be_cancelled_on_screen(client_logged_in, user, subcontractor, item, no_http):
    po = _order(subcontractor, item, user)
    rejected = services.mark_rejected(services.mark_sent(po, user), user, reason="作り物の断りの理由")
    cancel_url = reverse("purchasing:purchaseorder_cancel", args=[po.pk])

    body = _detail(client_logged_in, rejected)

    assert cancel_url in body
    assert reverse("purchasing:purchaseorder_send", args=[po.pk]) not in body

    response = client_logged_in.post(cancel_url, {"reason": "別の協力企業へ出し直す"})

    assert response.status_code == 302
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_CANCELLED


@pytest.mark.django_db
def test_accepted_or_sent_order_is_not_cancellable(client_logged_in, user, subcontractor, item, no_http):
    sent = services.mark_sent(_order(subcontractor, item, user), user)
    accepted = services.mark_accepted(services.mark_sent(_order(subcontractor, item, user), user), user)

    for po in (sent, accepted):
        assert reverse("purchasing:purchaseorder_cancel", args=[po.pk]) not in _detail(client_logged_in, po)
        with pytest.raises(ValueError, match="「作成」か「断られた」の発注だけ"):
            services.cancel_purchase_order(po, user)
        assert PurchaseOrder.objects.get(pk=po.pk).status == po.status


@pytest.mark.django_db
def test_cancel_message_has_no_event_words(client_logged_in, user, subcontractor, item, no_http):
    sent = services.mark_sent(_order(subcontractor, item, user), user)

    response = client_logged_in.post(reverse("purchasing:purchaseorder_cancel", args=[sent.pk]), {"reason": ""})

    body = response.content.decode()
    assert "相手に連絡してください" in body
    assert "「作成」か「断られた」の発注だけ" in body
    assert "会期後" not in body
    assert PurchaseOrder.objects.get(pk=sent.pk).status == PurchaseOrder.STATUS_SENT
