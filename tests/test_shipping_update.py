"""出荷を直す関数 (shipping.services.update_shipment)。

直すのは数量と出荷日と備考の 3 列だけ。直した後に受注の状態を決め直す。
0 は R02、取消の受注は R05、分納を許さない設定ではこの出荷を除いた残と同じ数だけ。
受注より多い数は services では止めない (検査の層の R01 が画面の前に見る)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.models import Shipment

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
NEW_SHIPPED_AT = date(2026, 11, 6)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def line(user):
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.mark.django_db
def test_update_changes_three_columns_only(user, line):
    shipment = services.ship(line, Decimal("4"), SHIPPED_AT, user, note="作り物の備考")
    number = shipment.shipment_no

    result = services.update_shipment(shipment, "3", NEW_SHIPPED_AT, user, note="数を直した")

    assert isinstance(result, Shipment)
    result.refresh_from_db()
    assert result.pk == shipment.pk
    assert result.qty == Decimal("3")
    assert result.shipped_at == NEW_SHIPPED_AT
    assert result.note == "数を直した"
    assert result.shipment_no == number
    assert result.order_line_id == line.pk
    assert result.delivery_note is None
    assert Shipment.objects.count() == 1


@pytest.mark.django_db
def test_update_refreshes_order_status(user, line):
    shipment = services.ship(line, Decimal("10"), SHIPPED_AT, user)
    line.order.refresh_from_db()
    assert line.order.status == "shipped"

    services.update_shipment(shipment, Decimal("4"), SHIPPED_AT, user)

    line.order.refresh_from_db()
    assert line.order.status == "partial"
    assert orders_services.remaining_qty(line) == Decimal("6")


@pytest.mark.django_db
def test_update_zero_is_r02(user, line):
    shipment = services.ship(line, Decimal("4"), SHIPPED_AT, user)

    with pytest.raises(RuleViolation) as caught:
        services.update_shipment(shipment, 0, SHIPPED_AT, user)
    assert caught.value.code == "R02"
    shipment.refresh_from_db()
    assert shipment.qty == Decimal("4")


@pytest.mark.django_db
def test_update_on_cancelled_order_is_r05(user, line):
    shipment = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    orders_services.cancel_order(line.order, user)

    with pytest.raises(RuleViolation) as caught:
        services.update_shipment(shipment, Decimal("3"), SHIPPED_AT, user)
    assert caught.value.code == "R05"
    assert "取消した受注の出荷は直せません。" in caught.value.message
    shipment.refresh_from_db()
    assert shipment.qty == Decimal("4")


@pytest.mark.django_db
def test_update_without_partial_shipment_needs_remaining(settings, user, line):
    shipment = services.ship(line, Decimal("10"), SHIPPED_AT, user)
    settings.SEIZOU = {"partial_shipment": False}

    with pytest.raises(RuleViolation) as caught:
        services.update_shipment(shipment, Decimal("4"), SHIPPED_AT, user)
    assert caught.value.code == "分納"
    assert caught.value.message.startswith("この会社は分納を許していません (設定「分納」)。")
    assert "10" in caught.value.message
    shipment.refresh_from_db()
    assert shipment.qty == Decimal("10")

    # この出荷を除いた残 (10) と同じ数なら通る
    result = services.update_shipment(shipment, Decimal("10"), NEW_SHIPPED_AT, user)
    assert result.shipped_at == NEW_SHIPPED_AT


@pytest.mark.django_db
def test_update_over_order_is_saved_by_services(user, line):
    shipment = services.ship(line, Decimal("10"), SHIPPED_AT, user)

    services.update_shipment(shipment, Decimal("12"), SHIPPED_AT, user)

    shipment.refresh_from_db()
    assert shipment.qty == Decimal("12")
    assert orders_services.remaining_qty(line) == Decimal("-2")
    line.order.refresh_from_db()
    assert line.order.status == "shipped"
