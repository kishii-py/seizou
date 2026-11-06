"""R09: 請求書の明細は、出荷済みの物だけ。

「出荷済み」は、発行の納品書に入っている出荷と読む。billing.services.create_invoice が止める。
納品書の無い出荷・出荷でない物・発行の請求書に入っている出荷・ほかの得意先の出荷は、請求書に入れられない。
請求書を取り消すと、入っていた出荷はまた請求できる。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.billing import services
from seizou.billing.models import Invoice, InvoiceLine
from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from tests.billing_helpers import shipped as _shipped

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
R09_RULE = "請求書の明細は、出荷済みの物だけ。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


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
def test_r09_lines_are_the_given_shipments(user, customer, line):
    first = _shipped(user, customer, line, "4")
    second = _shipped(user, customer, line, "6")

    invoice = services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)

    rows = list(invoice.lines.all())
    assert len(rows) == 2
    assert {row.shipment_id: row.qty for row in rows} == {first.pk: Decimal("4"), second.pk: Decimal("6")}
    assert invoice.status == Invoice.STATUS_ISSUED


@pytest.mark.django_db
def test_r09_shipment_without_delivery_note_is_refused(user, customer, line):
    shipped = _shipped(user, customer, line, "4")
    no_note = shipping_services.ship(line, Decimal("6"), SHIPPED_AT, user)

    with pytest.raises(RuleViolation) as caught:
        services.create_invoice(customer, [shipped, no_note], user, issued_on=ISSUED_ON)
    assert caught.value.code == "R09"
    assert caught.value.message.startswith("発行できません。" + R09_RULE)
    assert no_note.shipment_no in caught.value.message
    assert "外してください" in caught.value.message

    assert Invoice.objects.count() == 0
    assert InvoiceLine.objects.count() == 0


@pytest.mark.django_db
def test_r09_non_shipment_is_refused(user, customer, line):
    shipped = _shipped(user, customer, line, "4")

    with pytest.raises(RuleViolation) as caught:
        services.create_invoice(customer, [shipped, line], user, issued_on=ISSUED_ON)
    assert caught.value.code == "R09"
    assert caught.value.message.startswith("発行できません。" + R09_RULE)

    assert Invoice.objects.count() == 0


@pytest.mark.django_db
def test_r09_already_invoiced_is_refused(user, customer, line):
    first = _shipped(user, customer, line, "4")
    second = _shipped(user, customer, line, "6")
    invoice = services.create_invoice(customer, [first], user, issued_on=ISSUED_ON)

    with pytest.raises(RuleViolation) as caught:
        services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)
    assert caught.value.code == "R09"
    assert caught.value.message.startswith("発行できません。" + R09_RULE)
    assert first.shipment_no in caught.value.message
    assert invoice.invoice_no in caught.value.message

    assert Invoice.objects.count() == 1
    assert InvoiceLine.objects.filter(shipment=second).count() == 0


@pytest.mark.django_db
def test_r09_other_customer_is_refused(user, customer, item, line):
    other = _customer(user, "C002", "作り物の別の得意先")
    other_line = _order_line(user, other, item)
    mine = _shipped(user, customer, line, "4")
    theirs = _shipped(user, other, other_line, "3")

    with pytest.raises(RuleViolation) as caught:
        services.create_invoice(customer, [mine, theirs], user, issued_on=ISSUED_ON)
    assert caught.value.code == "R09"
    assert caught.value.message.startswith("発行できません。" + R09_RULE)
    assert theirs.shipment_no in caught.value.message

    assert Invoice.objects.count() == 0


@pytest.mark.django_db
def test_r09_cancelled_invoice_frees_shipments(user, customer, line):
    first = _shipped(user, customer, line, "4")
    second = _shipped(user, customer, line, "6")
    old = services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)

    cancelled = services.cancel_invoice(old, user)
    again = services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)

    assert cancelled.status == Invoice.STATUS_CANCELLED
    assert again.pk != old.pk
    assert again.status == Invoice.STATUS_ISSUED
    assert {row.shipment_id for row in again.lines.all()} == {first.pk, second.pk}
    # 取り消した請求書の明細は残る。
    assert old.lines.count() == 2
