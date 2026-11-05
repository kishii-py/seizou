"""update_order の明細の鍵 order_line (この受注の OrderLine。id と同じ意味) と、出荷のある明細を消したとき。

検査の層の R04 が data["lines"] の order_line を読むので、services も同じ鍵を受ける。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import OrderLine
from seizou.shipping import services as shipping_services

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)


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
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(item, qty="2", unit_price="100"):
    return {"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price)}


@pytest.mark.django_db
def test_order_line_key_updates_existing_line(user, customer, item):
    order = services.create_order(customer, [_line(item, "2"), _line(item, "3")], user, order_date=ORDER_DATE)
    first, second = order.lines.order_by("line_no")

    services.update_order(
        order,
        {},
        [
            {"order_line": first, "qty": Decimal("7"), "unit_price": Decimal("150")},
            # id と order_line が同じ明細を指すときは通る。
            {"id": second.pk, "order_line": second, "note": "作り物の備考"},
        ],
        user,
    )
    rows = list(order.lines.order_by("line_no"))
    assert [row.pk for row in rows] == [first.pk, second.pk]
    assert rows[0].qty == Decimal("7")
    assert rows[0].unit_price == Decimal("150")
    assert rows[0].amount == Decimal("1050")
    assert rows[1].qty == Decimal("3")
    assert rows[1].note == "作り物の備考"


@pytest.mark.django_db
def test_order_line_of_other_order_is_refused(user, customer, item):
    order = services.create_order(customer, [_line(item, "2")], user, order_date=ORDER_DATE)
    other = services.create_order(customer, [_line(item, "5")], user, order_date=ORDER_DATE)
    mine = order.lines.get()
    theirs = other.lines.get()

    with pytest.raises(ValueError, match="この受注の明細ではありません"):
        services.update_order(order, {}, [{"order_line": theirs, "qty": Decimal("1")}], user)

    # id と order_line が違う明細を指すときも断る。
    with pytest.raises(ValueError, match="違う明細を指しています"):
        services.update_order(order, {}, [{"id": mine.pk, "order_line": theirs}], user)

    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.qty == Decimal("2")
    assert theirs.qty == Decimal("5")
    assert theirs.order_id == other.pk


@pytest.mark.django_db
def test_removing_shipped_line_is_japanese_value_error(user, customer, item):
    order = services.create_order(customer, [_line(item, "10"), _line(item, "3")], user, order_date=ORDER_DATE)
    first, second = order.lines.order_by("line_no")
    shipping_services.ship(first, Decimal("4"), SHIPPED_AT, user)

    with pytest.raises(ValueError) as caught:
        services.update_order(order, {"note": "消す前の備考"}, [{"order_line": second}], user)
    assert str(caught.value) == "保存できません。消した明細に出荷があります。数量を直すか、明細を残してください。"

    # 明細は 2 行とも残り、受注の項目も書き換わらない (同じトランザクションごと戻る)。
    assert OrderLine.objects.filter(order=order).count() == 2
    order.refresh_from_db()
    assert order.note == ""
