"""納品書を取り消す関数 (shipping.services.cancel_delivery_note)。

取消の納品書は出荷を指したまま残す。2 回目の取消と、発行の請求書に入った出荷の納品書の取消は ValueError。
請求書を先に取り消せば、納品書を取り消せる。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.models import DeliveryNote

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def line(user, customer):
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def note_with_two(user, customer, line):
    """出荷 4 と 6 をまとめた発行の納品書。"""
    first = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    second = services.ship(line, Decimal("6"), SHIPPED_AT, user)
    return services.create_delivery_note(customer, [first, second], user), first, second


@pytest.mark.django_db
def test_cancel_sets_status(user, note_with_two):
    note, _first, _second = note_with_two

    result = services.cancel_delivery_note(note, user)

    assert isinstance(result, DeliveryNote)
    assert result.pk == note.pk
    assert result.status == DeliveryNote.STATUS_CANCELLED
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_CANCELLED


@pytest.mark.django_db
def test_cancel_twice_is_refused(user, note_with_two):
    note, _first, _second = note_with_two
    services.cancel_delivery_note(note, user)

    with pytest.raises(ValueError, match="この納品書は取り消し済みです。"):
        services.cancel_delivery_note(note, user)
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_CANCELLED


@pytest.mark.django_db
def test_cancelled_note_keeps_its_shipments(user, note_with_two):
    note, first, second = note_with_two

    services.cancel_delivery_note(note, user)

    note.refresh_from_db()
    assert sorted(row.pk for row in note.shipments.all()) == sorted([first.pk, second.pk])
    first.refresh_from_db()
    assert first.delivery_note == note


@pytest.mark.django_db
def test_cancel_after_invoice_cancelled(user, customer, note_with_two):
    note, first, second = note_with_two
    first.refresh_from_db()
    second.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)
    with pytest.raises(ValueError, match=invoice.invoice_no):
        services.cancel_delivery_note(note, user)

    billing_services.cancel_invoice(invoice, user)
    result = services.cancel_delivery_note(note, user)

    assert result.status == DeliveryNote.STATUS_CANCELLED
