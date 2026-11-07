"""受注と発注の明細の金額の 1 か所 (masters.services.line_amount) と、単価の単位の値の読み口 (決め PRICE-1b)。

- 単価の単位が式 (lump) の明細は、数量を掛けずに単価を金額にする。部品の単位 (unit) は数量 × 単価。
- どちらも取引先 (空なら会社) の小計の計算方法で 1 円に丸める (R03)。
- 知らない単価の単位の値は、表に書く前に日本語の ValueError で断る。
作り物のデータだけを使う。
"""

from decimal import Decimal

import pytest

from seizou.masters import services as masters_services
from seizou.masters.services import (
    PRICE_BASIS_CHOICES,
    PRICE_BASIS_LUMP,
    PRICE_BASIS_UNIT,
    PRICE_BASIS_VALUES,
    line_amount,
    price_basis_text,
    refuse_unknown_price_basis,
)


def _partner(user, code, rounding):
    return masters_services.create_partner(
        {"code": code, "name": f"作り物の得意先 {code}", "role": "customer", "rounding": rounding},
        user,
    )


@pytest.mark.django_db
def test_lump_amount_is_unit_price_and_unit_amount_is_qty_times_price(user):
    partner = _partner(user, "C001", "round")
    assert line_amount(partner, Decimal("300"), Decimal("250")) == Decimal("75000")
    assert line_amount(partner, Decimal("300"), Decimal("250"), PRICE_BASIS_UNIT) == Decimal("75000")
    # 式は数量に依らない (数量 1 と 300 で同じ)。
    for qty in ("1", "300"):
        assert line_amount(partner, Decimal(qty), Decimal("50000"), "lump") == Decimal("50000"), qty


@pytest.mark.django_db
def test_lump_amount_is_rounded_by_partner(user):
    floor = _partner(user, "C001", "floor")
    half_up = _partner(user, "C002", "round")
    assert line_amount(floor, Decimal("300"), Decimal("1000.5"), "lump") == Decimal("1000")
    assert line_amount(half_up, Decimal("300"), Decimal("1000.5"), "lump") == Decimal("1001")
    # 部品の単位の 3 × 41.5 = 124.5 は、今の R03 の試験 (tests/test_r03_amount.py) と同じ値。
    expected = {"round": Decimal("125"), "ceil": Decimal("125"), "floor": Decimal("124")}
    for number, (rounding, amount) in enumerate(expected.items(), start=3):
        partner = _partner(user, f"C00{number}", rounding)
        assert line_amount(partner, Decimal("3"), Decimal("41.5")) == amount, rounding


def test_refuse_unknown_price_basis():
    assert refuse_unknown_price_basis("unit") == "unit"
    assert refuse_unknown_price_basis("lump") == "lump"
    for value in ("set", "", None):
        with pytest.raises(ValueError) as caught:
            refuse_unknown_price_basis(value)
        assert str(caught.value).startswith("保存できません。単価の単位"), value


def test_price_basis_names_are_read_from_masters_services():
    assert PRICE_BASIS_VALUES == {"unit", "lump"}
    assert (PRICE_BASIS_UNIT, PRICE_BASIS_LUMP) == ("unit", "lump")
    assert dict(PRICE_BASIS_CHOICES) == {"unit": "部品の単位", "lump": "式"}
    assert price_basis_text("lump", "個") == "式"
    assert price_basis_text("unit", "kg") == "kg"
    assert price_basis_text("unit", "") == "個"
