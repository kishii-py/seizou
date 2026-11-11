"""受け入れの画面の 4 つの答え (受け入れる・条件を直して受け入れる・保留する・断る) のテスト (候補 C97)。

1 つの DB に、注文を送る会社と受ける会社の両方の役を置く (tests/test_connect_reply.py と同じ形。つながりと偽の requests.post もそこから使う)。
受ける側は画面 (受け入れ・条件を直して受け入れる・保留する・断る) から答え、相手の受け口が発注に当てるところまでを見る。
相手への HTTP は requests.post を偽の物に差し替え、受けた本文を services.receive に渡す (外へは出ない)。
鍵はテストのたびに作り、環境変数に置く。取引先・品目・会社は作り物。
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services
from seizou.connect.models import Inbox
from seizou.core.views import refusal_head
from seizou.masters import services as masters_services
from seizou.orders.models import Order
from seizou.purchasing.models import PurchaseOrder
from tests.test_connect_inbox_intake import intake_row
from tests.test_connect_reply import _connect, _into_receive
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret
from tests.test_core_access import account
from tests.test_purchasing_reply_types import ORDER_ENV, REPLY_ENV, _send_order

#: 金額の閲覧だけを効かせる (部署と書く権限の判定はしない。tests/test_billing_invoice_lines_tab.py と同じ)
MONEY_ON = {"department": "off", "write": "off", "money": "on"}
#: 書く権限だけを効かせる (受注の権限の無い人は受け入れられない)
WRITE_ON = {"department": "off", "write": "on", "money": "off"}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_superuser("tester", password="pass-for-tests-only")


@pytest.fixture
def secrets(monkeypatch):
    monkeypatch.setenv(ORDER_ENV, _new_secret())
    monkeypatch.setenv(REPLY_ENV, _new_secret())


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の発注元", "role": "customer"}, user)


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user),
    ]


@pytest.fixture
def own_items(user):
    return [
        masters_services.create_item({"part_no": "B-100", "name": "作り物の受ける側の軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "B-200", "name": "作り物の受ける側の板", "unit": "枚"}, user),
    ]


@pytest.fixture
def fake(monkeypatch, user, subcontractor, customer, secrets):
    """2 社の役のつながりを置き、相手への HTTP を受け口に回す偽の requests.post。"""
    _connect(user, subcontractor, customer)
    post = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", post)
    return post


@pytest.fixture
def sent(fake, user, subcontractor, items):
    """A 社の役が送った明細 2 行の発注と、B 社の役の受信した注文。"""
    po = _send_order(subcontractor, items, user)
    return po, Inbox.objects.get()


def _url(name, inbox):
    return reverse(f"connect:{name}", args=[inbox.pk])


def _messages(response):
    return [str(message) for message in response.context["messages"]]


def _sent_json(fake):
    return json.loads(fake.calls[-1]["data"].decode("utf-8"))


def _conditional_data(own_items, **values):
    """条件を直して受け入れるの POST の値。初めは相手の注文の値のまま (values で欄を替える)。"""
    data = {
        "item_1": own_items[0].pk,
        "item_2": own_items[1].pk,
        "line_1_unit_price": "25.5",
        "line_1_qty": "10",
        "line_1_due_date": "2026-11-20",
        "line_2_unit_price": "100",
        "line_2_qty": "5",
        "line_2_due_date": "2026-11-27",
    }
    data.update(values)
    return data


# --- (a) 受け入れの画面のほかの答え方 ---


@pytest.mark.django_db
def test_accept_screen_shows_other_answers(client, user, sent, customer):
    _po, inbox = sent
    client.force_login(user)

    body = client.get(_url("inbox_accept", inbox)).content.decode()
    assert "ほかの答え方" in body
    for name, label in (
        ("inbox_accept_conditional", "条件を直して受け入れる"),
        ("inbox_hold", "保留する"),
        ("inbox_reject", "断る"),
    ):
        assert f'href="{_url(name, inbox)}"' in body
        assert label in body

    # 読み取りの行は「断る」だけと、その訳
    intake = intake_row(customer, user)
    body = client.get(_url("inbox_accept", intake)).content.decode()
    assert f'href="{_url("inbox_reject", intake)}"' in body
    assert _url("inbox_accept_conditional", intake) not in body
    assert _url("inbox_hold", intake) not in body
    assert "注文書の読み取りから入った注文は、相手に答えを送れないので" in body


@pytest.mark.django_db
def test_held_row_has_no_hold_link_and_accepted_row_has_no_other_answers(client, user, sent, own_items):
    _po, inbox = sent
    client.force_login(user)
    services.hold(inbox, user)

    body = client.get(_url("inbox_accept", inbox)).content.decode()
    assert _url("inbox_hold", inbox) not in body
    assert f'href="{_url("inbox_accept_conditional", inbox)}"' in body

    services.accept(inbox, {"T-100": own_items[0], "T-200": own_items[1]}, user)
    body = client.get(_url("inbox_accept", inbox)).content.decode()
    assert "ほかの答え方" not in body


# --- (b) (c) 条件を直して受け入れる ---


@pytest.mark.django_db
def test_conditional_post_creates_order_with_answers_and_replies_ca(client, user, sent, fake, own_items):
    po, inbox = sent
    client.force_login(user)

    response = client.post(
        _url("inbox_accept_conditional", inbox),
        _conditional_data(own_items, line_1_unit_price="24", line_1_due_date="2026-11-25", line_2_due_date=""),
        follow=True,
    )

    assert response.status_code == 200
    # 済んだ知らせ (受注を作った知らせの 1 文 = orders.services.create_order の saved_note が後ろに付く)
    assert _messages(response)[0].startswith(services.MESSAGE_CONDITIONAL_SAVED)
    order = Order.objects.get()
    first, second = order.lines.order_by("line_no")
    assert (first.unit_price, first.qty, first.due_date) == (Decimal("24"), Decimal("10"), date(2026, 11, 25))
    assert (second.unit_price, second.qty, second.due_date) == (Decimal("100"), Decimal("5"), date(2026, 11, 27))
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_ACCEPTED
    assert inbox.reply_type == "CA"
    assert inbox.reply_lines == [
        {"line_no": 1, "reply_type": "CA", "reply_unit_price": "24", "reply_qty": "10", "reply_due_date": "2026-11-25"},
        {"line_no": 2, "reply_type": "AP"},
    ]
    sent_json = _sent_json(fake)
    assert sent_json["reply_type"] == "CA"
    assert sent_json["lines"] == inbox.reply_lines
    assert sent_json["supplier_order_no"] == order.order_no
    # 送る明細の答えは受諾と条件付受諾だけ (明細の受託不可 RE を選ぶ欄は無い)・分けて納める鍵は無い
    assert {row["reply_type"] for row in sent_json["lines"]} <= {"AP", "CA"}
    assert all(set(row) <= {"line_no", "reply_type", "reply_unit_price", "reply_qty", "reply_due_date"} for row in sent_json["lines"])

    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_CONDITIONAL
    assert po.lines.get(line_no=1).reply_unit_price == Decimal("24")

    detail = client.get(_url("inbox_detail", inbox)).content.decode()
    assert "条件付" in detail
    assert "返した条件" in detail
    assert "1 行目 T-100: 単価 ¥24 (注文 ¥25.5)・数量 10・納期 2026-11-25 (注文 2026-11-20)" in detail
    rows = client.get(reverse("connect:inbox_list")).content.decode()
    assert '<span class="badge badge-soft badge-warning">条件付</span>' in rows


@pytest.mark.django_db
def test_conditional_post_without_changes_replies_ap(client, user, sent, fake, own_items):
    po, inbox = sent
    client.force_login(user)

    response = client.post(_url("inbox_accept_conditional", inbox), _conditional_data(own_items), follow=True)

    assert _messages(response)[0].startswith(services.MESSAGE_CONDITIONAL_UNCHANGED)
    assert services.MESSAGE_CONDITIONAL_UNCHANGED == "どの値も直していないので、受け入れるとして相手に返しました。"
    inbox.refresh_from_db()
    assert inbox.reply_type == "AP"
    assert inbox.reply_lines == []
    assert _sent_json(fake)["reply_type"] == "AP"
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ACCEPTED


# --- (d) 保留 ---


@pytest.mark.django_db
def test_hold_post_then_accept(client, user, sent, fake, own_items):
    po, inbox = sent
    client.force_login(user)

    response = client.post(_url("inbox_hold", inbox), {}, follow=True)

    assert response.redirect_chain[-1][0] == _url("inbox_detail", inbox)
    assert services.MESSAGE_HOLD_SAVED in _messages(response)
    assert Order.objects.count() == 0
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_RECEIVED
    assert inbox.reply_type == "AJ"
    assert inbox.reply_sent_at is not None
    held_id = inbox.reply_message_id
    assert _sent_json(fake)["reply_type"] == "AJ"
    assert _sent_json(fake)["lines"] == []
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ON_HOLD

    assert '<span class="badge badge-soft badge-warning">保留した</span>' in client.get(reverse("connect:inbox_list")).content.decode()
    detail = response.content.decode()
    assert "保留した" in detail
    assert "送った " in detail

    client.post(_url("inbox_accept", inbox), {"item_1": own_items[0].pk, "item_2": own_items[1].pk})

    assert Order.objects.count() == 1
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_ACCEPTED
    assert inbox.reply_type == "AP"
    assert inbox.reply_message_id != held_id
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ACCEPTED


@pytest.mark.django_db
def test_hold_then_reject(client, user, fake, subcontractor, items):
    po = _send_order(subcontractor, items, user)
    inbox = Inbox.objects.get()
    client.force_login(user)

    client.post(_url("inbox_hold", inbox), {})
    client.post(_url("inbox_reject", inbox), {"reason": "作り物の理由"})

    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_REJECTED
    assert inbox.reply_type == "RE"
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_REJECTED
    assert po.reply_note == "作り物の理由"


@pytest.mark.django_db
def test_hold_refuses_answered_row(user, sent, own_items):
    _po, inbox = sent
    services.accept(inbox, {"T-100": own_items[0], "T-200": own_items[1]}, user)

    with pytest.raises(ValueError, match="^保留できません。この注文のデータは「受け入れた」です。"):
        services.hold(inbox, user)


# --- (e) 読み取りの行 ---


@pytest.mark.django_db
def test_intake_row_has_no_conditional_or_hold_screen(client, user, customer):
    intake = intake_row(customer, user)
    client.force_login(user)

    for name in ("inbox_accept_conditional", "inbox_hold"):
        assert client.get(_url(name, intake)).status_code == 404
        assert client.post(_url(name, intake), {}).status_code == 404

    assert Order.objects.count() == 0
    intake.refresh_from_db()
    assert intake.reply_type == ""


# --- (f) 金額の閲覧の無い人 ---


@pytest.mark.django_db
def test_conditional_without_money_is_refused(client, settings, sent, own_items):
    """金額の閲覧の無い人は、受け入れ (条件付を含む。受注を作る操作 = 金額に関わる操作) をしない。入口も出さない (決め 11-07 21:0x)。

    受け入れの画面 (直に開いた物) は回答単価の欄を置かないまま (form の hide_money)。保存は金額の門が断り、受注はできない。
    """
    _po, inbox = sent
    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    client.force_login(account("jimu"))

    assert "条件を直して受け入れる" not in client.get(_url("inbox_accept", inbox)).content.decode()
    assert "受け入れる</a>" not in client.get(_url("inbox_detail", inbox)).content.decode()
    body = client.get(_url("inbox_accept_conditional", inbox)).content.decode()
    assert "line_1_unit_price" not in body
    assert "line_1_qty" in body

    data = _conditional_data(own_items, line_1_qty="8")
    for key in ("line_1_unit_price", "line_2_unit_price"):
        data.pop(key)
    response = client.post(_url("inbox_accept_conditional", inbox), data)

    assert response.status_code == 200
    html = response.content.decode()
    assert 'data-notice="stop"' in html
    assert "受信した注文は金額が主な内容のため、金額の閲覧の権限が無い方は受け入れられません。" in html
    assert Order.objects.count() == 0

    # 金額の閲覧のある人は、回答単価の欄が出て、同じ注文を条件付で受け入れられる。
    client.force_login(account("keiri", see_money=True))
    assert "line_1_unit_price" in client.get(_url("inbox_accept_conditional", inbox)).content.decode()
    client.post(_url("inbox_accept_conditional", inbox), _conditional_data(own_items, line_1_qty="8"))
    line = Order.objects.get().lines.get(line_no=1)
    assert (line.unit_price, line.qty) == (Decimal("25.5"), Decimal("8"))


# --- (g) 権限 ---


@pytest.mark.django_db
def test_conditional_and_hold_need_orders_permission(client, settings, sent, own_items):
    _po, inbox = sent
    settings.SEIZOU = {"access_control": dict(WRITE_ON)}
    client.force_login(account("plain-clerk"))

    # 権限で断った物は、入力の画面に止まった知らせ (土台の data-notice="stop") で出る (tests/test_core_access_views.py と同じ形)
    for url, data in ((_url("inbox_accept_conditional", inbox), _conditional_data(own_items)), (_url("inbox_hold", inbox), {})):
        response = client.post(url, data)
        assert response.status_code == 200
        html = response.content.decode()
        assert 'data-notice="stop"' in html
        assert "受注の編集の権限がありません。" in html

    assert Order.objects.count() == 0
    inbox.refresh_from_db()
    assert inbox.reply_type == ""


# --- (h) 欄の検査で止まった時 ---


@pytest.mark.django_db
def test_blank_qty_stops_with_accept_head(client, user, sent, own_items):
    _po, inbox = sent
    client.force_login(user)

    response = client.post(_url("inbox_accept_conditional", inbox), _conditional_data(own_items, line_1_qty=""))

    assert response.status_code == 200
    message = response.context["notice"].message
    assert message.startswith("受け入れられません。")
    assert "1 行目 T-100 の回答数量" in message
    assert Order.objects.count() == 0
    assert refusal_head("hold", ()) == "保留できません。"
    assert refusal_head("accept_with_conditions", ()) == "受け入れられません。"


# --- (i) 送り直し ---


@pytest.mark.django_db
def test_hold_without_reply_connection_shows_resend(client, monkeypatch, user, subcontractor, customer, items, secrets):
    _connect(user, subcontractor, customer, reply_back=False)
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    _send_order(subcontractor, items, user)
    inbox = Inbox.objects.get()
    client.force_login(user)

    client.post(_url("inbox_hold", inbox), {})

    inbox.refresh_from_db()
    assert inbox.reply_type == "AJ"
    assert inbox.reply_sent_at is None
    assert _url("inbox_reply_resend", inbox) in client.get(_url("inbox_detail", inbox)).content.decode()
