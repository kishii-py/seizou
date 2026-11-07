"""出荷と受入の 1 回分の金額の 1 か所 (masters.services.delivery_amount) と、明細の数量に初めて届く回の見方 (reaches_line_qty)。

- 単価の単位が式 (lump) の明細は、全部を出し終えた回 (completes が真) だけ一式の全額、ほかの回は 0 (決め PRICE-1c)。
- 部品の単位 (unit) は今と同じ数量 × 単価を丸めた値 (completes に依らない)。
- price_basis と completes は渡し忘れると TypeError で止まる。
作り物のデータだけを使う。
"""

from decimal import Decimal

import pytest

from seizou.masters import services as masters_services
from seizou.masters.services import delivery_amount, reaches_line_qty


def _partner(user, code, rounding):
    return masters_services.create_partner(
        {"code": code, "name": f"作り物の得意先 {code}", "role": "customer", "rounding": rounding},
        user,
    )


@pytest.mark.django_db
def test_unit_amount_is_qty_times_price_regardless_of_completes(user):
    partner = _partner(user, "C001", "round")
    for completes in (False, True):
        assert delivery_amount(partner, Decimal("100"), Decimal("250"), "unit", completes=completes) == Decimal("25000")


@pytest.mark.django_db
def test_lump_amount_is_full_only_on_completing_delivery(user):
    partner = _partner(user, "C001", "round")
    for qty in ("100", "300"):
        assert delivery_amount(partner, Decimal(qty), Decimal("50000"), "lump", completes=False) == Decimal("0"), qty
    assert delivery_amount(partner, Decimal("200"), Decimal("50000"), "lump", completes=True) == Decimal("50000")


@pytest.mark.django_db
def test_lump_amount_is_rounded_by_partner(user):
    floor = _partner(user, "C001", "floor")
    half_up = _partner(user, "C002", "round")
    assert delivery_amount(floor, Decimal("300"), Decimal("1000.5"), "lump", completes=True) == Decimal("1000")
    assert delivery_amount(half_up, Decimal("300"), Decimal("1000.5"), "lump", completes=True) == Decimal("1001")


def test_reaches_line_qty():
    assert reaches_line_qty(300, 0, 100) is False
    assert reaches_line_qty(300, 100, 200) is True
    # 多い出荷も、届いた回なら真。
    assert reaches_line_qty(300, 250, 100) is True
    # 届いた後の出荷は偽。
    assert reaches_line_qty(300, 300, 10) is False
    assert reaches_line_qty(100, 0, 100) is True
    assert reaches_line_qty(Decimal("2.5"), Decimal("1.5"), Decimal("1")) is True


@pytest.mark.django_db
def test_completes_must_be_passed(user):
    partner = _partner(user, "C001", "round")
    with pytest.raises(TypeError):
        delivery_amount(partner, Decimal("1"), Decimal("1"), "unit")
