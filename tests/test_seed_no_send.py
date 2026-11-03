"""受け入れと出荷の send=False (作り物の種の読み込みが相手に送らない口)。

送る向きのつながりがあっても HTTP を呼ばず、番号を付けず、返事の欄に書く。
届いた注文は受け口を通さずに record_inbox で入れる。つながりの受け口の URL は届かない所 (HTTP を呼んだら落とすので使わない)。
鍵はテストのたびに作り、環境変数に置く。取引先と品目は作り物。日は固定の値を渡す。
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.shipping import services as shipping_services
from seizou.shipping.models import Shipment
from tests.test_connect_signing import _new_secret

ENV_NAME = "SEIZOU_PARTNER_SEEDBUYER_SECRET"
BUYER_URL = "http://127.0.0.1:9/connect/receive/order/"
ORDER_DATE = date(2026, 10, 28)
SHIPPED_AT = date(2026, 10, 29)


@pytest.fixture
def no_http(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("種の読み込みで HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def send_connection(monkeypatch, customer, user):
    """得意先に送る向きのつながり (注文回答と出荷の知らせを返す向き)。"""
    monkeypatch.setenv(ENV_NAME, _new_secret())
    return connect_services.register_connection(customer, BUYER_URL, ENV_NAME, "send", user)


def _payload(message_id):
    return {
        "id": str(message_id),
        "kind": "order",
        "order_no": "PO-0001",
        "order_date": ORDER_DATE.isoformat(),
        "note": "",
        "lines": [
            {
                "line_no": 1,
                "part_no": "X-1",
                "part_name": "相手の軸",
                "qty": "10",
                "unit": "個",
                "unit_price": "100",
                "due_date": None,
                "note": "",
            }
        ],
    }


@pytest.fixture
def seed_order(customer, item, user, send_connection, no_http):
    """届いた注文を受け入れた受注 (send=False)。"""
    message_id = uuid.uuid4()
    inbox = connect_services.record_inbox(customer, _payload(message_id), user, message_id=message_id)
    return connect_services.accept(inbox, {"X-1": item}, user, send=False)


@pytest.mark.django_db
def test_accept_without_send_writes_note(seed_order):
    assert isinstance(seed_order, Order)
    assert seed_order.source == "inbox"
    inbox = Inbox.objects.get()
    assert inbox.order == seed_order
    assert inbox.status == "accepted"
    assert inbox.reply_message_id is None
    assert inbox.reply_sent_at is None
    assert inbox.reply_response == connect_services.MESSAGE_REPLY_SEED
    assert inbox.reply_state().startswith("届いていない: 作り物の種の読み込み")


@pytest.mark.django_db
def test_ship_without_send_writes_note_for_edi_order(seed_order, user, no_http):
    shipment = shipping_services.ship(seed_order.lines.get(), Decimal("4"), SHIPPED_AT, user, send=False)

    assert isinstance(shipment, Shipment)
    saved = Shipment.objects.get()
    assert saved.pk == shipment.pk
    assert saved.notice_message_id is None
    assert saved.notice_sent_at is None
    assert saved.notice_response == shipping_services.MESSAGE_NOTICE_SEED


@pytest.mark.django_db
def test_ship_without_send_leaves_manual_order_alone(customer, item, user, no_http):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )

    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), SHIPPED_AT, user, send=False)

    saved = Shipment.objects.get(pk=shipment.pk)
    assert saved.notice_response == ""
    assert saved.notice_state() == ""
