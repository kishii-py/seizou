"""生産管理ボードと受注の一覧の絞りのタブ (土台の presets の最初の組。N147。UX の案 3-A)。名前は作り物だけ。

盤: すべて・出荷の残りがある (既定の印と同じ組)・納期が過ぎた・今週が納期 (月曜から日曜)。
受注の一覧: すべて・出荷の残りがある (既定の印と同じ組)・今月の受注 (状態を問わない)・出荷済 (状態を選ぶので、印は切に描かれる)。
"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from seizou.orders import services
from seizou.shipping import services as shipping_services
from tests.test_orders_views import customer, logged_in, shaft, user  # noqa: F401 - fixture を借りる

ROOT = Path(__file__).resolve().parent.parent
FRAGMENTS = [
    ROOT / "src/seizou/core/manual_default" / name
    for name in ("orders_order_list.html", "orders_orderline_list.html")
] + [
    ROOT / "examples/companies" / company / "manual" / name
    for company in ("a_machining", "b_plating")
    for name in ("orders_order_list.html", "orders_orderline_list.html")
]


def _order(partner, item, user, *, due=None, qty="5", order_date=None):  # noqa: F811
    """明細 1 行の受注 (納期は due。受注日は既定で今日)。"""
    lines = [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100"), "due_date": due}]
    return services.create_order(partner, lines, user, order_date=order_date or timezone.localdate())


def _listed(response):
    return [row["obj"] for row in response.context["rows"]]


def _tabs(response) -> dict:
    return {tab["label"]: tab for tab in response.context["presets"]}


def _counts(response) -> dict:
    return {tab["label"]: tab["count"] for tab in response.context["presets"]}


def _selected(response) -> list:
    return [tab["label"] for tab in response.context["presets"] if tab["selected"]]


@pytest.fixture
def board_lines(user, customer, shaft):  # noqa: F811
    """late (納期が昨日)・soon (納期が 3 日後)・done (出荷済)・cancelled (取消の受注) の 4 明細。"""
    today = timezone.localdate()
    late = _order(customer, shaft, user, due=today - timedelta(days=1)).lines.get()
    soon = _order(customer, shaft, user, due=today + timedelta(days=3)).lines.get()
    done = _order(customer, shaft, user, due=today - timedelta(days=2), qty="2").lines.get()
    shipping_services.ship(done, Decimal("2"), today, user)
    cancelled = _order(customer, shaft, user, due=today + timedelta(days=5)).lines.get()
    services.cancel_order(cancelled.order, user)
    return {"late": late, "soon": soon, "done": done, "cancelled": cancelled}


@pytest.mark.django_db
def test_board_tabs_and_counts(logged_in, board_lines):  # noqa: F811
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    this_week = sum(monday <= board_lines[name].due_date <= sunday for name in ("late", "soon"))

    response = logged_in.get(reverse("orders:orderline_list"))
    assert list(_counts(response)) == ["すべて", "出荷の残りがある", "納期が過ぎた", "今週が納期"]
    assert _counts(response) == {"すべて": 4, "出荷の残りがある": 2, "納期が過ぎた": 1, "今週が納期": this_week}
    assert _selected(response) == ["出荷の残りがある"]
    body = response.content.decode()
    assert 'id="list-presets"' in body and "生産管理ボード" in body
    week = _tabs(response)["今週が納期"]["href"]
    assert f"f_due_from={monday.isoformat()}" in week and f"f_due_to={sunday.isoformat()}" in week


@pytest.mark.django_db
def test_board_tab_href_fills_the_frame(logged_in, board_lines):  # noqa: F811
    url = reverse("orders:orderline_list")
    tabs = _tabs(logged_in.get(url))

    response = logged_in.get(url + tabs["納期が過ぎた"]["href"])
    assert _listed(response) == [board_lines["late"]]
    body = response.content.decode()
    assert 'name="f_overdue" value="1" class="checkbox checkbox-sm" checked data-list-filter>納期が過ぎた物だけ</label>' in body
    assert _selected(response) == ["納期が過ぎた"]
    assert _counts(response)["納期が過ぎた"] == 1

    response = logged_in.get(url + tabs["すべて"]["href"])
    assert len(_listed(response)) == 4
    assert _selected(response) == ["すべて"]
    assert 'name="f_open" value="1" class="checkbox checkbox-sm" data-list-filter>出荷の残りがある物だけ</label>' in response.content.decode()


@pytest.fixture
def listed_orders(user, customer, shaft):  # noqa: F811
    """今日の受注日の open (受注のまま)・done (出荷済)・cancelled (取消) の 3 受注。"""
    open_order = _order(customer, shaft, user)
    done = _order(customer, shaft, user, qty="2")
    shipping_services.ship(done.lines.get(), Decimal("2"), timezone.localdate(), user)
    cancelled = _order(customer, shaft, user, qty="1")
    services.cancel_order(cancelled, user)
    return {"open": open_order, "done": done, "cancelled": cancelled}


@pytest.mark.django_db
def test_order_list_tabs_and_counts(logged_in, listed_orders):  # noqa: F811
    url = reverse("orders:order_list")
    response = logged_in.get(url)
    assert list(_counts(response)) == ["すべて", "出荷の残りがある", "今月の受注", "出荷済"]
    assert _counts(response) == {"すべて": 3, "出荷の残りがある": 1, "今月の受注": 3, "出荷済": 1}

    first = timezone.localdate().replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    month = logged_in.get(url + _tabs(response)["今月の受注"]["href"])
    assert set(_listed(month)) == set(listed_orders.values())
    body = month.content.decode()
    assert f'name="f_ordered_from" value="{first}"' in body and f'name="f_ordered_to" value="{last}"' in body
    assert _selected(month) == ["今月の受注"]


@pytest.mark.django_db
def test_order_list_selected_tab_follows_status(logged_in, listed_orders):  # noqa: F811
    url = reverse("orders:order_list")
    assert _selected(logged_in.get(url)) == ["出荷の残りがある"]

    response = logged_in.get(url, {"f_status": "shipped"})
    assert _selected(response) == ["出荷済"]
    assert _listed(response) == [listed_orders["done"]]
    flag = [item for item in response.context["filters"] if item["name"] == "open"][0]
    assert (flag["checked"], flag["disabled"], flag["active"]) == (False, True, False)

    # 枠が送る形 (隠しの欄が印の 1 を持ち越す) でも「出荷済」のまま
    response = logged_in.get(url, {"f_open": "1", "f_status": "shipped"}, HTTP_HX_REQUEST="true")
    assert _selected(response) == ["出荷済"]
    assert '<div id="list-presets" role="tablist" aria-label="絞りのタブ" class="tabs seizou-tabs mb-4" hx-swap-oob="true">' in response.content.decode()


def test_manual_fragments_mention_the_tabs():
    for path in FRAGMENTS:
        text = path.read_text(encoding="utf-8")
        # タブの説明は 1 行 (grep -c と同じく行で数える。1 行の中に「タブ」の字は 2 回ある)
        assert sum("タブ" in line for line in text.splitlines()) == 1, path
        assert "すべて・" in text and "を押すと、絞り込みの枠にその条件が入る。" in text, path


COUNT = '<span id="list-count" class="font-medium tabular-nums text-base-content">'


@pytest.mark.django_db
def test_order_list_count_adds_everything_unless_everything_tab(logged_in, listed_orders, customer):  # noqa: F811
    """件数の行: 「すべて」以外のタブのとき (どのタブも選ばれていないときも) は「すべて」の数を括弧で添え、「すべて」では「全 N 件」のまま (C39-1 の a。初見テスト R5 の V65)。"""
    url = reverse("orders:order_list")

    # 開いた直後 (「出荷の残りがある」が選ばれた形): 表の 1 件と、「すべて」のタブの 3 件
    response = logged_in.get(url)
    assert _selected(response) == ["出荷の残りがある"]
    body = response.content.decode()
    assert f"{COUNT}1</span> 件 (すべて 3 件)</span>" in body
    assert f"全 {COUNT}" not in body
    assert 'id="list-meta" class="ml-auto flex flex-wrap items-center gap-4">' in body

    # 「すべて」のタブ: 今の「全 N 件」のまま、括弧は無い
    everything = logged_in.get(url + _tabs(response)["すべて"]["href"])
    assert _selected(everything) == ["すべて"]
    body = everything.content.decode()
    assert f"全 {COUNT}3</span> 件</span>" in body and "(すべて" not in body

    # どのタブも選ばれていない (得意先を選んだ) ときも、括弧に「すべて」の数を添える
    chosen = logged_in.get(url, {"f_customer": str(customer.pk)})
    assert _selected(chosen) == []
    assert f"{COUNT}1</span> 件 (すべて 3 件)</span>" in chosen.content.decode()

    # キーワードを打った htmx の応答: 件数と括弧の数とタブの数が、そろって oob で描き直される
    keyword = listed_orders["done"].order_no
    partial = logged_in.get(url, {"q": keyword}, HTTP_HX_REQUEST="true").content.decode()
    assert "<html" not in partial
    meta = partial.split('id="list-meta" class="ml-auto flex flex-wrap items-center gap-4" hx-swap-oob="true">', 1)[1]
    assert f"{COUNT}0</span> 件 (すべて 1 件)</span>" in meta
    assert '<div id="list-presets" role="tablist" aria-label="絞りのタブ" class="tabs seizou-tabs mb-4" hx-swap-oob="true">' in meta


@pytest.mark.django_db
def test_board_count_adds_everything_on_open(logged_in, board_lines):  # noqa: F811
    """生産管理ボードも開いた直後は「出荷の残りがある」が選ばれた形なので、件数に「すべて」の数を添える (土台の字)。"""
    response = logged_in.get(reverse("orders:orderline_list"))
    assert _selected(response) == ["出荷の残りがある"]
    assert f"{COUNT}2</span> 件 (すべて 4 件)</span>" in response.content.decode()


@pytest.mark.django_db
def test_count_line_unchanged_without_tabs_or_on_everything(logged_in):  # noqa: F811
    """発注の一覧は開いた直後に「すべて」が選ばれるので「全 N 件」のまま。タブの無い一覧も「全 N 件」のまま。"""
    purchase = logged_in.get(reverse("purchasing:purchaseorder_list"))
    assert _selected(purchase) == ["すべて"]
    body = purchase.content.decode()
    assert f"全 {COUNT}0</span> 件</span>" in body and "(すべて" not in body

    items = logged_in.get(reverse("masters:item_list"))
    assert items.context["presets"] == []
    body = items.content.decode()
    assert f"全 {COUNT}0</span> 件</span>" in body and "(すべて" not in body
