"""受注を直す関数 (01 の 10.4 の update_order)。"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import OrderLine

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


def _line(item, qty="2", unit_price="100"):
    return {"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price)}


@pytest.mark.django_db
def test_update_header_fields(user, customer, item):
    order = services.create_order(
        customer, [_line(item)], user, order_date=ORDER_DATE, customer_order_no="PO-0001"
    )
    order_no = order.order_no
    line = order.lines.get()

    services.update_order(
        order, {"customer_order_no": "PO-0002", "note": "急ぎの品"}, [{"id": line.pk}], user
    )
    order.refresh_from_db()
    assert order.customer_order_no == "PO-0002"
    assert order.note == "急ぎの品"
    # data に無い鍵は変わらない。
    assert order.order_no == order_no
    assert order.order_date == ORDER_DATE
    assert order.customer == customer
    assert order.status == "open"
    line.refresh_from_db()
    assert line.qty == Decimal("2")

    services.update_order(order, {"order_date": date(2026, 11, 3)}, [{"id": line.pk}], user)
    order.refresh_from_db()
    assert order.order_date == date(2026, 11, 3)
    assert order.note == "急ぎの品"


@pytest.mark.django_db
def test_update_lines_add_change_remove(user, customer, item):
    plate = masters_services.create_item(
        {"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user
    )
    order = services.create_order(
        customer, [_line(item, "1"), _line(item, "2"), _line(item, "3")], user, order_date=ORDER_DATE
    )
    first, second, third = order.lines.order_by("line_no")

    services.update_order(
        order,
        {},
        [
            {"id": first.pk, "qty": Decimal("5"), "note": "数を直した"},
            {"id": third.pk},
            _line(plate, "4", "50"),
        ],
        user,
    )

    first.refresh_from_db()
    assert first.qty == Decimal("5")
    assert first.amount == Decimal("500")
    assert first.note == "数を直した"
    assert not OrderLine.objects.filter(pk=second.pk).exists()

    lines = list(order.lines.order_by("line_no"))
    assert [line.pk for line in lines[:2]] == [first.pk, third.pk]
    added = lines[2]
    # 足した明細の行番号は、今の最大 (3) + 1。消した明細の番号は使い直さない。
    assert added.line_no == 4
    assert added.item == plate
    assert added.unit == "枚"
    assert added.amount == Decimal("200")
    line_nos = [line.line_no for line in lines]
    assert len(line_nos) == len(set(line_nos))


@pytest.mark.django_db
def test_unknown_key_is_refused(user, customer, item):
    order = services.create_order(customer, [_line(item)], user, order_date=ORDER_DATE)
    line = order.lines.get()

    with pytest.raises(ValueError, match="知らない項目"):
        services.update_order(order, {"status": "shipped"}, [{"id": line.pk}], user)
    with pytest.raises(ValueError, match="知らない項目"):
        services.update_order(order, {}, [{"id": line.pk, "color": "赤"}], user)
    with pytest.raises(ValueError, match="知らない項目"):
        services.create_order(
            customer, [{**_line(item), "id": line.pk}], user, order_date=ORDER_DATE
        )

    # ほかの受注の明細は直せない。
    other = services.create_order(customer, [_line(item)], user, order_date=ORDER_DATE)
    other_line = other.lines.get()
    with pytest.raises(ValueError, match="この受注の明細ではありません"):
        services.update_order(order, {}, [{"id": other_line.pk, "qty": Decimal("9")}], user)

    # 得意先を変えるときも、役に得意先を含む取引先だけ。
    subcontractor = masters_services.create_partner(
        {"code": "S001", "name": "作り物の外注先", "role": "subcontractor"}, user
    )
    with pytest.raises(ValueError, match="得意先ではありません"):
        services.update_order(order, {"customer": subcontractor}, [{"id": line.pk}], user)

    with pytest.raises(NotImplementedError):
        services.update_order(order, {}, [{"id": line.pk}], user, ext={"ext01": "x"})

    order.refresh_from_db()
    other_line.refresh_from_db()
    assert order.status == "open"
    assert order.customer == customer
    assert other_line.qty == Decimal("2")
    assert order.lines.count() == 1
