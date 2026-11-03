"""一覧の土台の続き 2 (N77): 外部キーの列の並び替えの向き・範囲の外の頁・金額の列の無い CSV の記録。

画面は Django の test client (force_login) か、試験の中で BaseListView を継承した画面を RequestFactory で直に呼ぶ
(tests/test_core_list_columns.py の call の形)。名前は作り物だけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.workorders import services as workorders_services

User = get_user_model()

PASSWORD = "kumo-tests-2611"

#: 外部キーの列 (表示の字の先頭の項目で並べる 3 列)。(一覧の URL の名前, 列の名前, sort_key)
FK_COLUMNS = (
    ("workorders:workorder_list", "order_line", "order_line__order__order_no"),
    ("intake:extraction_list", "order", "order__order_no"),
    ("progress:record_list", "workorder_step", "workorder_step__workorder__wo_no"),
)


@pytest.fixture
def user(db):
    return User.objects.create_user("tester", password=PASSWORD)


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="作り物の切断", sort_order=1)


def make_order(user, customer, item, order_date):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("5"), "unit_price": Decimal("100")}],
        user,
        order_date=order_date,
    )


def labels(response) -> list[str]:
    return [spec.label for spec in response.context["columns"]]


# ---------------------------------------------------------------- 1 外部キーの列の並び替えの向き


@pytest.mark.django_db
@pytest.mark.parametrize(("url_name", "column", "sort_key"), FK_COLUMNS)
def test_fk_columns_sort_by_display_key(logged_in, url_name, column, sort_key):
    url = reverse(url_name)
    plain = logged_in.get(url)
    assert plain.status_code == 200

    ascending = logged_in.get(url, {"sort": column})
    assert ascending.status_code == 200
    assert ascending.context["paginator"].object_list.query.order_by == (sort_key, "pk")
    assert labels(ascending) == labels(plain)

    descending = logged_in.get(url, {"sort": "-" + column})
    assert descending.status_code == 200
    assert descending.context["paginator"].object_list.query.order_by == ("-" + sort_key, "-pk")
    assert labels(descending) == labels(plain)


@pytest.mark.django_db
def test_workorder_list_sorts_order_line_ascending(logged_in, user, customer, shaft, cut):
    # 受注番号は作った順。受注番号の小さい方の受注日を古くする (受注の Meta.ordering = -order_date の並びでは、
    # 昇順の印で受注日の新しい = 受注番号の大きい方が先に来る形)。
    first = make_order(user, customer, shaft, date(2026, 11, 1))
    second = make_order(user, customer, shaft, date(2026, 11, 3))
    assert first.order_no < second.order_no
    for order in (first, second):
        workorders_services.issue_workorder(
            order.lines.get(), Decimal("1"), [{"process": cut, "planned_qty": 1}], user
        )

    url = reverse("workorders:workorder_list")
    ascending = [row["obj"].order_line.order.order_no for row in logged_in.get(url, {"sort": "order_line"}).context["rows"]]
    descending = [
        row["obj"].order_line.order.order_no for row in logged_in.get(url, {"sort": "-order_line"}).context["rows"]
    ]

    assert ascending == [first.order_no, second.order_no]
    assert descending == [second.order_no, first.order_no]
