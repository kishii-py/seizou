"""R03: 受注の明細の金額は数量 × 単価で、端数は会社の設定どおりに丸める。DB の側で計算しない。"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import models

from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import OrderLine

ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def item(user):
    return masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user
    )


def _customer(user, code, rounding):
    return masters_services.create_partner(
        {"code": code, "name": f"作り物の得意先 {code}", "role": "customer", "rounding": rounding},
        user,
    )


@pytest.mark.django_db
def test_amount_is_qty_times_price_rounded_by_partner(user, item):
    # 3 × 41.5 = 124.5、3 × 41.4 = 124.2。四捨五入・切上・切捨で丸め方が分かれる。
    expected = {
        "round": {"41.5": Decimal("125"), "41.4": Decimal("124")},
        "ceil": {"41.5": Decimal("125"), "41.4": Decimal("125")},
        "floor": {"41.5": Decimal("124"), "41.4": Decimal("124")},
    }
    for number, (rounding, amounts) in enumerate(expected.items(), start=1):
        customer = _customer(user, f"C00{number}", rounding)
        order = services.create_order(
            customer,
            [
                {"item": item, "qty": Decimal("3"), "unit_price": Decimal(price)}
                for price in amounts
            ],
            user,
            order_date=ORDER_DATE,
        )
        lines = list(order.lines.order_by("line_no"))
        assert [line.amount for line in lines] == list(amounts.values()), rounding
        for line in lines:
            line.refresh_from_db()
        assert [line.amount for line in lines] == list(amounts.values()), rounding


@pytest.mark.django_db
def test_amount_is_recomputed_on_update(user, item):
    round_customer = _customer(user, "C001", "round")
    floor_customer = _customer(user, "C002", "floor")
    order = services.create_order(
        round_customer,
        [{"item": item, "qty": Decimal("3"), "unit_price": Decimal("41.5")}],
        user,
        order_date=ORDER_DATE,
    )
    line = order.lines.get()
    assert line.amount == Decimal("125")

    # 単価を変えると金額を計算し直す。
    services.update_order(order, {}, [{"id": line.pk, "unit_price": Decimal("40")}], user)
    line.refresh_from_db()
    assert line.amount == Decimal("120")

    # 得意先を変えると、新しい得意先の丸め方で計算し直す。
    services.update_order(order, {}, [{"id": line.pk, "unit_price": Decimal("41.5")}], user)
    services.update_order(order, {"customer": floor_customer}, [{"id": line.pk}], user)
    line.refresh_from_db()
    assert line.amount == Decimal("124")


def test_amount_is_not_computed_by_the_db():
    field = OrderLine._meta.get_field("amount")
    assert isinstance(field, models.DecimalField)
    assert not isinstance(field, models.GeneratedField)
