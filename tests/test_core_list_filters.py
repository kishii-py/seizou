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

from seizou.core import fields as company_fields
from seizou.core.system_checks import check_list_filters, list_filter_errors
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


# ---------------------------------------------------------------- N122 土台の広げ (flag・lookup の候補・会社ごとの項目・起動の検査)


def _open_only(queryset):
    """作り物の flag の条件 (出荷済でない受注だけ)。"""
    return queryset.exclude(status=Order.STATUS_SHIPPED)


def _before_third(queryset):
    """作り物の flag の条件 (受注日が 11-03 より前の受注だけ)。"""
    return queryset.filter(order_date__lt=date(2026, 11, 3))


class _FlagOrderList(BaseListView):
    model = Order
    screen_name = "作り物の flag の一覧"
    columns = ("order_no", "customer", "order_date", "status")
    filters = (
        {"name": "open", "label": "出荷の残りがある物だけ", "kind": "flag", "when": _open_only, "default": True},
        {"name": "early", "label": "11-03 より前の受注だけ", "kind": "flag", "when": _before_third},
    )
    ordering = ("order_date", "pk")
    template_name = "core/list.html"


def test_flag_filter_on_off_default_and_clear(user, orders):
    made = orders["orders"]
    opened = [made[0].pk, made[2].pk, made[4].pk]

    # 鍵が無ければ既定 (open は入、early は切)。既定のままなら「絞りを外す」は出さない
    response = call(_FlagOrderList, user)
    assert listed(response) == opened
    flag_open, flag_early = response.context_data["filters"]
    assert (flag_open["checked"], flag_open["active"]) == (True, False)
    assert (flag_early["checked"], flag_early["active"]) == (False, False)
    assert not response.context_data["filter_active"]
    body = response.render().content.decode()
    hidden = '<input type="hidden" name="f_open" value="0">'
    box = 'name="f_open" value="1" class="checkbox checkbox-sm" checked data-list-filter>出荷の残りがある物だけ</label>'
    assert hidden in body and box in body and body.index(hidden) < body.index(box)
    assert 'name="f_early" value="1" class="checkbox checkbox-sm" data-list-filter>' in body
    assert "絞りを外す" not in body

    # 0 で切れる (既定と違うので「絞りを外す」が出る)。欄から送ると 0 と 1 の両方が届き、後の 1 を読む
    response = call(_FlagOrderList, user, f_open="0")
    assert listed(response) == [order.pk for order in made]
    assert response.context_data["filters"][0]["active"]
    assert "絞りを外す" in response.render().content.decode()
    assert listed(call(_FlagOrderList, user, f_open=["0", "1"])) == opened
    assert listed(call(_FlagOrderList, user, f_open="x")) == opened  # 読めない値は既定
    assert listed(call(_FlagOrderList, user, f_open="0", f_early="1")) == [made[0].pk, made[1].pk]
    assert listed(call(_FlagOrderList, user, f_early=["0", "1"])) == [made[0].pk]

    # ページ送りの link は 0 と 1 を同じ順で持ち越し、「絞りを外す」の link は f_ を外す (= 既定に戻る)
    response = call(_FlagOrderList, user, f_open=["0", "1"], f_early="0", q="作り物")
    context = response.context_data
    assert "f_open=0&f_open=1" in context["query_without_page"]
    assert "f_open" not in context["query_without_filters"] and "f_early" not in context["query_without_filters"]
    assert "q=" in context["query_without_filters"]

    # CSV と htmx の行の部分も同じ条件
    response = call(_FlagOrderList, user, f_open="0", format="csv")
    assert response.content.decode("utf-8-sig").count("\n") == 6
    request = RequestFactory().get("/", {"f_open": "0"}, HTTP_HX_REQUEST="true")
    request.user = user
    partial = _FlagOrderList.as_view()(request).content.decode()
    assert 'id="list-filter-clear" class="pb-2" hx-swap-oob="true"' in partial and "絞りを外す" in partial
    assert 'id="list-count" class="font-medium tabular-nums text-base-content">5<' in partial


def test_flag_declaration_mistakes_raise_value_error():
    good = {"name": "open", "label": "作り物の印", "kind": "flag", "when": _open_only}
    spec = FilterSpec(Order, good)
    assert (spec.kind, spec.default, spec.params) == ("flag", False, ("f_open",))
    assert spec.q({"f_open": "1"}) is None  # flag は q() でなく apply() が when を掛ける

    with pytest.raises(ValueError, match="when"):
        FilterSpec(Order, {"name": "open", "label": "作り物の印", "kind": "flag"})
    with pytest.raises(ValueError, match="field"):
        FilterSpec(Order, {**good, "field": "status"})
    with pytest.raises(ValueError, match="label"):
        FilterSpec(Order, {"name": "open", "kind": "flag", "when": _open_only})
    with pytest.raises(ValueError, match="default"):
        FilterSpec(Order, {**good, "default": "True"})
    with pytest.raises(ValueError, match="flag の絞りだけ"):
        FilterSpec(Order, {"name": "status", "kind": "choice", "field": "status", "when": _open_only})


def test_lookup_choices_limit_candidates_and_reading(user, orders):
    made, north, south = orders["orders"], orders["north"], orders["south"]
    maker = masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)

    def customers():
        return [(partner.pk, partner.name) for partner in (north, south)]

    class _ChoiceLookupList(_OrderList):
        filters = ({"name": "customer", "label": "得意先", "kind": "lookup", "field": "customer", "choices": customers},)

    response = call(_ChoiceLookupList, user, f_customer=str(south.pk), per="20")
    assert listed(response) == [made[1].pk, made[3].pk]
    customer = response.context_data["filters"][0]
    assert customer["options"] == [(str(north.pk), north.name), (str(south.pk), south.name)]
    assert str(maker.pk) not in [value for value, _ in customer["options"]]

    # 候補に無い pk (表にはある協力企業) と数でない字は絞らない
    for value in (str(maker.pk), "abc"):
        response = call(_ChoiceLookupList, user, f_customer=value, per="20")
        assert response.context_data["paginator"].count == 5
        assert response.context_data["filters"][0]["value"] == ""


def test_company_field_filters_follow_the_registered_type(user, monkeypatch):
    monkeypatch.setattr(company_fields, "_REGISTRY", {})
    company_fields.register(
        "masters.Item",
        "fields.ItemExt",
        [
            {"label": "表面の仕上げ", "type": "choice", "choices": ["めっき", "塗装"]},
            {"label": "検査日", "type": "date"},
            {"label": "膜厚", "type": "number"},
            {"label": "材質", "type": "text"},
            {"label": "作り物の印", "type": "bool"},
            {"label": "社内の区分", "type": "choice", "choices": ["甲"], "in_list": False},
        ],
    )

    finish = FilterSpec(Item, {"name": "finish", "field": "ext__ext01"})
    assert (finish.kind, finish.label) == ("choice", "表面の仕上げ")
    assert finish.choice_values() == [("めっき", "めっき"), ("塗装", "塗装")]
    assert finish.q({"f_finish": "めっき"}).children == [("ext__ext01", "めっき")]
    assert finish.q({"f_finish": "金"}) is None

    checked = FilterSpec(Item, {"name": "checked", "label": "検査した日", "kind": "date_range", "field": "ext__ext02"})
    assert (checked.kind, checked.label) == ("date_range", "検査した日")
    q = checked.q({"f_checked_from": "2026-11-01", "f_checked_to": "2026-11-30"})
    assert q.children == [("ext__ext02__gte", date(2026, 11, 1)), ("ext__ext02__lte", date(2026, 11, 30))]

    thickness = FilterSpec(Item, {"name": "thickness", "field": "ext__ext03"})
    assert thickness.kind == "number_range"
    q = thickness.q({"f_thickness_from": "5", "f_thickness_to": "1,000"})
    assert q.children == [("ext__ext03__gte", Decimal("5")), ("ext__ext03__lte", Decimal("1000"))]

    for field, message in (
        ("ext__ext04", "絞れません"),
        ("ext__ext05", "絞れません"),
        ("ext__ext09", "登録されていません"),
    ):
        with pytest.raises(ValueError, match=message):
            FilterSpec(Item, {"name": "x", "field": field})
    with pytest.raises(ValueError, match="絞りの種類は choice"):
        FilterSpec(Item, {"name": "x", "kind": "lookup", "field": "ext__ext01"})

    # 一覧に出す会社ごとの項目のうち絞れる物は、宣言の後ろに自動で足す (同じ項目の宣言があれば足さない)
    assert [spec.name for spec in _ItemList().filter_specs()] == ["price", "ext01", "ext02", "ext03"]

    class _DeclaredExtList(_ItemList):
        filters = ({"name": "finish", "label": "仕上げ", "field": "ext__ext01"},)

    assert [spec.name for spec in _DeclaredExtList().filter_specs()] == ["finish", "ext02", "ext03"]
    body = call(_DeclaredExtList, user).render().content.decode()
    assert '<option value="めっき">めっき</option>' in body
    for name in ("f_finish", "f_ext02_from", "f_ext02_to", "f_ext03_from", "f_ext03_to"):
        assert f'name="{name}"' in body, name
    for name in ("f_ext01", "f_ext04", "f_ext05", "f_ext06"):
        assert f'name="{name}"' not in body, name


def test_list_filter_errors_find_broken_declarations():
    class _BrokenList(BaseListView):
        model = Order
        screen_name = "作り物の壊れた絞りの一覧"
        filters = (
            {"name": "open", "label": "作り物の印", "kind": "flag"},
            {"name": "status", "kind": "multi", "field": "status"},
            {"name": "nothing", "kind": "choice", "field": "no_such_field"},
            {"name": "ordered", "label": "受注日", "kind": "date_range", "field": "order_date"},
        )

    errors = list_filter_errors([_BrokenList])
    assert [error.id for error in errors] == ["seizou.E004"] * 3
    for error, name in zip(errors, ("open", "status", "nothing"), strict=True):
        assert f"_BrokenList の filters の絞り {name} を読めません" in error.msg
        assert error.obj is _BrokenList and error.hint
    assert list_filter_errors([_OrderList, _ItemList, _FlagOrderList, object]) == []
    # URL の木にある一覧の宣言 (N117〜N121 の絞り) は全部読める
    assert check_list_filters() == []
