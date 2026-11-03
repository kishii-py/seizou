"""R21: 取消した請求書には、入金を記録できない。billing.services.record_payment が止める。

発行の請求書には記録できる。入金が残る請求書の取消は今までどおり通り、その後の入金は R21 で止まる。
残っていた入金は cancel_payment で取り消せる (記録を直す道)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.billing import services
from seizou.billing.models import Invoice, Payment
from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
RECEIVED_ON = date(2026, 12, 25)
R21_RULE = "取消した請求書には、入金を記録できない。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def invoice(user):
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}],
        user,
        order_date=ORDER_DATE,
    )
    shipment = shipping_services.ship(order.lines.get(), Decimal("10"), SHIPPED_AT, user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    return services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)


def test_r21_cancelled_invoice_refuses_payment(user, invoice):
    services.cancel_invoice(invoice, user)

    with pytest.raises(RuleViolation) as caught:
        services.record_payment(invoice, RECEIVED_ON, Decimal("1000"), user)

    assert caught.value.code == "R21"
    message = str(caught.value)
    assert R21_RULE in message
    assert invoice.invoice_no in message
    assert Payment.objects.count() == 0


def test_r21_issued_invoice_accepts_payment(user, invoice):
    payment = services.record_payment(invoice, RECEIVED_ON, Decimal("1000"), user)

    assert payment.invoice_id == invoice.pk
    assert Payment.objects.filter(invoice=invoice).count() == 1


def test_r21_cancelled_invoice_keeps_old_payment_but_refuses_new(user, invoice):
    old = services.record_payment(invoice, RECEIVED_ON, Decimal("1000"), user)

    # 入金が残っていても、請求書の取消は今までどおり通る。
    services.cancel_invoice(invoice, user)
    assert Invoice.objects.get(pk=invoice.pk).status == Invoice.STATUS_CANCELLED

    with pytest.raises(RuleViolation) as caught:
        services.record_payment(invoice, RECEIVED_ON, Decimal("500"), user)
    assert caught.value.code == "R21"
    assert Payment.objects.count() == 1

    # 残っていた入金は取り消せる (記録を直す道)。
    cancelled = services.cancel_payment(old, user)
    assert cancelled.status == Payment.STATUS_CANCELLED
    assert Invoice.objects.get(pk=invoice.pk).paid_total() == 0
