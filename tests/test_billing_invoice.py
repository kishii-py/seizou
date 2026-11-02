"""請求書の関数 (billing.services) の動き。番号・金額の丸め・消費税・期間・写し・取消・候補・支払期日。"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.billing import services
from seizou.billing.models import Invoice
from seizou.core.models import Company
from seizou.masters import services as masters_services
from seizou.masters.models import Partner
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _customer(user, code="C001", **terms):
    data = {"code": code, "name": f"作り物の得意先 {code}", "role": "customer", **terms}
    return masters_services.create_partner(data, user)


def _order_line(user, customer, item, *, qty="10", unit_price="100"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price)}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _shipped(user, customer, line, qty, shipped_at=SHIPPED_AT):
    """納品書の出た出荷 (出荷済み) を 1 つ作る。"""
    shipment = shipping_services.ship(line, Decimal(qty), shipped_at, user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    return shipment


def _invoice_of_two_15_yen_lines(user, item, tax_mode):
    """金額 15 の明細 2 行の請求書 (消費税の計算方法の確かめ)。"""
    customer = _customer(user, tax_mode=tax_mode)
    line = _order_line(user, customer, item, qty="2", unit_price="15")
    first = _shipped(user, customer, line, "1")
    second = _shipped(user, customer, line, "1")
    return services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)


@pytest.mark.django_db
def test_number_starts_with_iv(user, item):
    customer = _customer(user)
    line = _order_line(user, customer, item)
    shipment = _shipped(user, customer, line, "10")

    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)

    invoice.refresh_from_db()
    assert invoice.invoice_no.startswith("IV-")
    assert str(invoice) == invoice.invoice_no
    assert invoice.issued_on == ISSUED_ON
    assert invoice.customer == customer


@pytest.mark.django_db
def test_empty_shipments_is_value_error(user):
    customer = _customer(user)
    with pytest.raises(ValueError, match="出荷がありません"):
        services.create_invoice(customer, [], user, issued_on=ISSUED_ON)
    assert Invoice.objects.count() == 0


@pytest.mark.django_db
def test_amount_uses_round_amount(user, item):
    customer = _customer(user, rounding="ceil")
    line = _order_line(user, customer, item, qty="3", unit_price="33.40")
    shipment = _shipped(user, customer, line, "3")

    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)

    row = invoice.lines.get()
    # 3 × 33.40 = 100.20 を、取引先の小計の計算方法 (切上) で 101 にする。
    assert row.qty == Decimal("3")
    assert row.unit_price == Decimal("33.40")
    assert row.amount == Decimal("101")
    assert invoice.subtotal == Decimal("101")


@pytest.mark.django_db
def test_tax_total(user, item):
    invoice = _invoice_of_two_15_yen_lines(user, item, "total")
    assert invoice.subtotal == Decimal("30")
    assert invoice.tax == Decimal("3")
    assert invoice.total == Decimal("33")


@pytest.mark.django_db
def test_tax_line_round(user, item):
    invoice = _invoice_of_two_15_yen_lines(user, item, "line_round")
    assert invoice.tax == Decimal("4")
    assert invoice.total == Decimal("34")


@pytest.mark.django_db
def test_tax_line_floor(user, item):
    invoice = _invoice_of_two_15_yen_lines(user, item, "line_floor")
    assert invoice.tax == Decimal("2")
    assert invoice.total == Decimal("32")


@pytest.mark.django_db
def test_blank_partner_tax_mode_uses_company(user, item):
    Company.objects.create(name="作り物の会社", tax_mode="line_floor")
    invoice = _invoice_of_two_15_yen_lines(user, item, "")
    assert invoice.tax == Decimal("2")


@pytest.mark.django_db
def test_period_is_first_and_last_ship_date(user, item):
    customer = _customer(user)
    line = _order_line(user, customer, item)
    middle = _shipped(user, customer, line, "3", date(2026, 11, 5))
    first = _shipped(user, customer, line, "3", date(2026, 11, 3))
    last = _shipped(user, customer, line, "4", date(2026, 11, 10))

    invoice = services.create_invoice(customer, [middle, last, first], user, issued_on=ISSUED_ON)

    assert invoice.period_from == date(2026, 11, 3)
    assert invoice.period_to == date(2026, 11, 10)
    # 明細の並びは出荷日の順。
    assert [row.shipment_id for row in invoice.lines.all()] == [first.pk, middle.pk, last.pk]


@pytest.mark.django_db
def test_snapshot_kept_after_price_change(user, item):
    customer = _customer(user)
    line = _order_line(user, customer, item)
    shipment = _shipped(user, customer, line, "10")
    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)

    orders_services.update_order(line.order, {}, [{"id": line.pk, "unit_price": Decimal("200")}], user)

    line.refresh_from_db()
    assert line.unit_price == Decimal("200")
    invoice.refresh_from_db()
    row = invoice.lines.get()
    assert row.unit_price == Decimal("100")
    assert row.amount == Decimal("1000")
    assert invoice.subtotal == Decimal("1000")


@pytest.mark.django_db
def test_cancel_twice_is_value_error(user, item):
    customer = _customer(user)
    line = _order_line(user, customer, item)
    shipment = _shipped(user, customer, line, "10")
    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)

    services.cancel_invoice(invoice, user)
    with pytest.raises(ValueError, match="取り消し済み"):
        services.cancel_invoice(invoice, user)

    invoice.refresh_from_db()
    assert invoice.status == Invoice.STATUS_CANCELLED


@pytest.mark.django_db
def test_billable_shipments_matches_create_rules(user, item):
    customer = _customer(user)
    other = _customer(user, "C002")
    line = _order_line(user, customer, item)
    other_line = _order_line(user, other, item)
    no_note = shipping_services.ship(line, Decimal("2"), SHIPPED_AT, user)
    invoiced = _shipped(user, customer, line, "3")
    billable = _shipped(user, customer, line, "5")
    others = _shipped(user, other, other_line, "4")
    services.create_invoice(customer, [invoiced], user, issued_on=ISSUED_ON)

    assert set(services.billable_shipments()) == {billable, others}
    assert list(services.billable_shipments(customer)) == [billable]
    assert no_note not in services.billable_shipments()


# --- 支払期日 (payment_due_date) ---


def _terms(closing_day, payment_month, payment_day):
    """支払の欄だけを持つ取引先 (保存しない)。"""
    return Partner(closing_day=closing_day, payment_month=payment_month, payment_day=payment_day)


def test_due_next_month_before_and_after_closing():
    customer = _terms(20, "next", 25)
    assert services.payment_due_date(customer, date(2026, 11, 20)) == date(2026, 12, 25)
    assert services.payment_due_date(customer, date(2026, 11, 21)) == date(2027, 1, 25)


def test_due_current_month_end():
    customer = _terms(20, "current", 31)
    assert services.payment_due_date(customer, date(2026, 11, 10)) == date(2026, 11, 30)


def test_due_month_end_in_february():
    customer = _terms(31, "next", 31)
    assert services.payment_due_date(customer, date(2027, 1, 15)) == date(2027, 2, 28)


def test_due_payment_day_31_in_30_day_month():
    customer = _terms(20, "next", 31)
    assert services.payment_due_date(customer, date(2026, 3, 10)) == date(2026, 4, 30)


def test_due_closing_31_is_month_end():
    customer = _terms(31, "next", 25)
    assert services.payment_due_date(customer, date(2026, 11, 30)) == date(2026, 12, 25)


def test_due_crosses_year():
    customer = _terms(20, "after_next", 25)
    assert services.payment_due_date(customer, date(2026, 12, 21)) == date(2027, 3, 25)


def test_due_is_none_when_terms_blank():
    base = date(2026, 11, 10)
    assert services.payment_due_date(_terms(20, "", 25), base) is None
    assert services.payment_due_date(_terms(20, "next", None), base) is None
    assert services.payment_due_date(_terms(None, "next", 25), base) is None


@pytest.mark.django_db
def test_invoice_due_on_from_last_ship_date(user, item):
    customer = _customer(user, closing_day=20, payment_month="next", payment_day=25)
    line = _order_line(user, customer, item)
    before = _shipped(user, customer, line, "4", date(2026, 11, 20))
    after = _shipped(user, customer, line, "6", date(2026, 11, 21))

    invoice = services.create_invoice(customer, [before, after], user, issued_on=ISSUED_ON)

    invoice.refresh_from_db()
    assert invoice.due_on == date(2027, 1, 25)

    blank = _customer(user, "C002")
    blank_line = _order_line(user, blank, item)
    shipment = _shipped(user, blank, blank_line, "10")
    no_terms = services.create_invoice(blank, [shipment], user, issued_on=ISSUED_ON)
    no_terms.refresh_from_db()
    assert no_terms.due_on is None
