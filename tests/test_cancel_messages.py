"""取消の後の知らせ (N106。精査 A7 の 4 節の 5・決め 11-04 14:4x の (5))。

取消の service が、返す行に saved_message (「取り消しました。」) を付け、
入力の土台 (core.views.with_saved_note) が「保存しました。」の代わりに緑の知らせに出す。
右上の知らせは get_messages(response.wsgi_request) の (level_tag, 字) で見る (tests/test_purchasing_send_receipt_polish.py と同じ形)。
取引先と品目は作り物。発注の取消 (purchasing) はこのファイルの外。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.billing.models import Invoice, Payment
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.shipping import services as shipping_services
from seizou.shipping.models import DeliveryNote
from seizou.workorders import services as workorders_services
from seizou.workorders.models import WorkOrder

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
RECEIVED_ON = date(2026, 12, 25)
CANCELLED = [("success", "取り消しました。")]


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def order(user):
    """作り物の得意先の、数量 10 の明細 1 行の受注。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )


def _messages(response):
    """その要求で足した右上の知らせ (level_tag と字) の並び。"""
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def test_order_cancel_says_cancelled(logged_in, order):
    response = logged_in.post(reverse("orders:order_cancel", args=[order.pk]), {"reason": "作り物の理由"})

    assert response.status_code == 302
    assert response["Location"] == reverse("orders:order_detail", args=[order.pk])
    assert _messages(response) == CANCELLED
    order.refresh_from_db()
    assert order.status == Order.STATUS_CANCELLED


def test_workorder_cancel_says_cancelled(logged_in, user, order):
    cut = Process.objects.create(code="CUT", name="切断", sort_order=1, use_in_plan=True)
    workorder = workorders_services.issue_workorder(
        order.lines.get(), Decimal("4"), [{"process": cut, "planned_qty": Decimal("4")}], user
    )

    response = logged_in.post(reverse("workorders:workorder_cancel", args=[workorder.pk]), {"reason": ""})

    assert response.status_code == 302
    assert response["Location"] == reverse("workorders:workorder_detail", args=[workorder.pk])
    assert _messages(response) == CANCELLED
    workorder.refresh_from_db()
    assert workorder.status == WorkOrder.STATUS_CANCELLED


@pytest.fixture
def note(user, order):
    """受注の 4 を出荷し、納品書にまとめた物。"""
    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), SHIPPED_AT, user)
    return shipping_services.create_delivery_note(order.customer, [shipment], user)


def test_delivery_note_cancel_says_cancelled(logged_in, note):
    response = logged_in.post(reverse("shipping:deliverynote_cancel", args=[note.pk]), {})

    assert response.status_code == 302
    assert response["Location"] == reverse("shipping:deliverynote_detail", args=[note.pk])
    assert _messages(response) == CANCELLED
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_CANCELLED


@pytest.fixture
def invoice(user, order, note):
    """納品書の出荷 4 の請求書 (入金なし)。"""
    return billing_services.create_invoice(order.customer, list(note.shipments.all()), user, issued_on=ISSUED_ON)


def test_invoice_cancel_says_cancelled(logged_in, invoice):
    response = logged_in.post(reverse("billing:invoice_cancel", args=[invoice.pk]), {})

    assert response.status_code == 302
    assert response["Location"] == reverse("billing:invoice_detail", args=[invoice.pk])
    assert _messages(response) == CANCELLED
    invoice.refresh_from_db()
    assert invoice.status == Invoice.STATUS_CANCELLED


def test_payment_cancel_says_cancelled(logged_in, user, invoice):
    payment = billing_services.record_payment(invoice, RECEIVED_ON, Decimal("200"), user)

    response = logged_in.post(reverse("billing:payment_cancel", args=[payment.pk]), {})

    assert response.status_code == 302
    assert response["Location"] == reverse("billing:payment_detail", args=[payment.pk])
    assert _messages(response) == CANCELLED
    payment.refresh_from_db()
    assert payment.status == Payment.STATUS_CANCELLED
