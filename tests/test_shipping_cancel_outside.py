"""請求書の外で受け取った入金を記録した出荷のある納品書の取消を断る (作者の決め C107-11。shipping.services.outside_paid_shipments と cancel_refusal)。

記録した後に納品書を取り消して出荷の数量を直せると、受け取った代金と出荷が食い違うので、請求済みの出荷と同じ扱いで断る。
文の順は 出荷の行の請求書 → 一式の行の請求書 → 外の入金。形の手本 = tests/test_shipping_cancel_lump.py。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.models import DeliveryNote

NOV_05 = date(2026, 11, 5)
NOV_30 = date(2026, 11, 30)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
#: 入力のカード (core/_form_card.html) の form の頭
FORM_CARD = '<form method="post" class="rounded-xl'


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def line(user, customer):
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("100"), "unit_price": Decimal("275")}],
        user,
        order_date=date(2026, 11, 1),
    )
    return order.lines.get()


def _note(user, customer, line, *qtys):
    shipments = [services.ship(line, Decimal(qty), NOV_05, user) for qty in qtys]
    note = services.create_delivery_note(customer, shipments, user)
    for shipment in shipments:
        shipment.refresh_from_db()
    return note, shipments


@pytest.mark.django_db
def test_outside_paid_note_cannot_be_cancelled(user, customer, line):
    note, [shipment] = _note(user, customer, line, "5")
    payment = billing_services.record_outside_payment(shipment, NOV_05, Decimal("1375"), user)
    expected = (
        f"取り消せません。納品書 {note.note_no} の出荷 {shipment.shipment_no} は、請求書の外で受け取った入金を記録しています。"
        "先に入金を取り消してから、納品書を取り消してください。"
    )

    assert services.outside_paid_shipments(note) == [(shipment, payment)]
    assert services.cancel_refusal(note) == expected
    with pytest.raises(ValueError) as caught:
        services.cancel_delivery_note(note, user)
    assert str(caught.value) == expected
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_ISSUED


@pytest.mark.django_db
def test_cancelling_payment_allows_note_cancel(user, customer, line):
    note, [shipment] = _note(user, customer, line, "5")
    payment = billing_services.record_outside_payment(shipment, NOV_05, Decimal("1375"), user)

    billing_services.cancel_payment(payment, user)

    assert services.cancel_refusal(note) == ""
    assert services.cancel_delivery_note(note, user).status == DeliveryNote.STATUS_CANCELLED


@pytest.mark.django_db
def test_invoice_refusal_comes_first(user, customer, line):
    note, [billed, paid] = _note(user, customer, line, "4", "5")
    invoice = billing_services.create_invoice(customer, [billed], user, issued_on=NOV_30)
    billing_services.record_outside_payment(paid, NOV_05, Decimal("1375"), user)

    refusal = services.cancel_refusal(note)

    assert f"出荷 {billed.shipment_no} は請求書 {invoice.invoice_no} で請求済みです。" in refusal
    assert "請求書の外で受け取った入金" not in refusal


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, MODAL])
def test_cancel_screen_lists_outside_paid_shipment(client, user, customer, line, headers):
    note, [shipment] = _note(user, customer, line, "5")
    billing_services.record_outside_payment(shipment, NOV_05, Decimal("1375"), user)
    client.force_login(user)

    body = client.get(reverse("shipping:deliverynote_cancel", args=[note.pk]), **headers).content.decode()

    assert '<span class="font-semibold">取り消せません。</span>' in body
    assert "は請求書の外で受け取った入金を記録済み" in body
    assert f'href="{reverse("shipping:shipment_detail", args=[shipment.pk])}"' in body
    assert FORM_CARD not in body

    detail = client.get(reverse("shipping:deliverynote_detail", args=[note.pk])).content.decode()
    assert 'id="deliverynote-invoices"' not in detail


@pytest.mark.django_db
def test_note_without_outside_payment_is_unchanged(user, customer, line):
    note, _shipments = _note(user, customer, line, "5")

    assert services.outside_paid_shipments(note) == []
    assert services.cancel_refusal(note) == ""
