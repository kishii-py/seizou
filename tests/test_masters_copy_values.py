"""マスタの値を欄へ写す 4 つの規則 (copy_values。05 の 2 節の採用の条件)。"""

from decimal import Decimal

from seizou.masters import services
from seizou.masters.models import Item, Partner


def test_copy_values_four_rules():
    item = Item(part_no="T-100", name="作り物の軸", unit="個", standard_price=Decimal("120.50"))

    # 規則 1: マスタに値が無ければ何もしない。
    bare = Item(part_no="T-200", name="作り物の板", unit="", standard_price=None)
    assert services.copy_values(bare, {"unit": "", "unit_price": ""}, set()) == ({}, [])

    # 規則 2: 今の値と同じなら知らせない。
    assert services.copy_values(item, {"unit": "個", "unit_price": "120.50"}, set()) == ({}, [])

    # 規則 3: 欄が空か、前に写した値なら写して「写した」。
    values, messages = services.copy_values(item, {"unit": "", "unit_price": ""}, set())
    assert values == {"unit": "個", "unit_price": Decimal("120.50")}
    assert messages == ["単位と単価を部品から写しました。"]
    values, messages = services.copy_values(
        item, {"unit": "本", "unit_price": "99"}, {"unit", "unit_price"}
    )
    assert values == {"unit": "個", "unit_price": Decimal("120.50")}
    assert messages == ["単位と単価を部品から写しました。"]

    # 規則 4: 人が入れた値なら残して「残した」。
    values, messages = services.copy_values(item, {"unit": "", "unit_price": "100"}, {"unit"})
    assert values == {"unit": "個"}
    assert messages == ["単位を部品から写しました。", "単価は入力のまま残しました。"]


def test_copy_values_compares_as_same_type():
    item = Item(part_no="T-100", name="作り物の軸", unit="個", standard_price=Decimal("15.4"))
    # 字の「15.4」と「15.40」と数の 15.4 を、マスタの 15.4 と同じとみなす。
    for typed in ["15.4", "15.40", Decimal("15.40"), 15.4]:
        assert services.copy_values(item, {"unit": "個", "unit_price": typed}, set()) == ({}, []), typed
    # 数に読めない字は人が入れた値として残す。
    values, messages = services.copy_values(item, {"unit": "個", "unit_price": "abc"}, set())
    assert values == {}
    assert messages == ["単価は入力のまま残しました。"]


def test_copy_values_from_partner_is_empty():
    partner = Partner(code="C001", name="作り物の得意先", role="customer", closing_day=31)
    assert services.copy_values(partner, {"unit": "", "unit_price": ""}, set()) == ({}, [])
