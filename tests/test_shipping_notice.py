"""出荷の知らせの列と、出荷の知らせを送る側の services (shipping)。EDI の 3 段目。

1 段目 = services と出荷の列。2 段目 = 出荷の詳細の「出荷の知らせ」の節と「送り直す」の口 (Django の test client)。
手入力の受注の出荷は no_http で止める (HTTP を呼ばないことを見る)。受信した注文で作った受注は、入口を「受信した注文」にした受注に
受信した注文の行を 1 つ置いて作る (受け口を通さない近道。試験だけ)。相手への送信は偽の requests.post に差し替える。
鍵はテストのたびに作り、環境変数に置く。取引先と品目は作り物。日は固定の値を渡す。
"""

import json
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import requests
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.core import access
from seizou.core.views import hidden_detail_fields
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.shipping import services
from seizou.shipping.models import Shipment
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ENV_NAME = "SEIZOU_PARTNER_TESTBUYER_SECRET"
BUYER_URL = "https://buyer.example.test/connect/receive/order/"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def no_http(monkeypatch):
    """HTTP を呼んだら落とす。手入力の受注の出荷は出荷の知らせを送らないことを見る。"""

    def fail(*args, **kwargs):
        raise AssertionError("受信した注文で作った受注ではないのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


def _order(customer, item, user, *, source="manual"):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
        source=source,
    )


def _inbox_order(customer, item, user):
    """受信した注文で作った受注 (入口 = 受信した注文。受け入れた受信した注文の行を 1 つ置く)。"""
    order = _order(customer, item, user, source="inbox")
    Inbox.objects.create(
        message_id=uuid.uuid4(),
        partner=customer,
        kind="order",
        payload={"order_no": "PO-0001"},
        status="accepted",
        order=order,
    )
    return order


@pytest.fixture
def order(customer, item, user):
    """手入力の受注 (入口は既定の手入力)。"""
    return _order(customer, item, user)


@pytest.fixture
def shipment(user, order, no_http):
    return services.ship(order.lines.get(), Decimal("4"), SHIPPED_AT, user)


@pytest.fixture
def send_connection(monkeypatch, customer, user):
    monkeypatch.setenv(ENV_NAME, _new_secret())
    return connect_services.register_connection(customer, BUYER_URL, ENV_NAME, "send", user)


@pytest.mark.django_db
def test_assign_notice_number_is_stable(user, shipment):
    first = services.assign_notice_number(shipment, user)
    second = services.assign_notice_number(shipment, user)

    assert isinstance(first, uuid.UUID)
    assert first == second
    assert Shipment.objects.get(pk=shipment.pk).notice_message_id == first


@pytest.mark.django_db
def test_record_notice_response_truncates_and_marks_sent(user, shipment):
    saved = services.record_notice_response(shipment, "あ" * 600, user)

    assert isinstance(saved, Shipment)
    assert saved is not shipment and saved.pk == shipment.pk
    assert len(saved.notice_response) == 500
    assert saved.notice_sent_at is None

    sent = services.record_notice_response(shipment, "受け取った", user, sent=True)

    assert sent.notice_response == "受け取った"
    assert sent.notice_sent_at is not None

    # sent が偽の 2 回目は、送った日時を消さない
    again = services.record_notice_response(shipment, "返事が読めません (HTTP 500)", user)

    assert again.notice_sent_at == sent.notice_sent_at
    assert Shipment.objects.get(pk=shipment.pk).notice_response == "返事が読めません (HTTP 500)"


def test_notice_state_text():
    assert Shipment().notice_state() == ""
    assert Shipment(notice_response="届きません: 相手につながりません。").notice_state() == (
        "届きません: 相手につながりません。"
    )
    sent = Shipment(notice_sent_at=datetime(2026, 11, 5, 1, 30, tzinfo=UTC), notice_response="受け取った")
    # 日時は会社の時刻 (Asia/Tokyo) で出す
    assert sent.notice_state() == "送った 2026-11-05 10:30"


@pytest.mark.django_db
def test_ship_manual_order_calls_no_http(user, order, no_http):
    line = order.lines.get()

    first = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    second = services.ship(line, Decimal("6"), SHIPPED_AT, user)

    assert services._is_edi_order(order) is False
    for shipment in (first, second):
        assert isinstance(shipment, Shipment)
        assert shipment.notice_message_id is None
        assert shipment.notice_sent_at is None
        assert shipment.notice_response == ""
    order.refresh_from_db()
    assert order.status == Order.STATUS_SHIPPED


@pytest.mark.django_db
def test_ship_inbox_order_returns_shipment_with_notice_columns(monkeypatch, user, customer, item, send_connection):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    order = _inbox_order(customer, item, user)
    assert services._is_edi_order(order) is True
    line = order.lines.get()

    shipment = services.ship(line, Decimal("4"), SHIPPED_AT, user)

    (call,) = fake.calls
    assert call["url"] == BUYER_URL
    assert isinstance(shipment, Shipment)
    assert shipment.notice_sent_at is not None
    assert shipment.notice_response == "受け取った"
    assert str(shipment.notice_message_id) == json.loads(call["data"].decode("utf-8"))["id"]
    # 受注の状態の進み方は今までどおり
    order.refresh_from_db()
    assert order.status == Order.STATUS_PARTIAL

    services.ship(line, Decimal("6"), SHIPPED_AT, user)

    assert len(fake.calls) == 2
    order.refresh_from_db()
    assert order.status == Order.STATUS_SHIPPED


@pytest.mark.django_db
def test_update_shipment_does_not_send(monkeypatch, user, customer, item, send_connection):
    monkeypatch.setattr("requests.post", FakePost())
    order = _inbox_order(customer, item, user)
    shipment = services.ship(order.lines.get(), Decimal("4"), SHIPPED_AT, user)
    sent_at = shipment.notice_sent_at
    assert sent_at is not None

    def fail(*args, **kwargs):
        raise AssertionError("出荷の編集で出荷の知らせを送り直した")

    monkeypatch.setattr("requests.post", fail)
    updated = services.update_shipment(shipment, Decimal("5"), SHIPPED_AT, user)

    assert updated.qty == Decimal("5")
    assert Shipment.objects.get(pk=shipment.pk).notice_sent_at == sent_at


def test_shipment_hides_number_in_detail_fields():
    # 受注の明細の無い出荷は、送る物でない側 (番号と知らせの 2 列を隠す)。DB に問い合わせない。
    # 受信した注文で作った受注の出荷で 2 列が出ることは、test_shipment_detail_shows_sent_badge_without_resend が見る。
    hidden = hidden_detail_fields(Shipment())
    assert "notice_message_id" in hidden
    assert "notice_sent_at" in hidden
    assert "notice_response" in hidden


# --- 2 段目: 出荷の詳細の「出荷の知らせ」の節と「送り直す」の口 ---


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def _ship_with(monkeypatch, fake, customer, item, user):
    """受信した注文で作った受注に、相手の役を fake にして 4 を出荷する。"""
    monkeypatch.setattr("requests.post", fake)
    order = _inbox_order(customer, item, user)
    return services.ship(order.lines.get(), Decimal("4"), SHIPPED_AT, user)


def _unreachable():
    return FakePost(raises=requests.ConnectionError("作り物の失敗"))


def _detail(client, shipment):
    return client.get(reverse("shipping:shipment_detail", args=[shipment.pk])).content.decode()


def _section(body):
    start = body.index('id="shipment-notice-title"')
    return body[body.rindex("<section", 0, start) : body.index("</section>", start)]


def _messages(response):
    return [str(message) for message in response.context["messages"]]


@pytest.mark.django_db
def test_shipment_detail_shows_sent_badge_without_resend(monkeypatch, logged_in, user, customer, item, send_connection):
    shipment = _ship_with(monkeypatch, FakePost(), customer, item, user)
    assert shipment.notice_sent_at is not None

    body = _detail(logged_in, shipment)

    assert 'id="shipment-notice-title"' in body
    section = _section(body)
    assert "出荷の知らせ" in section
    assert "badge-success" in section
    assert "送った " in section
    assert "btn-primary" not in section
    assert reverse("shipping:shipment_notice_resend", args=[shipment.pk]) not in body
    assert "送り直す" not in body
    # 基本情報には 2 列が土台で並び、番号 (uuid) は出ない
    assert "出荷の知らせを送った日時" in body
    assert "出荷の知らせの相手の返事" in body
    assert "受け取った" in body
    assert str(shipment.notice_message_id) not in body
    assert "出荷の知らせの番号" not in body


@pytest.mark.django_db
def test_shipment_detail_shows_red_notice_and_resend_form(monkeypatch, logged_in, user, customer, item, send_connection):
    shipment = _ship_with(monkeypatch, _unreachable(), customer, item, user)
    assert shipment.notice_sent_at is None

    section = _section(_detail(logged_in, shipment))

    assert "alert-error" in section
    assert "届きません" in section
    form = re.search(r"<form[^>]*>.*?</form>", section, re.S).group(0)
    assert f'action="{reverse("shipping:shipment_notice_resend", args=[shipment.pk])}"' in form
    assert 'method="post"' in form
    assert 'name="csrfmiddlewaretoken"' in form
    assert "送り直す" in form


@pytest.mark.django_db
def test_manual_shipment_detail_has_no_notice_section(logged_in, user, shipment):
    body = _detail(logged_in, shipment)

    assert "shipment-notice-title" not in body
    # 手入力の受注の出荷は知らせを送る物でないので、知らせの 2 行を出さない (初見テスト R5 の候補 C18 の A2)
    for label in ("出荷の知らせを送った日時", "出荷の知らせの相手の返事"):
        assert re.search(rf"<dt[^>]*>{label}</dt>", body) is None, label


@pytest.mark.django_db
def test_resend_port_sends_and_redirects(monkeypatch, logged_in, user, customer, item, send_connection):
    shipment = _ship_with(monkeypatch, _unreachable(), customer, item, user)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    url = reverse("shipping:shipment_notice_resend", args=[shipment.pk])
    detail = reverse("shipping:shipment_detail", args=[shipment.pk])

    response = logged_in.post(url, follow=True)

    assert response.redirect_chain == [(detail, 302)]
    assert "出荷の知らせを送りました。" in _messages(response)
    (call,) = fake.calls
    assert json.loads(call["data"].decode("utf-8"))["id"] == str(shipment.notice_message_id)
    shipment.refresh_from_db()
    assert shipment.notice_sent_at is not None
    assert shipment.notice_response == "受け取った"

    # 別の届いていない出荷。相手がまだ届かない (ConnectionError) と、理由を赤の知らせに出して戻る
    other = _ship_with(monkeypatch, _unreachable(), customer, item, user)
    response = logged_in.post(reverse("shipping:shipment_notice_resend", args=[other.pk]), follow=True)

    assert response.redirect_chain == [(reverse("shipping:shipment_detail", args=[other.pk]), 302)]
    assert any("届きません" in text for text in _messages(response))
    other.refresh_from_db()
    assert other.notice_sent_at is None


@pytest.mark.django_db
def test_resend_port_refuses_get_and_anonymous(monkeypatch, client, user, customer, item, send_connection):
    shipment = _ship_with(monkeypatch, _unreachable(), customer, item, user)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    url = reverse("shipping:shipment_notice_resend", args=[shipment.pk])

    anonymous = client.post(url)

    assert anonymous.status_code == 302
    assert reverse("core:login") in anonymous["Location"]

    client.force_login(user)

    assert client.get(url).status_code == 405
    assert client.post(reverse("shipping:shipment_notice_resend", args=[shipment.pk + 100])).status_code == 404
    assert fake.calls == []


@pytest.mark.django_db
def test_resend_port_on_manual_shipment_records_reason(monkeypatch, logged_in, user, shipment):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    response = logged_in.post(reverse("shipping:shipment_notice_resend", args=[shipment.pk]), follow=True)

    assert response.redirect_chain == [(reverse("shipping:shipment_detail", args=[shipment.pk]), 302)]
    assert any("受信した注文で作った受注ではない" in text for text in _messages(response))
    assert fake.calls == []
    shipment.refresh_from_db()
    assert "受信した注文で作った受注ではない" in shipment.notice_response
    assert shipment.notice_message_id is None


@pytest.mark.django_db
def test_resend_port_follows_write_permission(monkeypatch, settings, logged_in, user, customer, item, send_connection):
    settings.SEIZOU = {"access_control": {"department": "off", "write": "on", "money": "off"}}
    shipment = _ship_with(monkeypatch, _unreachable(), customer, item, user)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    url = reverse("shipping:shipment_notice_resend", args=[shipment.pk])

    # 権限の行の無い利用者は断られる (送らない)
    response = logged_in.post(url, follow=True)

    assert response.redirect_chain == [(reverse("shipping:shipment_detail", args=[shipment.pk]), 302)]
    assert fake.calls == []
    assert any("出荷と納品書の編集の権限がありません" in text for text in _messages(response))
    assert access.write_message("shipping") in _messages(response)

    # システム管理者は通る
    admin = get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")
    logged_in.force_login(admin)
    response = logged_in.post(url, follow=True)

    assert len(fake.calls) == 1
    assert "出荷の知らせを送りました。" in _messages(response)
