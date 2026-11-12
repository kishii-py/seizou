"""得意先ごとの台帳 (billing.services.customer_ledger) と、得意先ごとの残 (billing.services.with_receivables)。読むだけ。

台帳の行は、発行の請求書 (日 = 発行日・金額 = 合計)・取消でない請求書の入金 (日 = 入金日・金額 = −入金)・取消でない調整 (日 = 調整日・金額 = 調整の金額)
を日の古い順に並べ、同じ日は 請求 → 入金 → 調整 → pk の順。行ごとに、そこまでの残 (累計) を付ける。
取消の請求書・取消の入金と調整・請求書の外で受け取った入金は並べない。hide_money なら金額と残を None にする。問い合わせは 3 回まで。
with_receivables の残は台帳の残と同じ。得意先は月末締め (締日 31)。作り物のデータは全部 services で作る。
"""

from datetime import date
from decimal import Decimal

import pytest

from seizou.billing import services
from seizou.billing.models import Adjustment, Payment
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

ORDER_DATE = date(2026, 8, 1)
PartnerModel = Payment._meta.get_field("invoice").related_model._meta.get_field("customer").related_model


def _customer(user, code, name):
    return masters_services.create_partner(
        {"code": code, "name": name, "role": "customer", "closing_day": 31, "payment_month": "next", "payment_day": 25},
        user,
    )


def _line(user, customer, unit_price="1000"):
    item = masters_services.create_item({"part_no": f"T-{customer.code}", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("100"), "unit_price": Decimal(unit_price)}], user, order_date=ORDER_DATE
    )
    return order.lines.get()


def _invoice(user, customer, line, qty, shipped_at, issued_on):
    shipment = shipped(user, customer, line, str(qty), shipped_at)
    return services.create_invoice(customer, [shipment], user, issued_on=issued_on)


@pytest.fixture
def customer(user):
    return _customer(user, "C001", "作り物の得意先")


@pytest.fixture
def line(user, customer):
    return _line(user, customer)


@pytest.fixture
def book(user, customer, line):
    """9 月分 (合計 6,600) と 10 月分 (合計 11,000) と、入金・調整・取消の記録。

    同じ日 (2026-10-31) に 10 月分の発行・9 月分の入金・9 月分の調整を置き、並びを見る。
    取消の請求書 (8 月分)・取消の入金・取消の調整・請求書の外で受け取った入金は台帳に並ばない。
    """
    september = _invoice(user, customer, line, 6, date(2026, 9, 15), date(2026, 9, 30))
    october = _invoice(user, customer, line, 10, date(2026, 10, 15), date(2026, 10, 31))
    august = _invoice(user, customer, line, 3, date(2026, 8, 20), date(2026, 8, 31))
    services.cancel_invoice(august, user)
    adjustment = services.record_adjustment(september, date(2026, 10, 31), Decimal("-660"), user)
    payment = services.record_payment(september, date(2026, 10, 31), Decimal("5940"), user)
    wrong = services.record_payment(october, date(2026, 11, 2), Decimal("99999"), user)
    services.cancel_payment(wrong, user)
    wrong_adjustment = services.record_adjustment(october, date(2026, 11, 2), Decimal("-5"), user)
    services.cancel_adjustment(wrong_adjustment, user)
    services.record_payment(
        october, date(2026, 11, 25), Decimal("1000"), user, method=Payment.METHOD_E_BOND, matures_on=date(2027, 2, 28)
    )
    loose = shipped(user, customer, line, "1", date(2026, 11, 3))
    services.record_outside_payment(loose, date(2026, 11, 3), Decimal("1100"), user)
    return {"september": september, "october": october, "payment": payment, "adjustment": adjustment}


@pytest.mark.django_db
def test_rows_are_in_date_order_with_running_balance(customer, book):
    ledger = services.customer_ledger(customer)

    got = [(row["kind"], row["on"], row["amount"], row["balance"]) for row in ledger["rows"]]
    assert got == [
        ("invoice", date(2026, 9, 30), Decimal("6600"), Decimal("6600")),
        ("invoice", date(2026, 10, 31), Decimal("11000"), Decimal("17600")),
        ("payment", date(2026, 10, 31), Decimal("-5940"), Decimal("11660")),
        ("adjustment", date(2026, 10, 31), Decimal("-660"), Decimal("11000")),
        ("payment", date(2026, 11, 25), Decimal("-1000"), Decimal("10000")),
    ]
    assert ledger["billed"] == Decimal("17600")
    assert ledger["paid"] == Decimal("6940")
    assert ledger["adjusted"] == Decimal("-660")
    assert ledger["balance"] == Decimal("10000")
    rows = ledger["rows"]
    assert rows[0]["record"] == book["september"]
    assert (rows[0]["period_from"], rows[0]["period_to"]) == (date(2026, 9, 1), date(2026, 9, 30))
    assert rows[2]["record"] == book["payment"] and rows[2]["method"] == "振込" and rows[2]["matures_on"] is None
    assert rows[3]["record"] == book["adjustment"] and rows[3]["reason"] == "振込手数料"
    assert rows[4]["method"] == "電子記録債権" and rows[4]["matures_on"] == date(2027, 2, 28)


@pytest.mark.django_db
def test_hide_money_blanks_amounts(customer, book):
    ledger = services.customer_ledger(customer, hide_money=True)
    assert [row["kind"] for row in ledger["rows"]] == ["invoice", "invoice", "payment", "adjustment", "payment"]
    assert all(row["amount"] is None and row["balance"] is None for row in ledger["rows"])
    assert (ledger["billed"], ledger["paid"], ledger["adjusted"], ledger["balance"]) == (None, None, None, None)


@pytest.mark.django_db
def test_ledger_reads_with_three_queries(customer, book, django_assert_max_num_queries):
    with django_assert_max_num_queries(3):
        ledger = services.customer_ledger(customer)
        for row in ledger["rows"]:
            str(row["record"])
    assert len(ledger["rows"]) == 5


@pytest.mark.django_db
def test_with_receivables_matches_ledger(user, customer, book):
    other = _customer(user, "C002", "作り物の別の得意先")
    other_line = _line(user, other)
    other_invoice = _invoice(user, other, other_line, 2, date(2026, 10, 5), date(2026, 10, 31))
    # 入金 2 件と調整 2 件 (結び付けを並べて数えると倍になる形)
    services.record_payment(other_invoice, date(2026, 11, 5), Decimal("500"), user)
    services.record_payment(other_invoice, date(2026, 11, 6), Decimal("500"), user)
    services.record_adjustment(other_invoice, date(2026, 11, 6), Decimal("-100"), user)
    services.record_adjustment(other_invoice, date(2026, 11, 6), Decimal("300"), user, reason=Adjustment.REASON_OTHER)
    nobody = _customer(user, "C003", "作り物の請求の無い得意先")

    rows = {row.pk: row for row in services.with_receivables(PartnerModel.objects.all())}

    first = rows[customer.pk]
    assert (first.receivable_billed, first.receivable_paid, first.receivable_adjusted) == (
        Decimal("17600"), Decimal("6940"), Decimal("-660")
    )
    assert first.receivable_balance == services.customer_ledger(customer)["balance"] == Decimal("10000")
    second = rows[other.pk]
    assert (second.receivable_billed, second.receivable_paid, second.receivable_adjusted) == (
        Decimal("2200"), Decimal("1000"), Decimal("200")
    )
    assert second.receivable_balance == services.customer_ledger(other)["balance"] == Decimal("1400")
    empty = rows[nobody.pk]
    assert (empty.receivable_billed, empty.receivable_paid, empty.receivable_adjusted, empty.receivable_balance) == (0, 0, 0, 0)


@pytest.mark.django_db
def test_fee_adjustment_clears_ledger_balance(user):
    """依頼書の (a): 請求 55,000・入金 54,340・調整 −660 (振込手数料) の得意先の台帳の残は 0。"""
    customer = _customer(user, "C010", "作り物の手数料の得意先")
    line = _line(user, customer, unit_price="5000")
    invoice = _invoice(user, customer, line, 10, date(2026, 11, 5), date(2026, 11, 30))
    assert invoice.total == Decimal("55000")
    services.record_payment(invoice, date(2026, 12, 25), Decimal("54340"), user)
    services.record_adjustment(invoice, date(2026, 12, 25), Decimal("-660"), user)

    ledger = services.customer_ledger(customer)

    assert ledger["balance"] == 0
    assert [row["balance"] for row in ledger["rows"]] == [Decimal("55000"), Decimal("660"), Decimal("0")]
    (row,) = services.with_receivables(PartnerModel.objects.filter(pk=customer.pk))
    assert row.receivable_balance == 0
