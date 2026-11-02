"""R02: 数量と単価は 0 より大きい (受注の明細の数量と単価)。services と DB の制約で守る。"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import Order, OrderLine

ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer", "rounding": "round"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user
    )


def _line(item, qty, unit_price):
    return {"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price)}


@pytest.mark.django_db
def test_zero_or_negative_qty_is_refused(user, customer, item):
    for qty in ("0", "-1"):
        # 1 行目は正しく、2 行目だけが決まりに当たる。受注ごと作られない。
        with pytest.raises(RuleViolation) as caught:
            services.create_order(
                customer,
                [_line(item, "2", "100"), _line(item, qty, "100")],
                user,
                order_date=ORDER_DATE,
            )
        assert caught.value.code == "R02"
        assert caught.value.message.startswith("数量と単価は 0 より大きい。")
    assert Order.objects.count() == 0
    assert OrderLine.objects.count() == 0


@pytest.mark.django_db
def test_zero_unit_price_is_refused(user, customer, item):
    for unit_price in ("0", "-10"):
        with pytest.raises(RuleViolation) as caught:
            services.create_order(
                customer, [_line(item, "2", unit_price)], user, order_date=ORDER_DATE
            )
        assert caught.value.code == "R02"
    assert Order.objects.count() == 0


@pytest.mark.django_db
def test_update_also_checks_r02(user, customer, item):
    order = services.create_order(customer, [_line(item, "2", "100")], user, order_date=ORDER_DATE)
    line = order.lines.get()

    with pytest.raises(RuleViolation) as caught:
        services.update_order(order, {}, [{"id": line.pk, "qty": Decimal("0")}], user)
    assert caught.value.code == "R02"

    # 足す明細も同じ決まりで見る。
    with pytest.raises(RuleViolation) as caught:
        services.update_order(
            order, {}, [{"id": line.pk}, _line(item, "1", "0")], user
        )
    assert caught.value.code == "R02"

    line.refresh_from_db()
    assert line.qty == Decimal("2")
    assert order.lines.count() == 1


@pytest.mark.django_db
def test_db_constraint_backs_r02(user, customer, item):
    order = services.create_order(customer, [_line(item, "2", "100")], user, order_date=ORDER_DATE)

    # services を通さずに表へ直接書いても、DB の制約が止める。
    with pytest.raises(IntegrityError), transaction.atomic():
        OrderLine.objects.create(
            order=order,
            line_no=2,
            item=item,
            qty=Decimal("0"),
            unit_price=Decimal("100"),
            amount=Decimal("0"),
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        OrderLine.objects.create(
            order=order,
            line_no=3,
            item=item,
            qty=Decimal("1"),
            unit_price=Decimal("0"),
            amount=Decimal("0"),
        )
    assert order.lines.count() == 1
