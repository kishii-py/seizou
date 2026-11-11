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

from seizou.billing import services as billing_services
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
    assert caught.value.message.startswith("発行できません。" + R08_RULE)
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
    assert caught.value.message.startswith("発行できません。" + R08_RULE)
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


# --- 納品書を取り消してから出荷を直す (R08 の 2 文目) ---


@pytest.mark.django_db
def test_issued_note_blocks_shipment_update(user, customer, line):
    shipment = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    note = services.create_delivery_note(customer, [shipment], user)

    with pytest.raises(RuleViolation) as caught:
        services.update_shipment(shipment, Decimal("3"), SHIPPED_AT, user)
    assert caught.value.code == "R08"
    assert caught.value.message.startswith("保存できません。" + R08_RULE)
    assert note.note_no in caught.value.message
    assert shipment.shipment_no in caught.value.message

    shipment.refresh_from_db()
    assert shipment.qty == Decimal("4")


@pytest.mark.django_db
def test_cancel_note_then_update_shipment(user, customer, line):
    shipment = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    note = services.create_delivery_note(customer, [shipment], user)

    services.cancel_delivery_note(note, user)
    services.update_shipment(shipment, Decimal("3"), SHIPPED_AT, user)

    shipment.refresh_from_db()
    assert shipment.qty == Decimal("3")
    assert orders_services.remaining_qty(line) == Decimal("7")


@pytest.mark.django_db
def test_cancelled_note_shipment_is_billable(user, customer, line):
    """取り消した納品書の出荷も請求できる (納品書は出荷の記録を刷った物。本人の決め 11-11 10:1x)。"""
    shipment = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    note = services.create_delivery_note(customer, [shipment], user)
    services.cancel_delivery_note(note, user)

    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=date(2026, 11, 30))
    assert [row.shipment_id for row in invoice.lines.all()] == [shipment.pk]


@pytest.mark.django_db
def test_invoiced_note_can_be_cancelled(user, customer, line):
    """請求書に入った出荷の納品書も取り消せる (前は断った。本人の決め 11-11 10:1x・13:0x で、止めずに検査の層の R27 で知らせる形にした。依頼書 N403b)。

    納品書は出荷の記録を刷った物なので、取り消しても請求書は発行のまま変わらない。知らせの文は shipping.services.billed_notice。
    """
    shipment = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    note = services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=date(2026, 11, 30))

    notice = services.billed_notice(note)
    assert invoice.invoice_no in notice
    assert note.note_no in notice
    assert shipment.shipment_no in notice
    assert services.cancel_delivery_note(note, user).status == DeliveryNote.STATUS_CANCELLED

    invoice.refresh_from_db()
    assert invoice.status == invoice.STATUS_ISSUED


@pytest.mark.django_db
def test_cancelled_note_shipments_go_on_a_new_note(user, customer, line):
    shipment = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    old_note = services.create_delivery_note(customer, [shipment], user)
    services.cancel_delivery_note(old_note, user)
    services.update_shipment(shipment, Decimal("3"), SHIPPED_AT, user)

    new_note = services.create_delivery_note(customer, [shipment], user)

    assert new_note.pk != old_note.pk
    assert new_note.status == DeliveryNote.STATUS_ISSUED
    shipment.refresh_from_db()
    assert shipment.delivery_note == new_note
    assert [row.qty for row in new_note.shipments.all()] == [Decimal("3")]
