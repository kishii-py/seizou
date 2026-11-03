"""受注を作る関数 (01 の 10.4 の create_order)。"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import Order

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


def _one_line(item):
    return [{"item": item, "qty": Decimal("2"), "unit_price": Decimal("100")}]


@pytest.mark.django_db
def test_create_order_with_two_lines(user, customer, item):
    plate = masters_services.create_item(
        {"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user
    )
    order = services.create_order(
        customer,
        [
            {
                "item": item,
                "qty": Decimal("10"),
                "unit_price": Decimal("120.50"),
                "due_date": date(2026, 11, 20),
                "customer_part_no": "X-001",
            },
            {
                "item": plate,
                "qty": Decimal("4"),
                "unit": "kg",
                "unit_price": Decimal("300"),
                "note": "作り物の備考",
            },
        ],
        user,
        order_date=ORDER_DATE,
        customer_order_no="PO-0001",
        source="inbox",
    )

    assert isinstance(order, Order)
    order.refresh_from_db()
    assert order.order_no
    assert order.status == "open"
    assert order.source == "inbox"
    assert order.customer == customer
    assert order.order_date == ORDER_DATE
    assert order.customer_order_no == "PO-0001"

    first, second = order.lines.order_by("line_no")
    assert [first.line_no, second.line_no] == [1, 2]
    # 単位が辞書に無ければ品目から写す。あれば辞書の値。
    assert first.unit == "個"
    assert second.unit == "kg"
    assert first.amount == Decimal("1205")
    assert second.amount == Decimal("1200")
    assert first.due_date == date(2026, 11, 20)
    assert second.due_date is None
    assert first.customer_part_no == "X-001"
    assert second.customer_part_no == ""
    assert second.note == "作り物の備考"


@pytest.mark.django_db
def test_customer_must_have_customer_role(user, item):
    subcontractor = masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )
    with pytest.raises(ValueError, match="得意先ではありません"):
        services.create_order(subcontractor, _one_line(item), user, order_date=ORDER_DATE)
    assert Order.objects.count() == 0

    both = masters_services.create_partner(
        {"code": "B001", "name": "作り物の得意先と協力企業", "role": "both"}, user
    )
    order = services.create_order(both, _one_line(item), user, order_date=ORDER_DATE)
    assert order.customer == both


@pytest.mark.django_db
def test_lines_must_not_be_empty(user, customer):
    with pytest.raises(ValueError, match="明細がありません"):
        services.create_order(customer, [], user, order_date=ORDER_DATE)
    assert Order.objects.count() == 0


@pytest.mark.django_db
def test_unknown_source_is_refused(user, customer, item):
    with pytest.raises(ValueError, match="入口"):
        services.create_order(
            customer, _one_line(item), user, order_date=ORDER_DATE, source="fax"
        )
    assert Order.objects.count() == 0
    for source in ("manual", "intake", "inbox"):
        order = services.create_order(
            customer, _one_line(item), user, order_date=ORDER_DATE, source=source
        )
        assert order.source == source


@pytest.mark.django_db
def test_ext_is_not_silently_dropped(user, customer, item):
    with pytest.raises(NotImplementedError):
        services.create_order(
            customer, _one_line(item), user, order_date=ORDER_DATE, ext={"ext01": "x"}
        )
    line = {**_one_line(item)[0], "ext": {"ext01": "x"}}
    with pytest.raises(NotImplementedError):
        services.create_order(customer, [line], user, order_date=ORDER_DATE)
    assert Order.objects.count() == 0


@pytest.mark.django_db
def test_next_number_is_used(user, customer, item):
    first = services.create_order(customer, _one_line(item), user, order_date=ORDER_DATE)
    second = services.create_order(customer, _one_line(item), user, order_date=ORDER_DATE)
    assert first.order_no
    assert second.order_no
    assert first.order_no != second.order_no
