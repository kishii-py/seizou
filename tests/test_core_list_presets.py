"""一覧の絞りのタブ (BaseListView.presets。N147。UX の案 3-A)。

試験の中で BaseListView を継承した画面を作り、RequestFactory で直に呼ぶ (tests/test_core_list_filters.py と同じ形)。
タブは絞り込みの枠の値の組に名前を付けた GET の link で、件数は表と同じ条件 (キーワードを含む) で数える。名前は作り物だけ。
"""

import re
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from seizou.core.views import BaseListView
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import Order

#: 今月の組の関数を呼んだ回数 (要求のたびに呼ぶことを見る)
MONTH_CALLS = []


def _this_month():
    MONTH_CALLS.append(1)
    first = timezone.localdate().replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return {"f_open": "0", "f_ordered_from": first.isoformat(), "f_ordered_to": last.isoformat()}


def _open_only(orders):
    return orders.filter(status__in=(Order.STATUS_OPEN, Order.STATUS_PARTIAL))


class _OrderList(BaseListView):
    model = Order
    screen_name = "作り物の受注の一覧"
    # 行ごとの問い合わせが増えない列だけ (得意先の列は FK を読むので置かない。問い合わせの数を比べる試験のため)
    columns = ("order_no", "order_date", "status")
    search_fields = ("order_no", "customer__name")
    filters = (
        {"name": "status", "label": "状態", "kind": "choice", "field": "status"},
        {"name": "ordered", "label": "受注日", "kind": "date_range", "field": "order_date"},
        {
            "name": "open",
            "label": "出荷の残っている受注だけ",
            "kind": "flag",
            "when": _open_only,
            "default": True,
            "disabled_when": lambda values: values.get("status") is not None,
        },
    )
    presets = (
        {"label": "未出荷がある", "params": {"f_open": "1"}},
        {"label": "今月の受注", "params": _this_month},
        {"label": "出荷済", "params": {"f_status": "shipped"}},
    )
    ordering = ("order_date", "pk")
    template_name = "core/list.html"


class _PlainList(_OrderList):
    """同じ絞りと検索で、タブの無い一覧。"""

    presets = ()


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


def _order(customer, item, user, order_date):
    return orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("1"), "unit_price": Decimal("100")}], user, order_date=order_date
    )


@pytest.fixture
def made(user):
    """受注 4 件。北の得意先: 今月の受注 (受注のまま) と 2020 年の受注 (受注のまま)。南の得意先: 今月の出荷済と 2020 年の取消。"""
    north = masters_services.create_partner({"code": "C001", "name": "作り物の北の得意先", "role": "customer"}, user)
    south = masters_services.create_partner({"code": "C002", "name": "作り物の南の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    today = timezone.localdate()
    rows = {
        "north_now": _order(north, item, user, today),
        "south_now": _order(south, item, user, today),
        "north_old": _order(north, item, user, date(2020, 1, 15)),
        "south_old": _order(south, item, user, date(2020, 1, 20)),
    }
    Order.objects.filter(pk=rows["south_now"].pk).update(status=Order.STATUS_SHIPPED)
    Order.objects.filter(pk=rows["south_old"].pk).update(status=Order.STATUS_CANCELLED)
    return {"rows": rows, "item": item, "north": north}


def _request(user, path="/", params=None, **headers):
    request = RequestFactory().get(path, params or {}, **headers)
    request.user = user
    return request


def call(view, user, **params):
    return view.as_view()(_request(user, params=params))


def tabs(response) -> dict:
    return {tab["label"]: tab for tab in response.context_data["presets"]}


def selected(response) -> list:
    return [tab["label"] for tab in response.context_data["presets"] if tab["selected"]]


def test_no_presets_renders_no_tabs(user, made):
    response = call(_PlainList, user)
    assert response.context_data["presets"] == []
    assert 'id="list-presets"' not in response.render().content.decode()


def test_all_tab_is_first_and_turns_default_flags_off(user, made):
    response = call(_OrderList, user)
    labels = [tab["label"] for tab in response.context_data["presets"]]
    assert labels == ["すべて", "未出荷がある", "今月の受注", "出荷済"]
    everything = parse_qs(response.context_data["presets"][0]["href"][1:])
    assert everything == {"f_open": ["0"]}
    body = response.render().content.decode()
    assert 'id="list-presets" role="tablist" aria-label="絞りのタブ" class="tabs tabs-border mb-4">' in body
    # タブは絞り込みの枠 (form) の前に 1 回だけ描く
    assert body.count('id="list-presets"') == 1
    assert body.index('id="list-presets"') < body.index("from:#list-q")


def test_tab_counts_match_the_filtered_rows(user, made):
    response = call(_OrderList, user)
    counts = {label: tab["count"] for label, tab in tabs(response).items()}
    assert counts == {"すべて": 4, "未出荷がある": 2, "今月の受注": 2, "出荷済": 1}
    for tab in response.context_data["presets"]:
        followed = _OrderList.as_view()(_request(user, "/" + tab["href"]))
        assert followed.context_data["paginator"].count == tab["count"], tab["label"]

    # キーワードで絞ると、全部のタブの数がキーワードで絞った中の数になる (キーワードは link にも残る)
    response = call(_OrderList, user, q="北")
    counts = {label: tab["count"] for label, tab in tabs(response).items()}
    assert counts == {"すべて": 2, "未出荷がある": 2, "今月の受注": 1, "出荷済": 0}
    for tab in response.context_data["presets"]:
        followed = _OrderList.as_view()(_request(user, "/" + tab["href"]))
        assert followed.context_data["paginator"].count == tab["count"], tab["label"]


def test_selected_tab_follows_the_get_values(user, made):
    assert selected(call(_OrderList, user)) == ["未出荷がある"]
    assert selected(call(_OrderList, user, f_status="shipped")) == ["出荷済"]
    assert selected(call(_OrderList, user, f_open="0")) == ["すべて"]
    # 組に無い絞りが入っていれば、どのタブも選ばれない
    assert selected(call(_OrderList, user, f_ordered_from="2020-01-01")) == []
    # 絞りの枠が送る形 (空の欄と、隠しの 0 の後のチェックの 1) でも、読めた値で比べる
    request = _request(user, params={"f_status": "", "f_ordered_from": "", "f_ordered_to": "", "f_open": ["0", "1"]})
    assert selected(_OrderList.as_view()(request)) == ["未出荷がある"]

    body = call(_OrderList, user, f_status="shipped").render().content.decode()
    assert body.count("tab-active") == 1
    assert re.search(r'<a role="tab" class="tab tab-active" href="[^"]*" aria-selected="true">出荷済<', body)


def test_tab_href_keeps_keyword_per_sort_and_drops_page_and_filters(user, made):
    response = call(_OrderList, user, q="x", per="20", sort="order_no", page="2", f_status="open")
    for tab in response.context_data["presets"]:
        params = parse_qs(tab["href"][1:])
        assert params["q"] == ["x"] and params["per"] == ["20"] and params["sort"] == ["order_no"], tab
        assert "page" not in params and "format" not in params, tab
        assert params.get("f_status") in (None, ["shipped"]), tab
    assert parse_qs(tabs(response)["出荷済"]["href"][1:])["f_status"] == ["shipped"]


def test_callable_params_are_evaluated_per_request(user, made):
    MONTH_CALLS.clear()
    first = timezone.localdate().replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    response = call(_OrderList, user)
    params = parse_qs(tabs(response)["今月の受注"]["href"][1:])
    assert params == {"f_open": ["0"], "f_ordered_from": [first.isoformat()], "f_ordered_to": [last.isoformat()]}
    calls = len(MONTH_CALLS)
    assert calls >= 1
    call(_OrderList, user)
    assert len(MONTH_CALLS) == 2 * calls


def test_htmx_partial_redraws_tabs_out_of_band(user, made):
    partial = _OrderList.as_view()(_request(user, HTTP_HX_REQUEST="true")).content.decode()
    tags = re.findall(r'<div id="list-presets"[^>]*>', partial)
    assert len(tags) == 1 and tags[0].count('hx-swap-oob="true"') == 1
    assert partial.count('id="list-presets"') == 1

    full = call(_OrderList, user).render().content.decode()
    tags = re.findall(r'<div id="list-presets"[^>]*>', full)
    assert len(tags) == 1 and "hx-swap-oob" not in tags[0]

    # タブの無い一覧の htmx の応答には、タブを描かない
    plain = _PlainList.as_view()(_request(user, HTTP_HX_REQUEST="true")).content.decode()
    assert 'id="list-presets"' not in plain


def _csv_queries(view, user):
    with CaptureQueriesContext(connection) as captured:
        response = view.as_view()(_request(user, params={"format": "csv"}))
        body = response.content
    return len(captured.captured_queries), response, body


def test_csv_request_does_not_count_tabs(user, made):
    _csv_queries(_PlainList, user)  # 最初の 1 回の控えを外す
    with_tabs, response, body = _csv_queries(_OrderList, user)
    assert response["Content-Type"].startswith("text/csv")
    plain, _, plain_body = _csv_queries(_PlainList, user)
    assert with_tabs == plain
    assert body == plain_body

    request = _request(user, params={"format": "csv"})
    view = _OrderList()
    view.setup(request)
    assert view.preset_tabs() == []


def test_filter_params_is_restored_after_counting(user, made):
    request = _request(user)
    view = _OrderList()
    view.setup(request)
    response = view.dispatch(request)
    assert [tab["count"] for tab in response.context_data["presets"]] == [4, 2, 2, 1]
    assert view._filter_params is None
    assert view.filter_params is view.request.GET


class _TooMany(_OrderList):
    presets = tuple({"label": f"作り物のタブ {n}", "params": {"f_open": "1"}} for n in range(5))


class _UnknownKey(_OrderList):
    presets = ({"label": "作り物のタブ", "params": {"f_open": "1"}, "icon": "list"},)


def test_too_many_presets_is_an_error(user, made):
    with pytest.raises(ValueError, match="4 つまで"):
        call(_TooMany, user)
    with pytest.raises(ValueError, match="書けない鍵"):
        call(_UnknownKey, user)


def _page_queries(view, user):
    with CaptureQueriesContext(connection) as captured:
        response = view.as_view()(_request(user))
        response.render()
    return len(captured.captured_queries), response


def test_query_count_grows_by_the_number_of_tabs_not_rows(user):
    north = masters_services.create_partner({"code": "C001", "name": "作り物の北の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    _order(north, item, user, timezone.localdate())
    _page_queries(_OrderList, user)  # 最初の 1 回の控えを外す
    small, response = _page_queries(_OrderList, user)
    assert len(response.context_data["rows"]) == 1
    plain_small, _ = _page_queries(_PlainList, user)

    for _ in range(3):
        _order(north, item, user, timezone.localdate())
    large, response = _page_queries(_OrderList, user)
    assert len(response.context_data["rows"]) == 4
    plain_large, _ = _page_queries(_PlainList, user)

    assert small == large, (small, large)
    assert plain_small == plain_large, (plain_small, plain_large)
    # 増えるのはタブ (「すべて」を含めて 4 つ) の数の count だけ
    assert large - plain_large == len(response.context_data["presets"]) == 4
