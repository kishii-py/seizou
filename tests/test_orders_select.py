"""受注の明細の候補の口 (orders:orderline_select) と、残の数を足す annotate_remaining。

候補の口は core の BaseSelectView の形 ({"items": [{"id", "text"}], "more": 真偽}) で 1 ページ 30 件を返す。
残が 0 以下の明細と、取消の受注の明細は出さない。出荷の入力が使う。
GET の for=workorder (指示書の発行) では、残が 0 以下の明細も出し、字に残りを付けない。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

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
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def plate(user):
    return masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)


def _line(item, qty):
    return {"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}


def _select(client, **params):
    response = client.get(reverse("orders:orderline_select"), params)
    assert response.status_code == 200
    return response.json()


@pytest.mark.django_db
def test_orderline_select_requires_login(client):
    response = client.get(reverse("orders:orderline_select"))
    assert response.status_code == 302


@pytest.mark.django_db
def test_orderline_select_lists_lines_with_remaining(user, logged_in, customer, shaft, plate):
    order = services.create_order(customer, [_line(shaft, "10"), _line(plate, "5")], user, order_date=ORDER_DATE)
    first, second = order.lines.order_by("line_no")
    shipping_services.ship(first, Decimal("4"), SHIPPED_AT, user)

    data = _select(logged_in)
    assert data["more"] is False
    assert [row["id"] for row in data["items"]] == [first.pk, second.pk]
    # 字は「受注番号-行番号 品番 品名 / 得意先 残り n」(得意先の注文番号が空なら括弧を出さない)
    assert data["items"][0]["text"] == f"{order.order_no}-1 T-100 作り物の軸 / 作り物の得意先 残り 6"
    assert data["items"][1]["text"] == f"{second} / 作り物の得意先 残り 5"


@pytest.mark.django_db
def test_orderline_select_excludes_cancelled_and_fully_shipped(user, logged_in, customer, shaft, plate):
    order = services.create_order(customer, [_line(shaft, "10"), _line(plate, "5")], user, order_date=ORDER_DATE)
    first, second = order.lines.order_by("line_no")
    shipping_services.ship(first, Decimal("10"), SHIPPED_AT, user)  # 残 0
    shipping_services.ship(second, Decimal("6"), SHIPPED_AT, user)  # 残 -1 (受注より多い出荷)
    cancelled = services.create_order(customer, [_line(shaft, "3")], user, order_date=ORDER_DATE)
    services.cancel_order(cancelled, user)
    left = services.create_order(customer, [_line(plate, "2")], user, order_date=ORDER_DATE)

    data = _select(logged_in)
    assert [row["id"] for row in data["items"]] == [left.lines.get().pk]


@pytest.mark.django_db
def test_orderline_select_for_workorder_keeps_shipped_lines(user, logged_in, customer, shaft, plate):
    """指示書の発行の欄 (GET の for=workorder) は、出荷し終えた明細も出し、取消の受注の明細は出さない。字に残りを付けない (初見テスト R5 の候補 C92)。"""
    order = services.create_order(customer, [_line(shaft, "10"), _line(plate, "5")], user, order_date=ORDER_DATE)
    first, second = order.lines.order_by("line_no")
    shipping_services.ship(first, Decimal("10"), SHIPPED_AT, user)  # 残 0
    cancelled = services.create_order(customer, [_line(shaft, "3")], user, order_date=ORDER_DATE)
    services.cancel_order(cancelled, user)

    rows = _select(logged_in, **{"for": "workorder"})["items"]
    assert [row["id"] for row in rows] == [first.pk, second.pk]
    assert cancelled.lines.get().pk not in [row["id"] for row in rows]
    texts = [row["text"] for row in rows]
    assert texts == [str(first), str(second)]
    assert all(" / " not in text and "残り" not in text for text in texts)

    # for を付けない時 (出荷の欄) は今どおり残のある明細だけ
    assert [row["id"] for row in _select(logged_in)["items"]] == [second.pk]


@pytest.mark.django_db
def test_orderline_select_search_and_paging(user, logged_in, customer, shaft, plate):
    many = services.create_order(customer, [_line(shaft, "1") for _ in range(31)], user, order_date=ORDER_DATE)
    other = services.create_order(customer, [_line(plate, "1")], user, order_date=ORDER_DATE)

    first_page = _select(logged_in, q="T-100")
    assert len(first_page["items"]) == 30
    assert first_page["more"] is True
    second_page = _select(logged_in, q="T-100", page="2")
    assert len(second_page["items"]) == 1
    assert second_page["more"] is False
    ids = {row["id"] for row in first_page["items"] + second_page["items"]}
    assert ids == set(many.lines.values_list("pk", flat=True))

    # 品名と受注番号でも探せる。数でないページは 1 として扱う。
    assert [row["id"] for row in _select(logged_in, q="作り物の板")["items"]] == [other.lines.get().pk]
    assert [row["id"] for row in _select(logged_in, q=other.order_no, page="x")["items"]] == [other.lines.get().pk]
    assert _select(logged_in, q="無い字")["items"] == []


@pytest.mark.django_db
def test_annotate_remaining_matches_remaining_qty(user, customer, shaft, plate):
    order = services.create_order(
        customer, [_line(shaft, "10"), _line(plate, "5"), _line(shaft, "3")], user, order_date=ORDER_DATE
    )
    first, second, third = order.lines.order_by("line_no")
    shipping_services.ship(first, Decimal("4"), SHIPPED_AT, user)
    shipping_services.ship(first, Decimal("2.5"), SHIPPED_AT, user)
    shipping_services.ship(second, Decimal("7"), SHIPPED_AT, user)

    rows = services.annotate_remaining(OrderLine.objects.filter(order=order)).order_by("line_no")
    assert len(rows) == 3
    for row in rows:
        assert row.shipped_total == services.shipped_qty(row)
        assert row.remaining_total == services.remaining_qty(row)
    assert [row.remaining_total for row in rows] == [Decimal("3.5"), Decimal("-2"), Decimal("3")]


@pytest.mark.django_db
def test_orderline_select_finds_by_customer_and_customer_order_no(user, logged_in, customer, shaft, plate):
    """得意先の名前・取引先コード・得意先の注文番号でも探せる (初見テスト R4 2-2)。字に得意先と注文番号が出て、残りは末尾のまま。"""
    other_customer = masters_services.create_partner(
        {"code": "C002", "name": "作り物の別の得意先", "role": "customer"}, user
    )
    mine = services.create_order(
        customer, [_line(shaft, "3")], user, order_date=ORDER_DATE, customer_order_no="PO-7781"
    )
    other = services.create_order(other_customer, [_line(plate, "2")], user, order_date=ORDER_DATE)
    mine_line = mine.lines.get()
    other_line = other.lines.get()

    by_order_no = _select(logged_in, q="PO-7781")["items"]
    assert [row["id"] for row in by_order_no] == [mine_line.pk]
    assert by_order_no[0]["text"] == f"{mine.order_no}-1 T-100 作り物の軸 / 作り物の得意先 (注文番号 PO-7781) 残り 3"

    assert [row["id"] for row in _select(logged_in, q="作り物の別の得意先")["items"]] == [other_line.pk]
    assert [row["id"] for row in _select(logged_in, q="C002")["items"]] == [other_line.pk]


@pytest.mark.django_db
def test_orderline_text_without_annotation_stays_short(user, customer, shaft):
    """集計の無い明細 (詳細の見出しなど) の字は得意先を足さない今の形のまま。"""
    order = services.create_order(customer, [_line(shaft, "1")], user, order_date=ORDER_DATE, customer_order_no="PO-1")

    assert str(order.lines.get()) == f"{order.order_no}-1 T-100 作り物の軸"
