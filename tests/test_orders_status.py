"""受注の状態を出荷済みの数から決め直す関数 (orders.services.refresh_status)。

ship と update_order の終わりに呼ばれる。止めない (現物優先)。取消はそのまま。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import Order
from seizou.shipping import services as shipping_services

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def order(user):
    """明細 2 行 (数量 10 と 5) の受注。"""
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    shaft = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    plate = masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)
    return services.create_order(
        customer,
        [
            {"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100")},
            {"item": plate, "qty": Decimal("5"), "unit_price": Decimal("200")},
        ],
        user,
        order_date=ORDER_DATE,
    )


def _status(order):
    order.refresh_from_db()
    return order.status


@pytest.mark.django_db
def test_open_partial_shipped(user, order):
    first, second = order.lines.order_by("line_no")
    assert _status(order) == Order.STATUS_OPEN

    shipping_services.ship(first, Decimal("4"), SHIPPED_AT, user)
    assert _status(order) == Order.STATUS_PARTIAL

    shipping_services.ship(first, Decimal("6"), SHIPPED_AT, user)
    assert _status(order) == Order.STATUS_PARTIAL  # 2 行目がまだ

    shipping_services.ship(second, Decimal("5"), SHIPPED_AT, user)
    assert _status(order) == Order.STATUS_SHIPPED

    # 直に呼んでも同じ。返す物は取り直した Order。
    result = services.refresh_status(order, user)
    assert isinstance(result, Order)
    assert result.pk == order.pk
    assert result.status == Order.STATUS_SHIPPED


@pytest.mark.django_db
def test_order_without_lines_is_open(user, order):
    result = services.update_order(order, {}, [], user)
    assert result.status == Order.STATUS_OPEN
    assert result.lines.count() == 0


@pytest.mark.django_db
def test_cancelled_stays_cancelled(user, order):
    first = order.lines.order_by("line_no").first()
    shipping_services.ship(first, Decimal("4"), SHIPPED_AT, user)
    services.cancel_order(order, user, reason="作り物の理由")

    result = services.refresh_status(order, user)
    assert result.status == Order.STATUS_CANCELLED
    assert _status(order) == Order.STATUS_CANCELLED


@pytest.mark.django_db
def test_update_qty_refreshes_status(user, order):
    first, second = order.lines.order_by("line_no")
    shipping_services.ship(first, Decimal("4"), SHIPPED_AT, user)
    shipping_services.ship(second, Decimal("5"), SHIPPED_AT, user)
    assert _status(order) == Order.STATUS_PARTIAL

    # 1 行目の数量を出荷済みと同じ 4 に下げると、全部の明細が出荷済になる。
    result = services.update_order(
        order, {}, [{"order_line": first, "qty": Decimal("4")}, {"order_line": second}], user
    )
    assert result.status == Order.STATUS_SHIPPED
    assert _status(order) == Order.STATUS_SHIPPED

    # 数量を 8 に戻すと一部出荷に戻る。
    result = services.update_order(
        order, {}, [{"order_line": first, "qty": Decimal("8")}, {"order_line": second}], user
    )
    assert result.status == Order.STATUS_PARTIAL


@pytest.mark.django_db
def test_over_shipped_is_shipped(user, order):
    first, second = order.lines.order_by("line_no")
    shipping_services.ship(first, Decimal("12"), SHIPPED_AT, user)
    shipping_services.ship(second, Decimal("5"), SHIPPED_AT, user)
    assert _status(order) == Order.STATUS_SHIPPED
