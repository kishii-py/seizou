"""発行の請求書に入った出荷は、納品書の帳票でも請求書の明細の写しの単価・単価の単位・金額で刷る (shipping.reports.delivery_note_context)。

- 請求書を作った後に受注の単価を直しても、納品書の刷り直しの単価と金額は請求書の明細と同じまま。
- 請求書を取り消すと、納品書の行は今の受注の単価に戻る (取消の請求書は読まない)。
- 1 枚の納品書に請求書に入った出荷と入っていない出荷があれば、入った行は写し、入っていない行は今の単価で、合計は行の金額の合計。
作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.reports import delivery_note_context

ORDER_DATE = date(2026, 10, 1)
SHIPPED_AT = date(2026, 10, 10)
ISSUED_ON = date(2026, 10, 15)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, qty="50", unit_price="1650"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price)}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _ship(user, line, qty):
    shipment = services.ship(line, Decimal(qty), SHIPPED_AT, user)
    shipment.refresh_from_db()
    return shipment


def _change_price(user, line, unit_price):
    orders_services.update_order(line.order, {}, [{"id": line.pk, "unit_price": Decimal(unit_price)}], user)


def _invoiced_note_after_price_change(user, customer, item):
    """数量 50・単価 1650 の明細を出荷し、納品書と請求書を作った後に単価を 1700 に直す。(納品書, 請求書) を返す。"""
    line = _line(user, customer, item)
    shipment = _ship(user, line, "50")
    note = services.create_delivery_note(customer, [shipment], user)
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    _change_price(user, line, "1700")
    return note, invoice


@pytest.mark.django_db
def test_invoiced_shipment_keeps_the_invoice_copy_after_price_change(client, user, customer, item):
    note, invoice = _invoiced_note_after_price_change(user, customer, item)
    invoice_line = invoice.lines.get()
    assert invoice_line.amount == Decimal("82500")

    context = delivery_note_context(note)
    [row] = context["lines"]
    assert row["unit_price"] == Decimal("1650")
    assert row["price_unit"] == invoice_line.price_unit_text == "個"
    assert row["amount"] == invoice_line.amount
    assert context["total"] == invoice_line.amount

    client.force_login(user)
    body = client.get(reverse("shipping:deliverynote_print", args=[note.pk])).content.decode()
    assert "¥1,650 / 個" in body
    assert "¥1,700" not in body


@pytest.mark.django_db
def test_cancelled_invoice_is_not_read(user, customer, item):
    note, invoice = _invoiced_note_after_price_change(user, customer, item)
    billing_services.cancel_invoice(invoice, user)

    context = delivery_note_context(note)
    [row] = context["lines"]
    assert row["unit_price"] == Decimal("1700")
    assert row["amount"] == Decimal("85000")
    assert context["total"] == Decimal("85000")


@pytest.mark.django_db
def test_note_mixes_invoiced_and_uninvoiced_shipments(user, customer, item):
    line = _line(user, customer, item)
    invoiced = _ship(user, line, "20")
    uninvoiced = _ship(user, line, "30")
    note = services.create_delivery_note(customer, [invoiced, uninvoiced], user)
    billing_services.create_invoice(customer, [invoiced], user, issued_on=ISSUED_ON)
    _change_price(user, line, "1700")

    context = delivery_note_context(note)
    rows = {row["shipment_no"]: row for row in context["lines"]}
    assert (rows[invoiced.shipment_no]["unit_price"], rows[invoiced.shipment_no]["amount"]) == (
        Decimal("1650"),
        Decimal("33000"),
    )
    assert (rows[uninvoiced.shipment_no]["unit_price"], rows[uninvoiced.shipment_no]["amount"]) == (
        Decimal("1700"),
        Decimal("51000"),
    )
    assert context["total"] == Decimal("84000")
