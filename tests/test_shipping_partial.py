"""分納の設定 (01 の 9 節。partial_shipment = 許す (既定) / 許さない)。両方の値で回す。"""

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


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def line(user):
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user
    )
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.mark.django_db
def test_partial_shipment_allowed_by_default(user, line):
    services.ship(line, Decimal("4"), SHIPPED_AT, user)
    services.ship(line, Decimal("6"), SHIPPED_AT, user)

    assert Shipment.objects.count() == 2
    assert orders_services.remaining_qty(line) == Decimal("0")


@pytest.mark.django_db
def test_partial_shipment_disallowed_requires_full_remaining(settings, user, line):
    settings.SEIZOU = {"partial_shipment": False}

    with pytest.raises(RuleViolation) as caught:
        services.ship(line, Decimal("4"), SHIPPED_AT, user)
    assert caught.value.code == "分納"
    assert caught.value.message.startswith("保存できません。この会社は分納を許していません (設定「分納」)。")
    assert "10" in caught.value.message
    assert Shipment.objects.count() == 0

    services.ship(line, Decimal("10"), SHIPPED_AT, user)
    assert orders_services.remaining_qty(line) == Decimal("0")

    with pytest.raises(RuleViolation) as caught:
        services.ship(line, Decimal("1"), SHIPPED_AT, user)
    assert caught.value.code == "分納"
    assert Shipment.objects.count() == 1
