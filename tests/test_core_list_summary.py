"""一覧の集計 (GET view=summary。core/summary.py と BaseListView。決め 11-04 17:5x〜18:0x・22:0x)。

軸 (g_by) は絞りの choice・lookup の項目と date_range の項目の月、量 (g_val) は件数か表の数の項目の列の合計。
絞りと検索の後の行を数える。金額の列は金額の閲覧が無い人には出さない。CSV は画面の表と同じ行。
試験の中で BaseListView を継承した画面を作り、RequestFactory で直に呼ぶ (tests/test_core_list_filters.py と同じ形)。
本物の画面は受注の一覧と注文残の盤で見る。名前は作り物だけ。
"""

import csv
import io
from datetime import date
from decimal import Decimal
from urllib.parse import quote

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from seizou.core.templatetags.seizou_charts import chart_for
from seizou.core.views import BaseListView
from seizou.masters import services as masters_services
from seizou.masters.models import Item
from seizou.orders import services as orders_services
from seizou.orders.models import Order, OrderLine
from seizou.shipping import services as shipping_services

MONEY_ON = {"access_control": {"department": "off", "write": "off", "money": "on"}}


class _OrderList(BaseListView):
    model = Order
    screen_name = "作り物の受注の一覧"
    columns = ("order_no", "customer", "order_date", "status")
    search_fields = ("order_no", "customer__name")
    filters = (
        {"name": "status", "label": "状態", "kind": "choice", "field": "status"},
        {"name": "customer", "label": "得意先", "kind": "lookup", "field": "customer"},
        {"name": "ordered", "label": "受注日", "kind": "date_range", "field": "order_date"},
        {"name": "due", "label": "納期", "kind": "date_range", "field": "lines__due_date"},
    )
    ordering = ("order_date", "pk")
    template_name = "core/list.html"


class _LineList(BaseListView):
    model = OrderLine
    screen_name = "作り物の明細の一覧"
    columns = (("order__order_no", "受注番号"), ("order__customer", "得意先"), "qty", "amount")
    filters = ({"name": "customer", "label": "得意先", "kind": "lookup", "field": "order__customer"},)
    ordering = ("order", "line_no")
    template_name = "core/list.html"


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
    """受注 5 件。北の得意先が 10-30・11-01・11-03 (数量 1・3・2)、南が 10-28・11-02 (数量 4・5。どちらも出荷済)。単価は 100。"""
    north = masters_services.create_partner({"code": "C001", "name": "作り物の北の得意先", "role": "customer"}, user)
    south = masters_services.create_partner({"code": "C002", "name": "作り物の南の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    plan = [
        (north, date(2026, 10, 30), "1"),
        (south, date(2026, 10, 28), "4"),
        (north, date(2026, 11, 1), "3"),
        (south, date(2026, 11, 2), "5"),
        (north, date(2026, 11, 3), "2"),
    ]
    made = [
        orders_services.create_order(
            customer, [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}], user, order_date=day
        )
        for customer, day, qty in plan
    ]
    Order.objects.filter(customer=south).update(status=Order.STATUS_SHIPPED)
    return {"orders": made, "north": north, "south": south}


def call(screen, user, headers=None, **params):
    request = RequestFactory().get("/", params, headers=headers)
    request.user = user
    return screen.as_view()(request)


def body(response) -> str:
    if hasattr(response, "render"):
        response.render()
    return response.content.decode()


def csv_rows(response) -> list[list[str]]:
    return list(csv.reader(io.StringIO(response.content.decode("utf-8").lstrip("\ufeff"))))


def pairs(response) -> list[tuple]:
    """集計の行の (字, 量)。量は Decimal にそろえる (SQLite と PostgreSQL で Decimal の桁が違うため)。"""
    return [(row["label"], Decimal(str(row["value"]))) for row in response.context_data["summary"]["rows"]]


def test_status_axis_counts_rows_in_choice_order(user, orders):
    response = call(_OrderList, user, view="summary")
    text = body(response)
    summary = response.context_data["summary"]
    # 既定は最初の choice の軸 (状態) × 件数。並びは候補の順 (未出荷 → 出荷済)
    assert summary["axis"].key == "status" and summary["measure"].key == "count"
    assert pairs(response) == [("未出荷", 3), ("出荷済", 2)]
    assert summary["total"] == 5
    assert [str(row["share"]) for row in summary["rows"]] == ["60.0", "40.0"]
    # 軸の候補: 状態・得意先・受注日の月。納期は明細 (多の側) を辿るので軸にしない
    assert [axis.key for axis in summary["axes"]] == ["status", "customer", "ordered:month"]
    assert [measure.key for measure in summary["measures"]] == ["count"]
    # 表の行は作らず、集計の表と図を描く。件数の選びの代わりに軸と量の選び
    assert response.context_data["rows"] == [] and response.context_data["totals"] is None
    assert 'id="summary-chart"' in text and text.count('<path class="fill-primary') == 2
    assert 'name="g_by"' in text and 'name="g_val"' in text and 'name="per"' not in text
    assert '<input type="hidden" name="view" value="summary">' in text
    assert '<option value="status" selected>状態</option>' in text
    assert "表に戻る" in text and 'id="row-' not in text


def test_measure_label_keeps_a_total_heading():
    """見出しが「合計」で終わる列 (請求書の一覧の「合計」) の量は「合計の合計」にしない (精査 A9 の 3 節の 4)。"""
    from seizou.core.summary import measures_for
    from seizou.core.views import ColumnSpec

    total = ColumnSpec(Item, {"name": "standard_price", "label": "合計", "kind": "money"})
    price = ColumnSpec(Item, {"name": "standard_price", "label": "単価"})
    assert [m.label for m in measures_for([total, price], money_ok=True)] == ["件数", "合計", "単価の合計"]
    assert [m.label for m in measures_for([total, price], money_ok=False)] == ["件数"]


def test_lookup_axis_sums_a_number_column(user, orders):
    response = call(_LineList, user, view="summary", g_by="customer", g_val="qty")
    summary = response.context_data["summary"]
    north, south = str(orders["north"]), str(orders["south"])
    assert summary["measure"].label == "数量の合計"
    assert pairs(response) == sorted([(north, Decimal(6)), (south, Decimal(9))])
    assert Decimal(str(summary["total"])) == Decimal(15)
    assert [measure.key for measure in summary["measures"]] == ["count", "qty", "amount"]
    # 金額の閲覧がある人は金額の合計も選べる (単価 100 × 数量)
    response = call(_LineList, user, view="summary", g_by="customer", g_val="amount")
    assert dict(pairs(response)) == {north: Decimal(600), south: Decimal(900)}
    assert response.context_data["summary"]["measure"].money


def test_month_axis_counts_by_order_date_month(user, orders):
    response = call(_OrderList, user, view="summary", g_by="ordered:month")
    assert response.context_data["summary"]["axis"].label == "受注日の月"
    assert pairs(response) == [("2026-10", 2), ("2026-11", 3)]


def test_summary_follows_filters_and_search(user, orders):
    north = orders["north"]
    assert pairs(call(_OrderList, user, view="summary", f_customer=north.pk)) == [("未出荷", 3)]
    response = call(_OrderList, user, view="summary", g_by="customer", f_status="shipped")
    assert pairs(response) == [(str(orders["south"]), 2)]
    response = call(_OrderList, user, view="summary", g_by="ordered:month", f_ordered_from="2026-11-01")
    assert pairs(response) == [("2026-11", 3)]
    response = call(_OrderList, user, view="summary", q=orders["orders"][0].order_no)
    assert pairs(response) == [("未出荷", 1)]
    # 知らない軸と量は既定 (状態 × 件数) に戻す
    response = call(_OrderList, user, view="summary", g_by="nothing", g_val="nothing")
    assert response.context_data["summary"]["axis"].key == "status"
    assert response.context_data["summary"]["measure"].key == "count"
    # 絞りを外す link と表に戻る link は集計の GET を持ち越す / 外す
    response = call(_OrderList, user, view="summary", g_by="customer", f_status="shipped")
    context = response.context_data
    assert "view=summary" in context["query_without_filters"] and "g_by=customer" in context["query_without_filters"]
    assert context["query_without_view"] == "f_status=shipped"


def test_money_measure_is_hidden_without_money_access(user, orders, settings):
    settings.SEIZOU = MONEY_ON
    response = call(_LineList, user, view="summary", g_by="customer", g_val="amount")
    text = body(response)
    summary = response.context_data["summary"]
    assert [measure.key for measure in summary["measures"]] == ["count", "qty"]
    assert summary["measure"].key == "count"
    assert "金額の合計" not in text and "数量の合計" in text
    header = csv_rows(call(_LineList, user, view="summary", g_by="customer", g_val="amount", format="csv"))[0]
    assert header == ["得意先", "件数", "割合 (%)"]


def test_summary_csv_has_the_same_rows_and_total(user, orders):
    response = call(_OrderList, user, view="summary", format="csv")
    assert csv_rows(response) == [
        ["状態", "件数", "割合 (%)"],
        ["未出荷", "3", "60.0"],
        ["出荷済", "2", "40.0"],
        ["合計", "5", "100.0"],
    ]
    disposition = response["Content-Disposition"]
    assert "_summary_" in disposition and quote("作り物の受注の一覧_集計_", safe="") in disposition
    # view の無い CSV は今までどおり行の一覧
    assert csv_rows(call(_OrderList, user, format="csv"))[0] == ["受注番号", "得意先", "受注日", "状態"]


def test_htmx_request_returns_the_summary_part(user, orders):
    response = call(_OrderList, user, headers={"HX-Request": "true"}, view="summary", g_by="customer")
    text = body(response)
    assert "<html" not in text and 'id="summary-chart"' in text
    assert 'id="list-meta" class="ml-auto flex flex-wrap items-center gap-4" hx-swap-oob="true"' in text
    assert "表に戻る" in text and 'id="row-' not in text


def test_list_without_summary_is_unchanged(user, orders):
    response = call(_OrderList, user)
    text = body(response)
    assert response.context_data["summary"] is None
    assert len(response.context_data["rows"]) == 5
    assert 'name="per"' in text and 'id="summary-chart"' not in text
    # 道具の帯に「集計」の link (絞りの GET を持ち越す)。集計のときは「表」
    assert 'href="?&view=summary"' in text and "集計</a>" in text
    assert "表</a>" in body(call(_OrderList, user, view="summary"))
    # 軸の無い一覧 (数の範囲の絞りだけ) は view=summary でも表のまま。「集計」も出さない
    masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)
    response = call(_ItemList, user, view="summary")
    text = body(response)
    assert response.context_data["summary"] is None and len(response.context_data["rows"]) == 2
    assert "集計</a>" not in text and 'name="per"' in text


def test_chart_tag_scales_bars_and_shortens_labels():
    rows = [{"label": "あ" * 20, "value": 10}, {"label": "い", "value": Decimal("5")}, {"label": "う", "value": 0}]
    chart = chart_for(rows)
    first, second, third = chart["bars"]
    assert chart["width"] == 640 and chart["height"] == 100  # 上下 8 + 28 × 3 行
    assert first["label"] == "あ" * 14 + "…" and first["full"] == "あ" * 20
    assert first["value_x"] == "560.0" and second["value_x"] == "380.0"  # 192 + 360 + 8 と 192 + 180 + 8
    assert first["d"].startswith("M192.0,") and third["d"] == "" and third["value_x"] == "200.0"
    assert chart_for(rows, limit=2)["rest"] == 1


@pytest.mark.django_db
def test_order_screens_offer_the_summary(client, user, orders):
    client.force_login(user)
    url = reverse("orders:order_list")
    assert "集計</a>" in client.get(url).content.decode()
    response = client.get(url, {"view": "summary", "f_open": "0"})
    summary = response.context["summary"]
    assert [axis.key for axis in summary["axes"]] == ["status", "customer", "ordered:month"]
    assert [(row["label"], row["value"]) for row in summary["rows"]] == [("未出荷", 3), ("出荷済", 2)]
    # 注文残の盤: 部品の annotate (残) で絞った後の行を、納期の月 × 数量の合計で数える (納期の無い明細は「未設定」)。
    # 北の 10-30 の明細 (数量 1) を全部出荷すると残が 0 になり、既定の「出荷の残りがある物だけ」から外れる (15 − 1)。
    shipping_services.ship(orders["orders"][0].lines.get(), Decimal("1"), date(2026, 11, 4), user)
    response = client.get(reverse("orders:orderline_list"), {"view": "summary", "g_val": "qty"})
    summary = response.context["summary"]
    assert [axis.key for axis in summary["axes"]] == ["due:month"]
    assert [measure.key for measure in summary["measures"]] == ["count", "qty"]
    assert [(row["label"], Decimal(str(row["value"]))) for row in summary["rows"]] == [("未設定", Decimal(14))]
