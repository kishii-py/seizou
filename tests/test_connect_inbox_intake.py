"""届いた注文の入口 (直接 / 読み取り)・差出人の確認待ち・差出人を選ぶ・読み取りの行の承認のテスト。行は record_inbox で作る (見張りの命令と API を通さない)。会社名と品番は作り物。

読み取りの行の中身は tests/fixtures/intake/order_reply.json の形 (2 行目の単価が null)。直接の行は
tests/test_r10_inbox_accept.py の _payload と _line の形。requests.post は「呼んだら落とす」偽の物に差し替える
(読み取りの行は相手へ何も送らない)。鍵はテストのたびに作り、環境変数に置く。
"""

import re
import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.connect import services
from seizou.connect.models import Inbox
from seizou.core import access
from seizou.core.exceptions import RuleViolation
from seizou.masters.services import create_item, create_partner
from seizou.orders.models import Order
from seizou.shipping import services as shipping_services
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret
from tests.test_core_access import account
from tests.test_intake_extract import reply_data
from tests.test_r10_inbox_accept import _line, _payload

ENV_NAME = "SEIZOU_PARTNER_INTAKECO_SECRET"
SENDER = "知らない作り物の会社"


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    yield fake
    assert fake.calls == []


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return create_partner({"code": "CI01", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def other_customer(user):
    return create_partner({"code": "CI03", "name": "作り物の別の得意先", "role": "both"}, user)


@pytest.fixture
def subcontractor(user):
    return create_partner({"code": "CI02", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def items(user):
    shaft = create_item({"part_no": "X-100", "name": "作り物のシャフト", "unit": "個", "standard_price": Decimal("130")}, user)
    bracket = create_item({"part_no": "X-200", "name": "作り物のブラケット", "unit": "個", "standard_price": Decimal("240")}, user)
    return {"X-100": shaft, "X-200": bracket}


def intake_payload(**changes):
    """読み取りの行の中身 (読み取りの JSON の形。2 行目の単価が null)。"""
    data = reply_data()
    data.update(changes)
    return data


def intake_row(partner, user, **changes):
    return services.record_inbox(
        partner, intake_payload(**changes), user, source=Inbox.SOURCE_INTAKE, sender_name=SENDER
    )


def direct_row(partner, user):
    msg_id = str(uuid.uuid4())
    return services.record_inbox(partner, _payload(msg_id, [_line(1, "X-100")]), user)


def test_record_inbox_source_and_partner_rules(customer, user):
    direct = direct_row(customer, user)
    assert direct.source == Inbox.SOURCE_DIRECT
    assert direct.accepted_at is None
    assert direct.extraction is None
    assert direct.sender_name == ""

    with pytest.raises(ValueError, match="取引先がありません"):
        services.record_inbox(None, intake_payload(), user)
    with pytest.raises(ValueError, match="入口 'mail' は使えません"):
        services.record_inbox(customer, intake_payload(), user, source="mail")

    long_name = "作" * 120
    waiting = services.record_inbox(
        None, intake_payload(), user, source=Inbox.SOURCE_INTAKE, sender_name=f"  {long_name} "
    )
    waiting.refresh_from_db()
    assert waiting.partner is None
    assert waiting.source == Inbox.SOURCE_INTAKE
    assert waiting.sender_name == "作" * 100


def test_accept_intake_row_makes_intake_order(customer, items, user):
    row = intake_row(customer, user, order_date=None)

    order = services.accept(row, items, user)

    assert order.source == Order.SOURCE_INTAKE
    assert order.customer == customer
    assert order.order_date == timezone.localtime(row.received_at).date()
    lines = list(order.lines.order_by("line_no"))
    assert lines[0].unit_price == Decimal("120")
    assert lines[1].unit_price == Decimal("240")  # 単価 null の明細は標準単価
    row.refresh_from_db()
    assert row.status == Inbox.STATUS_ACCEPTED
    assert row.accepted_at is not None
    assert row.reply_response == services.MESSAGE_REPLY_INTAKE
    assert row.reply_sent_at is None
    with pytest.raises(RuleViolation) as excinfo:
        services.accept(row, items, user)
    assert excinfo.value.code == "R11"


def test_accept_intake_row_without_price_is_refused(customer, user):
    shaft = create_item({"part_no": "X-100", "name": "作り物のシャフト", "unit": "個", "standard_price": Decimal("130")}, user)
    bracket = create_item({"part_no": "X-200", "name": "作り物のブラケット", "unit": "個"}, user)
    row = intake_row(customer, user)

    with pytest.raises(ValueError, match="標準単価"):
        services.accept(row, {"X-100": shaft, "X-200": bracket}, user)

    assert Order.objects.count() == 0
    row.refresh_from_db()
    assert row.status == Inbox.STATUS_RECEIVED
    assert row.accepted_at is None


def test_waiting_row_needs_sender_before_accept(customer, items, user):
    row = intake_row(None, user)

    with pytest.raises(ValueError, match=services.MESSAGE_NO_SENDER):
        services.accept(row, items, user)
    assert Order.objects.count() == 0

    chosen = services.choose_sender(row, customer, user)
    assert chosen.partner == customer
    order = services.accept(row, items, user)
    assert order.customer == customer
    assert order.source == Order.SOURCE_INTAKE


def test_choose_sender_rules(customer, other_customer, subcontractor, items, user):
    direct = direct_row(customer, user)
    with pytest.raises(ValueError, match=r"署名で決まる"):
        services.choose_sender(direct, other_customer, user)
    direct.refresh_from_db()
    assert direct.partner == customer

    waiting = intake_row(None, user)
    with pytest.raises(ValueError, match="得意先ではありません"):
        services.choose_sender(waiting, subcontractor, user)
    with pytest.raises(ValueError, match="差出人の取引先を選んでください"):
        services.choose_sender(waiting, None, user)
    waiting.refresh_from_db()
    assert waiting.partner is None

    matched = intake_row(customer, user, order_no="TEST-PO-002")
    services.choose_sender(matched, other_customer, user)  # 当て違いの付け直し
    matched.refresh_from_db()
    assert matched.partner == other_customer

    services.accept(matched, items, user)
    with pytest.raises(ValueError, match="「届いた」の注文だけ"):
        services.choose_sender(matched, customer, user)
    matched.refresh_from_db()
    assert matched.partner == other_customer


def test_direct_accept_keeps_inbox_source_and_sets_accepted_at(customer, items, user):
    row = direct_row(customer, user)

    order = services.accept(row, items, user, send=False)

    assert order.source == Order.SOURCE_INBOX
    row.refresh_from_db()
    assert row.source == Inbox.SOURCE_DIRECT
    assert row.accepted_at is not None
    assert row.reply_response == services.MESSAGE_REPLY_SEED


def test_no_reply_and_no_shipping_notice_for_intake_row(monkeypatch, customer, items, user):
    # 送る向きのつながりと鍵があっても、読み取りの行は相手へ何も送らない (送れば no_http が落とす)。
    monkeypatch.setenv(ENV_NAME, _new_secret())
    services.register_connection(customer, "http://127.0.0.1:9/connect/receive/order/", ENV_NAME, "send", user)

    rejected = intake_row(customer, user)
    services.reject(rejected, "作り物の理由で断る", user)
    rejected.refresh_from_db()
    assert rejected.reply_response == services.MESSAGE_REPLY_INTAKE
    assert rejected.reply_sent_at is None
    assert rejected.reply_state() == services.MESSAGE_REPLY_INTAKE
    assert not rejected.reply_state().startswith("届いていない")

    accepted = intake_row(customer, user, order_no="TEST-PO-003")
    order = services.accept(accepted, items, user)
    shipment = shipping_services.ship(order.lines.get(line_no=1), Decimal("4"), timezone.localdate(), user)
    shipment.refresh_from_db()
    assert shipment.notice_response == ""  # 入口が読み取りの受注は、出荷の時に送る口を呼ばない

    again = services.send_shipping_notice(shipment, user)
    assert again.notice_response == services.MESSAGE_SHIPPING_NOT_EDI_ORDER
    assert again.notice_sent_at is None


# ここから届いた注文の詳細 (差出人の確認待ちの印・差出人を選ぶ form と口・送り直すを出さない・明細の欄)。


def _messages(response):
    return [(message.level_tag, str(message)) for message in response.context["messages"]]


def _detail(client, inbox):
    response = client.get(reverse("connect:inbox_detail", args=[inbox.pk]))
    assert response.status_code == 200
    return response.content.decode()


def test_detail_shows_waiting_badge_and_sender_form(client, customer, subcontractor, user):
    waiting = intake_row(None, user)
    direct = direct_row(customer, user)
    client.force_login(user)

    body = _detail(client, waiting)
    assert "差出人の確認待ち" in body
    assert SENDER in body
    assert re.search(r"入口</dt>\s*<dd[^>]*>読み取り", body)
    assert f'action="{reverse("connect:inbox_sender", args=[waiting.pk])}"' in body
    assert f'value="{customer.pk}"' in body
    assert "作り物の協力企業" not in body  # 候補は得意先と両方だけ
    assert reverse("connect:inbox_accept", args=[waiting.pk]) not in body
    assert reverse("connect:inbox_reject", args=[waiting.pk]) in body

    body = _detail(client, direct)
    assert reverse("connect:inbox_sender", args=[direct.pk]) not in body
    assert re.search(r"入口</dt>\s*<dd[^>]*>直接", body)
    assert "読み取った差出人" not in body
    assert reverse("connect:inbox_accept", args=[direct.pk]) in body


def test_sender_post_sets_partner(client, customer, subcontractor, user):
    waiting = intake_row(None, user)
    url = reverse("connect:inbox_sender", args=[waiting.pk])
    detail_url = reverse("connect:inbox_detail", args=[waiting.pk])
    client.force_login(user)
    assert client.get(url).status_code == 405

    response = client.post(url, {"partner": subcontractor.pk}, follow=True)
    assert response.redirect_chain == [(detail_url, 302)]
    assert ("error", "選んだ取引先が候補にありません。選び直してください。") in _messages(response)
    response = client.post(url, {"partner": ""}, follow=True)
    assert ("error", "差出人の取引先を選んでください。") in _messages(response)
    waiting.refresh_from_db()
    assert waiting.partner is None

    response = client.post(url, {"partner": customer.pk}, follow=True)
    assert response.redirect_chain == [(detail_url, 302)]
    assert ("success", "差出人を 作り物の得意先 にしました。「承認」で受注にできます。") in _messages(response)
    waiting.refresh_from_db()
    assert waiting.partner == customer
    assert reverse("connect:inbox_accept", args=[waiting.pk]) in response.content.decode()


def test_sender_form_follows_write_permission(settings, client, customer, user):
    waiting = intake_row(None, user)
    url = reverse("connect:inbox_sender", args=[waiting.pk])
    settings.SEIZOU = {"access_control": {"department": "log", "write": "on", "money": "log"}}

    # 受注の権限の無いアカウントには form を出さず、POST しても変えない
    client.force_login(account("plain-clerk"))
    body = _detail(client, waiting)
    assert "差出人の確認待ち" in body
    assert url not in body
    response = client.post(url, {"partner": customer.pk}, follow=True)
    assert ("error", access.write_message("orders")) in _messages(response)
    waiting.refresh_from_db()
    assert waiting.partner is None

    # システム管理者には出る
    client.force_login(get_user_model().objects.create_superuser("admin-tester", password="pass-for-tests-only"))
    assert url in _detail(client, waiting)


def test_intake_row_hides_resend_and_shows_lines(client, customer, user):
    row = intake_row(customer, user)
    services.reject(row, "作り物の理由で断る", user)
    client.force_login(user)

    body = _detail(client, row)

    assert reverse("connect:inbox_reply_resend", args=[row.pk]) not in body
    assert services.MESSAGE_REPLY_INTAKE in body
    lines = body.split("明細 (相手の品番のまま)", 1)[1]
    assert "X-200" in lines
    assert "None" not in lines
