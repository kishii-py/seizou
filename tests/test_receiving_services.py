"""入荷の services (入荷を作る・自動の引当の数・手の引当の上限・預かり残・入荷の状態・一覧用の引当の合計)。

受注・出荷は services で作る。日は固定の値を渡す。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine
from seizou.receiving import services
from seizou.receiving.models import SOURCE_CONSIGNED, SOURCE_FREE, SOURCE_PAID, Allocation, Receiving
from seizou.shipping import services as shipping_services

ORDER_DATE = date(2026, 11, 2)
DAY1 = date(2026, 11, 3)
DAY2 = date(2026, 11, 4)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "W-100", "name": "作り物のワーク", "unit": "個"}, user)


def _line(user, customer, item, qty="10"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _data(item, customer=None, qty="10", source=SOURCE_CONSIGNED, **more):
    return {"item": item, "partner": customer, "source": source, "qty": Decimal(qty), "received_on": DAY1, **more}


def test_create_receiving_without_order_line_makes_no_allocation(user, customer, item):
    receiving = services.create_receiving(_data(item, customer, "12", SOURCE_FREE, lot="L-1", note="作り物"), user)

    assert receiving.pk is not None
    assert (receiving.item, receiving.partner, receiving.source) == (item, customer, SOURCE_FREE)
    assert receiving.qty == Decimal("12")
    assert (receiving.lot, receiving.note, receiving.created_by) == ("L-1", "作り物", user)
    assert Allocation.objects.count() == 0
    assert str(receiving) == "2026-11-03 無償支給 W-100 12"


def test_partner_is_optional(user, item):
    receiving = services.create_receiving(_data(item, None, "3", SOURCE_PAID), user)

    assert receiving.partner is None


@pytest.mark.parametrize("qty", ["0", "-1"])
def test_zero_or_negative_qty_is_refused(user, customer, item, qty):
    with pytest.raises(ValueError, match="0 より大きい数"):
        services.create_receiving(_data(item, customer, qty), user)
    assert Receiving.objects.count() == 0


def test_unknown_source_is_refused(user, customer, item):
    with pytest.raises(ValueError, match="無償支給"):
        services.create_receiving(_data(item, customer, "1", "bought"), user)
    assert Receiving.objects.count() == 0


def test_order_line_gets_one_allocation_of_received_qty(user, customer, item):
    line = _line(user, customer, item, "10")

    receiving = services.create_receiving(_data(item, customer, "4"), user, order_line=line)

    allocation = receiving.allocations.get()
    assert (allocation.order_line, allocation.qty) == (line, Decimal("4"))
    assert services.allocated_total(line) == Decimal("4")


def test_auto_allocation_stops_at_order_qty_minus_allocated(user, customer, item):
    line = _line(user, customer, item, "10")
    services.create_receiving(_data(item, customer, "7"), user, order_line=line)

    second = services.create_receiving(_data(item, customer, "5"), user, order_line=line)

    # 受注の数 10 − 既にある引当 7 = 3 だけ引き当てる (入荷の数 5 のうち 2 は引き当てない)
    assert second.allocations.get().qty == Decimal("3")
    assert services.allocated_total(line) == Decimal("10")

    third = services.create_receiving(_data(item, customer, "2"), user, order_line=line)

    # 受注の数に届いた明細には引当を作らない (入荷は残す)
    assert third.allocations.count() == 0
    assert Receiving.objects.count() == 3


def test_allocate_qty_overrides_the_auto_share(user, customer, item):
    line = _line(user, customer, item, "10")

    receiving = services.create_receiving(_data(item, customer, "8"), user, order_line=line, allocate_qty=Decimal("5"))

    assert receiving.allocations.get().qty == Decimal("5")


def test_allocate_qty_over_received_is_refused_and_rolled_back(user, customer, item):
    line = _line(user, customer, item, "10")

    with pytest.raises(ValueError, match="入荷の数量"):
        services.create_receiving(_data(item, customer, "3"), user, order_line=line, allocate_qty=Decimal("4"))
    assert Receiving.objects.count() == 0
    assert Allocation.objects.count() == 0


def test_manual_allocation_up_to_received_qty(user, customer, item):
    first = _line(user, customer, item, "6")
    second = _line(user, customer, item, "6")
    receiving = services.create_receiving(_data(item, customer, "10"), user)

    services.allocate(receiving, first, Decimal("6"), user)
    allocation = services.allocate(receiving, second, Decimal("4"), user)

    assert allocation.qty == Decimal("4")
    assert services.allocated_total(first) == Decimal("6")
    assert services.allocated_total(second) == Decimal("4")


def test_manual_allocation_over_received_qty_is_refused(user, customer, item):
    first = _line(user, customer, item, "6")
    second = _line(user, customer, item, "6")
    receiving = services.create_receiving(_data(item, customer, "10"), user)
    services.allocate(receiving, first, Decimal("6"), user)

    with pytest.raises(ValueError, match="引き当てられる残りは 4 です"):
        services.allocate(receiving, second, Decimal("5"), user)
    assert services.allocated_total(second) == Decimal("0")


def test_manual_allocation_zero_or_twice_is_refused(user, customer, item):
    line = _line(user, customer, item, "6")
    receiving = services.create_receiving(_data(item, customer, "10"), user)

    with pytest.raises(ValueError, match="0 より大きい数"):
        services.allocate(receiving, line, Decimal("0"), user)
    services.allocate(receiving, line, Decimal("2"), user)
    with pytest.raises(ValueError, match="既に引き当てています"):
        services.allocate(receiving, line, Decimal("1"), user)
    assert services.allocated_total(line) == Decimal("2")


def test_remaining_is_allocated_minus_shipped(user, customer, item):
    line = _line(user, customer, item, "10")
    assert services.remaining(line) == Decimal("0")

    services.create_receiving(_data(item, customer, "10"), user, order_line=line)
    shipping_services.ship(line, Decimal("4"), DAY2, user)

    assert services.remaining(line) == Decimal("6")


def test_receiving_state_none_partial_full(user, customer, item):
    line = _line(user, customer, item, "10")
    assert services.receiving_state(line) == services.STATE_NONE

    services.create_receiving(_data(item, customer, "4"), user, order_line=line)
    assert services.receiving_state(line) == services.STATE_PARTIAL

    services.create_receiving(_data(item, customer, "6"), user, order_line=line)
    assert services.receiving_state(line) == services.STATE_FULL


def test_annotate_allocated_adds_total_per_line(user, customer, item):
    first = _line(user, customer, item, "10")
    second = _line(user, customer, item, "10")
    services.create_receiving(_data(item, customer, "3"), user, order_line=first)
    services.create_receiving(_data(item, customer, "4"), user, order_line=first)

    totals = dict(services.annotate_allocated(OrderLine.objects.all()).values_list("pk", "allocated_total"))

    assert totals == {first.pk: Decimal("7"), second.pk: Decimal("0")}
