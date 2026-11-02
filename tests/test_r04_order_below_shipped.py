"""R04: 出荷済みより少ない数量に、受注の明細を変えたら知らせる (同梱のポカよけ。RULES.md)。

検査の層 (seizou.core.checks) の action "orders.update_order" が data["lines"] の order_line と qty を読む。
mode は会社の設定 over_shipment (stop / warn / off)。services は止めない (中心の決まりでなくポカよけ)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.core import checks
from seizou.core.exceptions import CheckViolation
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.shipping import services as shipping_services

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def order(user):
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    return services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )


@pytest.fixture
def line(user, order):
    """数量 10 のうち 4 を出荷した明細。"""
    row = order.lines.get()
    shipping_services.ship(row, Decimal("4"), SHIPPED_AT, user)
    return row


def _data(line, qty):
    return {"lines": [{"order_line": line, "qty": Decimal(qty)}]}


@pytest.mark.django_db
def test_r04_stop_raises_check_violation(settings, user, line):
    settings.SEIZOU = {"over_shipment": "stop"}
    with pytest.raises(CheckViolation) as caught:
        checks.run("orders.update_order", _data(line, "3"), user)
    assert caught.value.code == "R04"
    assert "出荷済みの数" in caught.value.message


@pytest.mark.django_db
def test_r04_warn_returns_warning(settings, user, line):
    settings.SEIZOU = {"over_shipment": "warn"}
    warnings = checks.run("orders.update_order", _data(line, "3"), user)
    assert len(warnings) == 1
    assert warnings[0].code == "R04"
    assert "出荷済みの数" in warnings[0].message


@pytest.mark.django_db
def test_r04_off_is_silent(settings, user, line):
    settings.SEIZOU = {"over_shipment": "off"}
    assert checks.run("orders.update_order", _data(line, "3"), user) == []


@pytest.mark.django_db
def test_r04_not_triggered_at_or_above_shipped(settings, user, line):
    settings.SEIZOU = {"over_shipment": "stop"}
    assert checks.run("orders.update_order", _data(line, "4"), user) == []
    assert checks.run("orders.update_order", _data(line, "10"), user) == []


@pytest.mark.django_db
def test_services_do_not_stop_r04(settings, user, order, line):
    # 止める設定でも、update_order を直に呼ぶと 3 に変えられる (止めるのは検査の層)。
    settings.SEIZOU = {"over_shipment": "stop"}
    result = services.update_order(order, {}, [{"order_line": line, "qty": Decimal("3")}], user)
    line.refresh_from_db()
    assert line.qty == Decimal("3")
    assert services.shipped_qty(line) == Decimal("4")
    assert services.remaining_qty(line) == Decimal("-1")
    assert result.status == "shipped"
