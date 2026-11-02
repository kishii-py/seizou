"""出荷済みの数と残の数 (01 の 10.4 の shipped_qty・remaining_qty)。列に持たず、出荷から集計する。"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.core.checks import tools
from seizou.masters import services as masters_services
from seizou.orders import services


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.mark.django_db
def test_shipped_and_remaining_without_shipping_app(user):
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸"}, user)
    order = services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=date(2026, 11, 2),
    )
    line = order.lines.get()

    # 出荷の表が無い間 (と、出荷の無い明細) は、出荷済みが 0 で残は受注の数。
    assert services.shipped_qty(line) == Decimal("0")
    assert services.remaining_qty(line) == Decimal("10")
    assert isinstance(services.shipped_qty(line), Decimal)
    assert isinstance(services.remaining_qty(line), Decimal)

    # 検査の層の道具は同じ関数を呼ぶ。
    assert tools.shipped_qty(line) == services.shipped_qty(line)
    assert tools.remaining_qty(line) == services.remaining_qty(line)
