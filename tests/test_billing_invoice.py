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
from tests.billing_helpers import shipped as _shipped

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
    """名前は前の決め (出荷日の最初と最後) のまま。今の期間は得意先の締め期間 (決め 11-05 11:1x。締日が空 = 月末で締める)。"""
    customer = _customer(user)
    line = _order_line(user, customer, item)
    middle = _shipped(user, customer, line, "3", date(2026, 11, 5))
    first = _shipped(user, customer, line, "3", date(2026, 11, 3))
    last = _shipped(user, customer, line, "4", date(2026, 11, 10))

    invoice = services.create_invoice(customer, [middle, last, first], user, issued_on=ISSUED_ON)

    # 出荷は 11/3〜11/10 でも、期間は最後の出荷日 (11/10) を含む締め期間の 11/1〜11/30。
    assert invoice.period_from == date(2026, 11, 1)
    assert invoice.period_to == date(2026, 11, 30)
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
    # 20 日締めで最後の出荷が 11/21 → 期間は 11/21〜12/20。支払期日は期間の終わりから決める (翌月 25 日)。
    assert (invoice.period_from, invoice.period_to) == (date(2026, 11, 21), date(2026, 12, 20))
    assert invoice.due_on == date(2027, 1, 25)

    blank = _customer(user, "C002")
    blank_line = _order_line(user, blank, item)
    shipment = _shipped(user, blank, blank_line, "10")
    no_terms = services.create_invoice(blank, [shipment], user, issued_on=ISSUED_ON)
    no_terms.refresh_from_db()
    assert no_terms.due_on is None


# --- 請求の期間 (closing_period。決め 11-05 11:1x = 得意先の締め日の翌日から次の締め日) ---


def _closing(closing_day):
    """締日だけを持つ取引先 (保存しない)。"""
    return Partner(closing_day=closing_day)


def test_closing_period_day_20_runs_to_next_closing():
    customer = _closing(20)
    # 出荷が 10/19〜10/29 なら、最後の 10/29 は 20 日を過ぎているので 10/21〜11/20。
    assert services.closing_period(customer, date(2026, 10, 29)) == (date(2026, 10, 21), date(2026, 11, 20))
    # 締日の当日はその期間に入り、翌日から次の期間。
    assert services.closing_period(customer, date(2026, 10, 20)) == (date(2026, 9, 21), date(2026, 10, 20))
    assert services.closing_period(customer, date(2026, 10, 21)) == (date(2026, 10, 21), date(2026, 11, 20))
    assert services.closing_period(customer, date(2026, 10, 19)) == (date(2026, 9, 21), date(2026, 10, 20))


def test_closing_period_month_end():
    customer = _closing(31)
    assert services.closing_period(customer, date(2026, 10, 19)) == (date(2026, 10, 1), date(2026, 10, 31))
    assert services.closing_period(customer, date(2026, 10, 29)) == (date(2026, 10, 1), date(2026, 10, 31))
    # 30 日の月も月末で締める。
    assert services.closing_period(customer, date(2026, 11, 30)) == (date(2026, 11, 1), date(2026, 11, 30))


def test_closing_period_blank_closing_day_is_month_end():
    customer = _closing(None)
    assert services.closing_period(customer, date(2026, 10, 29)) == (date(2026, 10, 1), date(2026, 10, 31))
    assert services.closing_period(customer, date(2026, 11, 1)) == (date(2026, 11, 1), date(2026, 11, 30))


def test_closing_period_february_month_end():
    month_end = _closing(31)
    assert services.closing_period(month_end, date(2027, 2, 10)) == (date(2027, 2, 1), date(2027, 2, 28))
    assert services.closing_period(month_end, date(2028, 2, 10)) == (date(2028, 2, 1), date(2028, 2, 29))
    assert services.closing_period(month_end, date(2027, 3, 1)) == (date(2027, 3, 1), date(2027, 3, 31))
    # 30 日締めの 2 月は 2/28 で締め、3 月の期間は 3/1 から (期間に隙間も重なりも無い)。
    day_30 = _closing(30)
    assert services.closing_period(day_30, date(2027, 2, 28)) == (date(2027, 1, 31), date(2027, 2, 28))
    assert services.closing_period(day_30, date(2027, 3, 1)) == (date(2027, 3, 1), date(2027, 3, 30))
    assert services.closing_period(day_30, date(2027, 3, 31)) == (date(2027, 3, 31), date(2027, 4, 30))


def test_closing_period_crosses_year():
    customer = _closing(20)
    assert services.closing_period(customer, date(2026, 12, 25)) == (date(2026, 12, 21), date(2027, 1, 20))
    assert services.closing_period(customer, date(2027, 1, 5)) == (date(2026, 12, 21), date(2027, 1, 20))
    assert services.closing_period(_closing(31), date(2027, 1, 5)) == (date(2027, 1, 1), date(2027, 1, 31))


@pytest.mark.django_db
def test_invoice_period_is_closing_period_of_last_shipment(user, item):
    customer = _customer(user, closing_day=20, payment_month="next", payment_day=25)
    line = _order_line(user, customer, item)
    early = _shipped(user, customer, line, "3", date(2026, 11, 19))
    late = _shipped(user, customer, line, "7", date(2026, 11, 29))

    invoice = services.create_invoice(customer, [late, early], user, issued_on=date(2026, 12, 21))

    invoice.refresh_from_db()
    # 出荷が 2 つの締め期間 (10/21〜11/20 と 11/21〜12/20) にまたがるときは、最後の出荷の期間。
    assert (invoice.period_from, invoice.period_to) == (date(2026, 11, 21), date(2026, 12, 20))
    # 支払期日は期間の終わりから決め、最後の出荷日から決めた日と同じ。
    assert invoice.due_on == date(2027, 1, 25)
    assert invoice.due_on == services.payment_due_date(customer, late.shipped_at)


# --- 作った後に、同じ締めの期間に残った出荷を知らせる (初見テスト R5・候補 C02 の 2。止めない) ---


@pytest.mark.django_db
def test_create_notes_unbilled_shipments_in_same_period(user, item):
    customer = _customer(user, closing_day=31)
    line = _order_line(user, customer, item)
    a = _shipped(user, customer, line, "2", date(2026, 11, 5))
    b = _shipped(user, customer, line, "2", date(2026, 11, 10))
    c = shipping_services.ship(line, Decimal("2"), date(2026, 11, 20), user)  # 納品書なし
    d = _shipped(user, customer, line, "2", date(2026, 12, 3))  # 次の期間
    other = _customer(user, "C002")
    other_line = _order_line(user, other, item)
    e = _shipped(user, other, other_line, "2", date(2026, 11, 10))  # 別の得意先

    invoice = services.create_invoice(customer, [a], user, issued_on=ISSUED_ON)

    assert (invoice.period_from, invoice.period_to) == (date(2026, 11, 1), date(2026, 11, 30))
    assert invoice.saved_level == "warning"
    assert invoice.saved_note == (
        f"この期間 (2026-11-01〜2026-11-30) の{customer.name}の出荷で、まだ請求書に入っていない物が 2 件あります: "
        f"{b.shipment_no}・{c.shipment_no} (発行の納品書なし)。"
    )
    assert invoice.saved_note.count("(発行の納品書なし)") == 1
    assert d.shipment_no not in invoice.saved_note
    assert e.shipment_no not in invoice.saved_note
    # 読むだけの関数を直に呼んでも、出荷日の順に B・C。
    assert list(services.unbilled_in_period(customer, date(2026, 11, 1), date(2026, 11, 30))) == [b, c]


@pytest.mark.django_db
def test_create_notes_all_billed_when_nothing_left(user, item):
    customer = _customer(user, closing_day=31)
    line = _order_line(user, customer, item)
    a = _shipped(user, customer, line, "4", date(2026, 11, 5))
    b = _shipped(user, customer, line, "6", date(2026, 11, 30))

    invoice = services.create_invoice(customer, [a, b], user, issued_on=ISSUED_ON)

    assert getattr(invoice, "saved_level", "") != "warning"
    assert invoice.saved_note == f"この期間の{customer.name}の出荷は、全部請求書に入っています。"


@pytest.mark.django_db
def test_unbilled_in_period_counts_shipments_of_cancelled_invoice(user, item):
    customer = _customer(user, closing_day=31)
    line = _order_line(user, customer, item)
    first = _shipped(user, customer, line, "3", date(2026, 11, 1))
    second = _shipped(user, customer, line, "3", date(2026, 11, 30))
    invoice = services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)
    period = (date(2026, 11, 1), date(2026, 11, 30))
    assert list(services.unbilled_in_period(customer, *period)) == []

    services.cancel_invoice(invoice, user)

    # 取消の請求書に入っていた出荷は、また請求できるので残りに入る (期間の両端の日も入る)。
    assert list(services.unbilled_in_period(customer, *period)) == [first, second]
