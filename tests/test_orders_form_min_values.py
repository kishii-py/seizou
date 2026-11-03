"""受注の明細の数量と単価は 0 より大きい (R02) を、保存の前に欄の横で案内する (初見テスト 2 回目の所見)。"""

import pytest

from seizou.orders.forms import OrderLineForm


@pytest.mark.django_db
def test_zero_qty_and_price_are_field_errors():
    form = OrderLineForm(data={"item": "", "qty": "0", "unit_price": "0"})

    assert not form.is_valid()
    assert form.errors["qty"] == ["数量は 0 より大きい数にしてください。"]
    assert form.errors["unit_price"] == ["単価は 0 より大きい数にしてください。"]


@pytest.mark.django_db
def test_small_positive_values_pass_the_field_check():
    form = OrderLineForm(data={"item": "", "qty": "0.01", "unit_price": "0.01"})
    form.is_valid()

    assert "qty" not in form.errors and "unit_price" not in form.errors
