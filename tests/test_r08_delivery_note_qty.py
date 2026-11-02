"""R08: 納品書の数量は出荷の数量と一致する。納品書を出した後の出荷は、納品書を取り消してから直す。

納品書は数量の写しを持たず、結ばれた出荷そのものを明細とする。
得意先の違う出荷と、既に発行の納品書に入っている出荷は、納品書にまとめられない。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.models import DeliveryNote, Shipment

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
R08_RULE = "納品書の数量は出荷の数量と一致する。納品書を出した後の出荷は、納品書を取り消してから直す。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def item(user):
    return masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user
    )


def _customer(user, code, name):
    return masters_services.create_partner({"code": code, "name": name, "role": "customer"}, user)


def _order_line(user, customer, item, qty="10"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def customer(user):
    return _customer(user, "C001", "作り物の得意先")


@pytest.fixture
def line(user, customer, item):
    return _order_line(user, customer, item)


@pytest.mark.django_db
def test_note_quantities_equal_shipment_quantities(user, customer, line):
    first = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    second = services.ship(line, Decimal("6"), SHIPPED_AT, user)

    note = services.create_delivery_note(customer, [first, second], user)

    assert isinstance(note, DeliveryNote)
    on_note = {row.pk: row.qty for row in note.shipments.all()}
    assert on_note == {first.pk: Decimal("4"), second.pk: Decimal("6")}
    assert sum(on_note.values()) == Decimal("10")

    # 納品書は数量と金額の写しの列を持たない。出荷の数の列は出荷の qty の 1 つだけ。
    assert not [f for f in DeliveryNote._meta.get_fields() if isinstance(f, models.DecimalField)]
    assert [f.name for f in Shipment._meta.get_fields() if isinstance(f, models.DecimalField)] == ["qty"]


@pytest.mark.django_db
def test_other_customers_shipment_is_refused(user, customer, item, line):
    other = _customer(user, "C002", "作り物の別の得意先")
    other_line = _order_line(user, other, item)
    mine = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    theirs = services.ship(other_line, Decimal("3"), SHIPPED_AT, user)

    with pytest.raises(RuleViolation) as caught:
        services.create_delivery_note(customer, [mine, theirs], user)
    assert caught.value.code == "R08"
    assert caught.value.message.startswith(R08_RULE)
    assert theirs.shipment_no in caught.value.message

    assert DeliveryNote.objects.count() == 0
    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.delivery_note is None
    assert theirs.delivery_note is None


@pytest.mark.django_db
def test_shipment_already_on_a_note_is_refused(user, customer, line):
    first = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    second = services.ship(line, Decimal("6"), SHIPPED_AT, user)
    note = services.create_delivery_note(customer, [first], user)

    with pytest.raises(RuleViolation) as caught:
        services.create_delivery_note(customer, [first, second], user)
    assert caught.value.code == "R08"
    assert caught.value.message.startswith(R08_RULE)
    assert note.note_no in caught.value.message

    assert DeliveryNote.objects.count() == 1
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.delivery_note == note
    assert second.delivery_note is None


@pytest.mark.django_db
def test_empty_shipments_is_refused(user, customer):
    with pytest.raises(ValueError, match="出荷がありません"):
        services.create_delivery_note(customer, [], user)
    assert DeliveryNote.objects.count() == 0


@pytest.mark.django_db
def test_note_number_and_date_are_set(user, customer, line):
    shipment = services.ship(line, Decimal("10"), SHIPPED_AT, user)

    note = services.create_delivery_note(customer, [shipment], user)

    note.refresh_from_db()
    assert note.note_no.startswith("DN-")
    assert note.issued_on == timezone.localdate()
    assert note.status == "issued"
    assert note.customer == customer
    assert str(note) == note.note_no
