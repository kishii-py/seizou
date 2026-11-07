"""桁 (指数) の上限の無い数の字を受けないこと (Claude Security の走査の候補 11-07 の 17・30)。

意図: 「1E+999999999」のような 12 字の数の字は Decimal には読めるが、小数点の形の字 (f"{n:f}") にすると 10 億桁になり、
記憶を使い切る。注文書の読み取り (intake の _number_text) と単価表から選ぶ小窓の GET の qty (masters の _pick_qty) が、
1 か所の関数 (seizou.core.seeds.decimal_fits) で桁を見て、超える値を今の断り方 (数の形ではありません・数量なし) で断る。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from seizou.core import seeds
from seizou.intake import services as intake_services
from seizou.masters.templatetags import masters_prices

#: 小数点の形の字にすると大きくなりすぎる数の字 (桁の上限の外)
TOO_WIDE = ["1E+20", "-1E+20", "1E-20", "12345678901234567", "0.00000000001"]
#: 記憶を使い切る大きさの数の字 (直す前は字にする所で止まらない。直した後は桁を見る所で断る)
HUGE = ["1E+999999999", "1E-999999999", "9E+99999999"]


@pytest.mark.parametrize("text", ["0", "1", "-5", "12.50", "480.000000", "1234567890123456", "0.0000000001", "1E+3"])
def test_decimal_fits_accepts_numbers_that_fit(text):
    assert seeds.decimal_fits(Decimal(text)) is True


@pytest.mark.parametrize("text", [*TOO_WIDE, *HUGE, "NaN", "Infinity", "-Infinity"])
def test_decimal_fits_refuses_numbers_beyond_digits(text):
    assert seeds.decimal_fits(Decimal(text)) is False


def test_decimal_fits_refuses_non_decimal():
    assert seeds.decimal_fits("1") is False
    assert seeds.decimal_fits(1) is False


@pytest.mark.parametrize("text", [*TOO_WIDE, *HUGE])
def test_intake_number_text_refuses_numbers_beyond_digits(text):
    with pytest.raises(ValueError, match="数量が数の形ではありません。"):
        intake_services._number_text(text, " 1 行目の", "数量")


def test_intake_number_text_keeps_numbers_that_fit():
    assert intake_services._number_text("12.50", " 1 行目の", "単価") == "12.50"
    assert intake_services._number_text(" 1E+3 ", " 1 行目の", "数量") == "1000"
    assert intake_services._number_text(300, " 1 行目の", "数量") == "300"


@pytest.mark.parametrize("text", [*TOO_WIDE, *HUGE])
def test_price_pick_qty_drops_numbers_beyond_digits(text):
    assert masters_prices._pick_qty(text) is None


def test_price_pick_qty_keeps_numbers_that_fit():
    assert masters_prices._pick_qty("１，２００") == Decimal("1200")
    assert masters_prices._pick_qty("0.5") == Decimal("0.5")
    assert masters_prices._pick_qty("-1") is None
