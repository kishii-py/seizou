"""出荷を作る関数 (01 の 10.4 の ship)。R02・R05 の出荷の側と、受注より多い出荷を services で止めないこと。

R02・R05 の受注の側のテストは tests/test_r02_*・tests/test_r05_* にある。
受注より多い出荷のポカよけは検査の層 (core) のテストが見る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.models import Shipment

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
    item = masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user
    )
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )


@pytest.fixture
def line(order):
    return order.lines.get()


@pytest.mark.django_db
def test_ship_creates_shipment_and_updates_aggregates(user, line):
    first = services.ship(line, Decimal("4"), SHIPPED_AT, user, note="作り物の備考")
    second = services.ship(line, 6, SHIPPED_AT, user)

    assert isinstance(first, Shipment)
    first.refresh_from_db()
    assert first.shipment_no.startswith("SH-")
    assert first.shipment_no != second.shipment_no
    assert first.order_line == line
    assert first.qty == Decimal("4")
    assert first.shipped_at == SHIPPED_AT
    assert first.note == "作り物の備考"
    assert first.delivery_note is None

    assert orders_services.shipped_qty(line) == Decimal("10")
    assert orders_services.remaining_qty(line) == Decimal("0")


@pytest.mark.django_db
def test_zero_or_negative_qty_is_refused(user, line):
    for qty in (Decimal("0"), Decimal("-1")):
        with pytest.raises(RuleViolation) as caught:
            services.ship(line, qty, SHIPPED_AT, user)
        assert caught.value.code == "R02"
        assert caught.value.message.startswith("数量と単価は 0 より大きい。")
    assert Shipment.objects.count() == 0

    # services を通さずに表へ直接書いても、DB の制約が止める。
    with pytest.raises(IntegrityError), transaction.atomic():
        Shipment.objects.create(
            shipment_no="SH-TEST-1", order_line=line, qty=Decimal("0"), shipped_at=SHIPPED_AT
        )


@pytest.mark.django_db
def test_cancelled_order_cannot_be_shipped(user, order, line):
    orders_services.cancel_order(order, user)

    with pytest.raises(RuleViolation) as caught:
        services.ship(line, Decimal("4"), SHIPPED_AT, user)
    assert caught.value.code == "R05"
    assert caught.value.message.startswith("取消した受注には、出荷も指示書も作れない。")
    assert Shipment.objects.count() == 0


@pytest.mark.django_db
def test_over_shipment_is_not_stopped_by_services(settings, user, line):
    # 受注より多い出荷を止める設定でも、services は止めない (止めるのは検査の層)。
    settings.SEIZOU = {"over_shipment": "stop"}

    shipment = services.ship(line, Decimal("11"), SHIPPED_AT, user)

    assert shipment.qty == Decimal("11")
    assert orders_services.shipped_qty(line) == Decimal("11")
    assert orders_services.remaining_qty(line) == Decimal("-1")


@pytest.mark.django_db
def test_ship_advances_order_status(user, order, line):
    services.ship(line, Decimal("4"), SHIPPED_AT, user)
    order.refresh_from_db()
    assert order.status == "partial"

    services.ship(line, Decimal("6"), SHIPPED_AT, user)
    order.refresh_from_db()
    # ship は出荷を作った後に orders.services.refresh_status を呼び、受注の状態を進める。
    assert order.status == "shipped"
