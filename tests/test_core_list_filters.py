"""一覧の絞り (BaseListView.filters と FilterSpec。N117。本人の決め 11-04 15:0x)。

試験の中で BaseListView を継承した画面を作り、RequestFactory で直に呼ぶ (tests/test_core_list_columns.py と同じ形)。
GET の名前は f_<name> (期間と数の範囲は f_<name>_from と f_<name>_to)。空と読めない値は絞らない。名前は作り物だけ。
"""

import csv
import io
import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from seizou.core.views import BaseListView, FilterSpec
from seizou.masters import services as masters_services
from seizou.masters.models import Item
from seizou.orders import services as orders_services
from seizou.orders.models import Order


class _OrderList(BaseListView):
    model = Order
    screen_name = "作り物の受注の一覧"
    columns = ("order_no", "customer", "order_date", "status")
    search_fields = ("order_no", "customer__name")
    filters = (
        {"name": "status", "label": "状態", "kind": "choice", "field": "status"},
        {"name": "ordered", "label": "受注日", "kind": "date_range", "field": "order_date"},
        {"name": "customer", "label": "得意先", "kind": "lookup", "field": "customer"},
    )
    ordering = ("order_date", "pk")
    template_name = "core/list.html"
    paginate_by = 2


class _ItemList(BaseListView):
    model = Item
    screen_name = "作り物の部品の一覧"
    columns = ("part_no", "name", "standard_price")
    filters = ({"name": "price", "label": "単価", "kind": "number_range", "field": "standard_price"},)
    ordering = ("part_no",)
    template_name = "core/list.html"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def orders(user):
    """受注 5 件 (受注日は 11-01〜11-05)。得意先は北が 1・3・5 件目、南が 2・4 件目。2 件目と 4 件目は出荷済。"""
    north = masters_services.create_partner({"code": "C001", "name": "作り物の北の得意先", "role": "customer"}, user)
    south = masters_services.create_partner({"code": "C002", "name": "作り物の南の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    made = []
    for day in range(1, 6):
        customer = north if day % 2 else south
        order = orders_services.create_order(
            customer,
            [{"item": item, "qty": Decimal("1"), "unit_price": Decimal("100")}],
            user,
            order_date=date(2026, 11, day),
        )
        made.append(order)
    Order.objects.filter(pk__in=[made[1].pk, made[3].pk]).update(status=Order.STATUS_SHIPPED)
    return {"orders": made, "north": north, "south": south}


def call(view, user, **params):
    request = RequestFactory().get("/", params)
    request.user = user
    return view.as_view()(request)


def listed(response) -> list:
    return [row["obj"].pk for row in response.context_data["rows"]]


def test_choice_filter_uses_field_choices_and_ignores_unknown_value(user, orders):
    made = orders["orders"]

    response = call(_OrderList, user, f_status="shipped", per="20")
    assert listed(response) == [made[1].pk, made[3].pk]
    assert response.context_data["paginator"].count == 2
    status = response.context_data["filters"][0]
    assert status["param"] == "f_status" and status["value"] == "shipped" and status["active"]
    assert [value for value, _ in status["options"]] == ["open", "partial", "shipped", "cancelled"]
    body = response.render().content.decode()
    assert '<option value="shipped" selected>出荷済</option>' in body
    assert 'name="f_status" data-list-filter' in body
    assert "change from:[data-list-filter]" in body
    assert "絞りを外す" in body

    # 候補に無い値と空は絞らない (知らせは出さない)
    for value in ("zzz", ""):
        response = call(_OrderList, user, f_status=value, per="20")
        assert response.context_data["paginator"].count == 5
        assert not response.context_data["filter_active"]
        assert "絞りを外す" not in response.render().content.decode()


def test_date_range_and_number_range_include_both_ends(user, orders):
    made = orders["orders"]

    response = call(_OrderList, user, f_ordered_from="2026-11-02", f_ordered_to="2026-11-04", per="20")
    assert listed(response) == [made[1].pk, made[2].pk, made[3].pk]
    ordered = response.context_data["filters"][1]
    assert (ordered["value_from"], ordered["value_to"]) == ("2026-11-02", "2026-11-04")
    assert listed(call(_OrderList, user, f_ordered_from="2026-11-04", per="20")) == [made[3].pk, made[4].pk]
    assert listed(call(_OrderList, user, f_ordered_to="2026-11-01", per="20")) == [made[0].pk]
    # 日付でない字は空と同じ (欄も空で出す)
    response = call(_OrderList, user, f_ordered_from="11/02", f_ordered_to="2026-11-01", per="20")
    assert listed(response) == [made[0].pk]
    assert response.context_data["filters"][1]["value_from"] == ""

    for part_no, price in (("P-1", "50"), ("P-2", "100"), ("P-3", "1000"), ("P-4", "1500.50")):
        Item.objects.create(part_no=part_no, name="作り物の板", standard_price=Decimal(price))
    parts = lambda response: [row["obj"].part_no for row in response.context_data["rows"]]  # noqa: E731
    assert parts(call(_ItemList, user, f_price_from="100", f_price_to="1,000")) == ["P-2", "P-3"]
    assert parts(call(_ItemList, user, f_price_from="1000")) == ["P-3", "P-4"]
    assert parts(call(_ItemList, user, f_price_from="abc", f_price_to="")) == ["P-1", "P-2", "P-3", "P-4", "T-100"]


def test_lookup_filter_lists_all_related_rows_and_ignores_unknown_pk(user, orders):
    made, north, south = orders["orders"], orders["north"], orders["south"]

    response = call(_OrderList, user, f_customer=str(south.pk), per="20")
    assert listed(response) == [made[1].pk, made[3].pk]
    customer = response.context_data["filters"][2]
    assert customer["options"] == [(str(north.pk), str(north)), (str(south.pk), str(south))]
    body = response.render().content.decode()
    assert f'<option value="{south.pk}" selected>{south}</option>' in body

    for value in ("abc", "999999"):
        response = call(_OrderList, user, f_customer=value, per="20")
        assert response.context_data["paginator"].count == 5
        assert response.context_data["filters"][2]["value"] == ""

    # 絞りどうしと検索は全部かけ合わせる (AND)
    response = call(_OrderList, user, f_customer=str(north.pk), f_ordered_from="2026-11-02", q="北", per="20")
    assert listed(response) == [made[2].pk, made[4].pk]


def test_page_sort_and_csv_links_keep_filters_and_search(user, orders):
    params = {"f_customer": str(orders["north"].pk), "q": "作り物", "sort": "-order_date"}
    response = call(_OrderList, user, **params)
    context = response.context_data
    assert context["paginator"].count == 3 and context["paginator"].num_pages == 2
    for key in ("query_without_page", "query_without_sort"):
        assert f"f_customer={orders['north'].pk}" in context[key]
        assert "q=" in context[key]
    assert "sort=-order_date" in context["query_without_page"]
    assert "f_customer" not in context["query_without_filters"]
    assert "q=" in context["query_without_filters"] and "sort=-order_date" in context["query_without_filters"]

    body = response.render().content.decode()
    hrefs = re.findall(r'href="\?([^"]*)"', body)
    kept = f"f_customer={orders['north'].pk}"
    for mark in ("page=2", "format=csv", "sort=order_no"):
        links = [href for href in hrefs if mark in href]
        assert links and all(kept in href and "q=" in href for href in links), mark
    (clear,) = [href for href in hrefs if "f_customer" not in href and "q=" in href]
    assert "sort=-order_date" in clear

    # htmx の要求 (行の部分) は「絞りを外す」も oob で差し替える
    request = RequestFactory().get("/", params, HTTP_HX_REQUEST="true")
    request.user = user
    partial = _OrderList.as_view()(request).content.decode()
    assert 'id="list-filter-clear" class="pb-2" hx-swap-oob="true"' in partial
    assert "絞りを外す" in partial
    assert 'id="list-count" class="font-medium tabular-nums text-base-content">3<' in partial


def test_csv_uses_the_same_filters(user, orders):
    made = orders["orders"]

    response = call(_OrderList, user, f_status="open", f_ordered_to="2026-11-03", format="csv")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert [row[0] for row in rows[1:]] == [made[0].order_no, made[2].order_no]
    assert response.content.decode("utf-8-sig").count("\n") == 3

    with pytest.raises(ValueError, match="知らない絞りの種類"):
        FilterSpec(Order, {"name": "status", "kind": "multi", "field": "status"})
    with pytest.raises(ValueError, match="絞りに書けない鍵"):
        FilterSpec(Order, {"name": "status", "kind": "choice", "field": "status", "multiple": True})
