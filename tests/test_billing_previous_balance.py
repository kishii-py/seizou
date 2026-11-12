"""請求書の前回の残の写し (Invoice.previous_balance・previous_balance_at。会社の設定 invoice_previous_balance)。

設定が既定 (載せない) なら、請求書を作っても前回の残は空のまま (今の帳票の字が変わらない元)。
設定が「載せる」なら、請求書を作る時に、同じ得意先の発行の請求書のうち期間の終わりがこの請求書の期間の初めより前の物の残の合計を写す。
期間が後の請求書と取消の請求書は数えない。写した後に入金を足しても、写した値は変わらない。
作り直しは作り直す時の値で写し直し (取り消した同じ期間の請求書を数えない)、締めの一括でも同じ値が写る。
得意先は月末締め (締日 31)。単価 1,000・消費税 10%。作り物のデータは全部 services で作る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone

from seizou.billing import services
from seizou.billing.models import Invoice
from seizou.core import conf
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

ORDER_DATE = date(2026, 8, 1)
TODAY = date(2026, 11, 11)
ON = {"invoice_previous_balance": True}


@pytest.fixture
def today(monkeypatch):
    """今日を 2026-11-11 に固定する (締めの一括の「締める日がまだ来ていない」の見方のため。日時は固定しない)。"""
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: TODAY)
    return TODAY


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer", "closing_day": 31, "payment_month": "next", "payment_day": 25},
        user,
    )


@pytest.fixture
def line(user, customer):
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("100"), "unit_price": Decimal("1000")}], user, order_date=ORDER_DATE
    )
    return order.lines.get()


def _invoice(user, customer, line, qty, shipped_at):
    """出荷 1 つの請求書 (発行日は出荷の月の末日)。合計 = 数量 × 1,000 × 1.1。"""
    shipment = shipped(user, customer, line, str(qty), shipped_at)
    period_from, period_to = services.closing_period(customer, shipped_at)
    return services.create_invoice(customer, [shipment], user, issued_on=period_to)


@pytest.fixture
def earlier(user, customer, line):
    """9 月分 (合計 6,600・入金 5,940・残 660) と 10 月分 (合計 11,000・入金 1,000・残 10,000)。"""
    september = _invoice(user, customer, line, 6, date(2026, 9, 15))
    services.record_payment(september, date(2026, 10, 25), Decimal("5940"), user)
    october = _invoice(user, customer, line, 10, date(2026, 10, 15))
    services.record_payment(october, date(2026, 11, 25), Decimal("1000"), user)
    assert september.balance() == Decimal("660")
    assert october.balance() == Decimal("10000")
    return september, october


def _fresh(invoice):
    return Invoice.objects.get(pk=invoice.pk)


# --- (f) 既定は載せない ---


@pytest.mark.django_db
def test_default_keeps_previous_balance_empty(settings, user, customer, line, earlier):
    settings.SEIZOU = {}
    assert conf.setting("invoice_previous_balance") is False
    november = _invoice(user, customer, line, 5, date(2026, 11, 5))
    stored = _fresh(november)
    assert stored.previous_balance is None
    assert stored.previous_balance_at is None


# --- (g) 載せる ---


@pytest.mark.django_db
def test_previous_balance_sums_earlier_invoices(settings, user, customer, line, earlier):
    settings.SEIZOU = dict(ON)
    september, _october = earlier
    # 期間が後の請求書 (12 月分) と、取消の請求書 (8 月分) は数えない
    _invoice(user, customer, line, 2, date(2026, 12, 3))
    august = _invoice(user, customer, line, 3, date(2026, 8, 20))
    services.cancel_invoice(august, user)

    before = timezone.now()
    november = _invoice(user, customer, line, 5, date(2026, 11, 5))

    stored = _fresh(november)
    assert stored.previous_balance == Decimal("10660")
    assert stored.previous_balance_at is not None and stored.previous_balance_at >= before

    # 作った後に 9 月分へ入金を足しても、写した値は変わらない
    services.record_payment(september, date(2026, 11, 30), Decimal("660"), user)
    assert _fresh(november).previous_balance == Decimal("10660")


@pytest.mark.django_db
def test_first_invoice_has_zero_previous_balance(settings, user, customer, line):
    settings.SEIZOU = dict(ON)
    first = _invoice(user, customer, line, 5, date(2026, 11, 5))
    assert _fresh(first).previous_balance == Decimal("0")


# --- (h) 作り直しと締めの一括 ---


@pytest.mark.django_db
def test_reissue_copies_value_at_reissue(settings, user, customer, line, earlier):
    settings.SEIZOU = dict(ON)
    september, _october = earlier
    november = _invoice(user, customer, line, 5, date(2026, 11, 5))
    assert _fresh(november).previous_balance == Decimal("10660")

    services.record_payment(september, date(2026, 11, 30), Decimal("660"), user)
    remade = services.reissue_invoice(november, user, issued_on=date(2026, 11, 30))

    assert _fresh(november).status == Invoice.STATUS_CANCELLED
    stored = _fresh(remade)
    assert (stored.period_from, stored.period_to) == (date(2026, 11, 1), date(2026, 11, 30))
    assert stored.previous_balance == Decimal("10000")


@pytest.mark.django_db
def test_closing_copies_same_value(settings, user, customer, line, today):
    settings.SEIZOU = dict(ON)
    september = _invoice(user, customer, line, 6, date(2026, 9, 15))
    services.record_payment(september, date(2026, 10, 25), Decimal("5940"), user)
    shipped(user, customer, line, "10", date(2026, 10, 15))

    made = services.create_closing_invoices(date(2026, 10, 31), user, issued_on=date(2026, 10, 31))

    (october,) = list(made)
    stored = _fresh(october)
    assert (stored.period_from, stored.period_to) == (date(2026, 10, 1), date(2026, 10, 31))
    assert stored.previous_balance == Decimal("660")
    assert stored.previous_balance_at is not None
