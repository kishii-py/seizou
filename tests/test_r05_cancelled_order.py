"""R05: 取消した受注には、出荷も指示書も作れない。受注の側では、取消の後の直しと 2 回目の取消を止める。"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import Order

ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def order(user):
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer", "rounding": "round"}, user
    )
    item = masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user
    )
    return services.create_order(
        customer,
        [{"item": item, "qty": Decimal("2"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )


@pytest.mark.django_db
def test_cancel_sets_status(user, order):
    services.update_order(order, {"note": "急ぎの品"}, [{"id": order.lines.get().pk}], user)

    cancelled = services.cancel_order(order, user, reason="得意先の都合で取りやめ")
    assert isinstance(cancelled, Order)
    order.refresh_from_db()
    assert order.status == "cancelled"
    assert order.note == "急ぎの品\n取消の理由: 得意先の都合で取りやめ"


@pytest.mark.django_db
def test_cancel_without_reason_keeps_note(user, order):
    services.cancel_order(order, user)
    order.refresh_from_db()
    assert order.status == "cancelled"
    assert order.note == ""


@pytest.mark.django_db
def test_cancel_reason_over_note_limit_is_refused(user, order):
    with pytest.raises(ValueError, match="500 字を超えます"):
        services.cancel_order(order, user, reason="長" * 500)
    order.refresh_from_db()
    assert order.status == "open"
    assert order.note == ""


@pytest.mark.django_db
def test_update_after_cancel_is_refused(user, order):
    line = order.lines.get()
    services.cancel_order(order, user)

    with pytest.raises(RuleViolation) as caught:
        services.update_order(order, {"note": "直したい"}, [{"id": line.pk, "qty": Decimal("3")}], user)
    assert caught.value.code == "R05"
    assert caught.value.message == "取消した受注は直せません。直すときは、新しく受注を作ってください。"

    order.refresh_from_db()
    line.refresh_from_db()
    assert order.note == ""
    assert line.qty == Decimal("2")


@pytest.mark.django_db
def test_cancel_twice_is_refused(user, order):
    services.cancel_order(order, user, reason="1 回目")
    with pytest.raises(RuleViolation) as caught:
        services.cancel_order(order, user, reason="2 回目")
    assert caught.value.code == "R05"
    order.refresh_from_db()
    assert order.note == "取消の理由: 1 回目"
