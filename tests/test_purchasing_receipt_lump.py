"""受入の金額の一式の決まり (決め PRICE-1c の「発注の受入も同じ決まり」)。

一式 (単価の単位が式) の発注の明細は、明細の受入の合計を数量の手前から数量以上にした回で一式の全額、それまでの回と
全額を載せた後の回は 0 円。部品の単位の明細は今と同じ受け入れた数 × 単価を丸めた値。
発注は送らない形 (send=False) で作る。取引先と品目は作り物。日は固定の値を渡す。
"""

from datetime import date
from decimal import Decimal

import pytest

from seizou.masters import services as masters_services
from seizou.purchasing import services

ORDER_DATE = date(2026, 10, 28)
DAY = date(2026, 11, 2)


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "rounding": "round"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, subcontractor, item, qty, unit_price, price_basis):
    order = services.create_purchase_order(
        subcontractor,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price), "price_basis": price_basis}],
        user,
        order_date=ORDER_DATE,
        send=False,
    )
    return order.lines.get()


def _amounts(user, line, *quantities):
    return [services.receive(line, Decimal(qty), DAY, user).amount for qty in quantities]


@pytest.mark.django_db
def test_lump_amount_is_full_only_on_the_completing_receipt(user, subcontractor, item):
    line = _line(user, subcontractor, item, "5", "120000", "lump")
    assert _amounts(user, line, "2", "3", "1") == [Decimal("0"), Decimal("120000"), Decimal("0")]


@pytest.mark.django_db
def test_lump_received_at_once(user, subcontractor, item):
    line = _line(user, subcontractor, item, "5", "120000", "lump")
    assert _amounts(user, line, "5") == [Decimal("120000")]


@pytest.mark.django_db
def test_unit_amount_is_unchanged(user, subcontractor, item):
    line = _line(user, subcontractor, item, "5", "33.3", "unit")
    # 2 × 33.3 = 66.6 → 67、3 × 33.3 = 99.9 → 100 (四捨五入の協力企業)。
    assert _amounts(user, line, "2", "3") == [Decimal("67"), Decimal("100")]


@pytest.mark.django_db
def test_payable_sums_the_saved_amounts(user, subcontractor, item):
    line = _line(user, subcontractor, item, "5", "120000", "lump")
    _amounts(user, line, "2", "3", "1")
    payable = services.payable_by_partner(today=DAY)
    assert payable["totals"]["this_month"] == Decimal("120000")
