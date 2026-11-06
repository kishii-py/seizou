"""入金の表と関数 (billing.services.record_payment・cancel_payment) と、請求書の残と入金の状態 (Invoice の method)。

入金の額はそのまま記録し、請求額との差を残として出す (多い入金も止めない。残はマイナスも出す)。
止めるのは 0 以下の額・知らない入金の方法・500 字を超える備考だけ。取消は行を消さず、状態を取消にして残を戻す。
取消した請求書への入金 (R21) は tests/test_r21_payment_on_cancelled_invoice.py が見る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.billing import services
from seizou.billing.models import Invoice, Payment
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped as _shipped

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
RECEIVED_ON = date(2026, 12, 25)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {
            "code": "C001",
            "name": "作り物の得意先",
            "role": "customer",
            "closing_day": 20,
            "payment_month": "next",
            "payment_day": 25,
        },
        user,
    )


@pytest.fixture
def line(user, customer, item):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def invoice(user, customer, line):
    """出荷 4 と 6 の請求書 (小計 15,000・消費税 1,500・合計 16,500)。"""
    first = _shipped(user, customer, line, "4")
    second = _shipped(user, customer, line, "6")
    return services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)


def _fresh(invoice):
    return Invoice.objects.get(pk=invoice.pk)


def test_invoice_starts_unpaid(invoice):
    assert invoice.total == Decimal("16500")
    assert invoice.paid_total() == 0
    assert invoice.balance() == Decimal("16500")
    assert invoice.payment_status() == "unpaid"
    assert invoice.payment_status_label() == "未入金"
    assert invoice.payments.count() == 0


def test_record_payment_creates_row_and_updates_status(user, invoice):
    payment = services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user, note="作り物の入金")

    assert isinstance(payment, Payment)
    payment.refresh_from_db()
    assert payment.invoice_id == invoice.pk
    assert payment.received_on == RECEIVED_ON
    assert payment.amount == Decimal("6500")
    assert payment.method == "transfer"
    assert payment.note == "作り物の入金"
    assert payment.recorded_by == user
    assert payment.recorded_at is not None
    assert payment.status == "recorded"
    text = str(payment)
    assert invoice.invoice_no in text and "2026-12-25" in text
    assert "6500" not in text and "6,500" not in text

    current = _fresh(invoice)
    assert current.paid_total() == Decimal("6500")
    assert current.balance() == Decimal("10000")
    assert current.payment_status() == "partial"
    assert current.payment_status_label() == "一部入金"


def test_full_and_over_payment_is_allowed(user, invoice):
    services.record_payment(invoice, RECEIVED_ON, Decimal("16500"), user)
    current = _fresh(invoice)
    assert current.payment_status() == "paid"
    assert current.payment_status_label() == "入金済み"
    assert current.balance() == 0

    extra = services.record_payment(invoice, RECEIVED_ON, Decimal("1000"), user, method="cash")

    assert extra.method == "cash"
    current = _fresh(invoice)
    assert current.balance() == Decimal("-1000")
    assert current.payment_status() == "paid"
    assert current.paid_total() == Decimal("17500")


@pytest.mark.parametrize("amount", [0, "-1", "0.00"])
def test_zero_or_negative_amount_is_refused(user, invoice, amount):
    with pytest.raises(ValueError, match="0 より大きい"):
        services.record_payment(invoice, RECEIVED_ON, amount, user)
    assert Payment.objects.count() == 0


def test_unreadable_amount_is_refused(user, invoice):
    with pytest.raises(ValueError, match="数として読めません"):
        services.record_payment(invoice, RECEIVED_ON, "abc", user)
    assert Payment.objects.count() == 0


def test_unknown_method_and_long_note_are_refused(user, invoice):
    with pytest.raises(ValueError, match="入金の方法"):
        services.record_payment(invoice, RECEIVED_ON, Decimal("100"), user, method="card")
    with pytest.raises(ValueError, match="500 字"):
        services.record_payment(invoice, RECEIVED_ON, Decimal("100"), user, note="長" * 501)
    assert Payment.objects.count() == 0


def test_future_date_is_allowed(user, invoice):
    payment = services.record_payment(invoice, date(2099, 1, 1), Decimal("100"), user)

    assert payment.received_on == date(2099, 1, 1)
    assert _fresh(invoice).paid_total() == Decimal("100")


def test_cancel_payment_keeps_row_and_restores_balance(user, invoice):
    payment = services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)

    cancelled = services.cancel_payment(payment, user)

    assert isinstance(cancelled, Payment)
    assert cancelled.status == "cancelled"
    current = _fresh(invoice)
    assert current.payments.count() == 1
    assert current.paid_total() == 0
    assert current.balance() == Decimal("16500")
    assert current.payment_status() == "unpaid"

    with pytest.raises(ValueError, match="取り消し済み"):
        services.cancel_payment(payment, user)


def test_recorded_by_is_blank_without_pk(invoice):
    payment = services.record_payment(invoice, RECEIVED_ON, Decimal("100"), None)

    payment.refresh_from_db()
    assert payment.recorded_by is None
