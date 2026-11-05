"""選ぶ欄の候補の 2 段の字・最近使った物・0 件のときの登録の行 (N162。core.select_options と core の候補の口 BaseSelectView)。

候補の口は今の鍵 (id・text・columns) の横に、2 段の字 title と sub を返す。text (選んだ値の欄の字) は変えない。
表 LINES に無い model (受注の明細など) は title が text と同じ字、sub が空の字 (JS は今の 1 行のまま描く)。
JS と CSS は字を読んで、描き方の印 (data-select-*) と決まりの数を見る。作り物の名前だけを使う。
"""

import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse

import seizou.core
from seizou.core import select_options
from seizou.core.fields import Column
from seizou.masters import services as masters_services
from seizou.masters.models import Partner
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from seizou.shipping import services as shipping_services

SELECT_JS = Path(seizou.core.__file__).resolve().parent / "static" / "js" / "seizou-select.js"
INPUT_CSS = Path(__file__).resolve().parents[1] / "scripts" / "css" / "input.css"
ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(item, qty="10"):
    return {"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}


def _get(client, name, **params):
    response = client.get(reverse(name), params)
    assert response.status_code == 200
    return response.json()


# --- 1. 2 段の候補 ---


@pytest.mark.django_db
def test_item_candidate_has_two_lines(user, logged_in, shaft):
    row = _get(logged_in, "masters:item_select", q="T-100")["items"][0]

    assert row["id"] == shaft.pk
    assert row["text"] == "T-100 作り物の軸"  # 選んだ値の欄の字は今の 1 行のまま
    assert row["title"] == "T-100"
    assert row["sub"] == "作り物の軸・個"

    # 単位の空の部品は、下の段が品名だけ
    bare = masters_services.create_item({"part_no": "T-101", "name": "作り物の板", "unit": ""}, user)
    row = _get(logged_in, "masters:item_select", q="T-101")["items"][0]
    assert row["id"] == bare.pk
    assert (row["title"], row["sub"]) == ("T-101", "作り物の板")


@pytest.mark.django_db
def test_item_sub_adds_searchable_company_fields(monkeypatch, shaft):
    # 会社ごとの項目のうち検索に使う物 (searchable) だけを、品名と単位の間に並べる
    monkeypatch.setattr(
        select_options.company_fields,
        "columns_for",
        lambda obj: [Column(name="ext01", label="材質", searchable=True), Column(name="ext02", label="図面の版")],
    )
    monkeypatch.setattr(
        select_options.company_fields,
        "ext_value",
        lambda obj, name: {"ext01": "S45C", "ext02": "作り物の版"}[name],
    )

    assert select_options.lines(shaft) == ("T-100", "作り物の軸・S45C・個")


@pytest.mark.django_db
def test_partner_candidate_has_code_short_name_and_role(logged_in):
    named = Partner.objects.create(code="C001", name="作り物の得意先", short_name="作り物", role="customer")
    bare = Partner.objects.create(code="C002", name="作り物の両方", role="both")

    rows = {row["id"]: row for row in _get(logged_in, "masters:partner_select")["items"]}

    assert rows[named.pk]["text"] == "作り物の得意先"
    assert rows[named.pk]["title"] == "C001 作り物"
    assert rows[named.pk]["sub"] == "作り物の得意先・得意先"
    # 略称が空なら上の段はコードだけ (名前は下の段)
    assert rows[bare.pk]["title"] == "C002"
    assert rows[bare.pk]["sub"] == "作り物の両方・両方"


@pytest.mark.django_db
def test_line_candidates_stay_one_line(user, logged_in, customer, shaft):
    orders_services.create_order(customer, [_line(shaft)], user, order_date=ORDER_DATE)

    rows = _get(logged_in, "orders:orderline_select")["items"]

    assert rows
    for row in rows:
        assert row["sub"] == ""
        assert row["title"] == row["text"]


def test_select_js_draws_two_lines():
    script = SELECT_JS.read_text(encoding="utf-8")
    for word in ("data-select-title", "data-select-sub", "escape(data.sub)", "escape(data.text)"):
        assert word in script, word
    # 取った字を HTML として差し込む所を増やさない (「詳細を見る」の htmx の swap の字の 1 つだけ)
    assert script.count("innerHTML") == 1

    css = INPUT_CSS.read_text(encoding="utf-8")
    assert css.count("[data-select-sub]") == 2
    # 部品の CSS の .ts-dropdown [data-selectable] .highlight より強い選び方 (薄い青の地を消して主色の太字)
    assert css.count("[data-selectable] .highlight") == 2


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い (JS の字の形は本体の門の node --check で見る)")
def test_select_js_passes_node_check():
    result = subprocess.run(["node", "--check", str(SELECT_JS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# --- 2. 最近使った ---


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "rounding": "round"}, user
    )


def _used_items(user, customer, subcontractor):
    """部品 T-100〜T-105 を作り、受注・発注・出荷で日付の違う記録を入れる。部品の品番 → 部品を返す。"""
    items = {
        f"T-10{n}": masters_services.create_item({"part_no": f"T-10{n}", "name": f"作り物の部品{n}", "unit": "個"}, user)
        for n in range(6)
    }
    for part_no, day in (("T-100", date(2026, 10, 31)), ("T-101", date(2026, 11, 1)), ("T-103", date(2026, 11, 3)), ("T-105", date(2026, 11, 5))):
        orders_services.create_order(customer, [_line(items[part_no])], user, order_date=day)
    shipped = orders_services.create_order(customer, [_line(items["T-102"])], user, order_date=date(2026, 11, 2))
    purchasing_services.create_purchase_order(
        subcontractor, [_line(items["T-104"])], user, order_date=date(2026, 11, 4), send=False
    )
    shipping_services.ship(shipped.lines.get(), Decimal("1"), date(2026, 11, 6), user)
    return items


@pytest.mark.django_db
def test_recent_lists_used_items_newest_first(user, logged_in, customer, subcontractor):
    items = _used_items(user, customer, subcontractor)

    data = _get(logged_in, "masters:item_select")

    # 日付の新しい順 (出荷 11-06 の T-102 が先頭)・同じ部品は 1 つ・5 件まで (10-31 の T-100 は出ない)
    assert [row["id"] for row in data["recent"]] == [
        items[part_no].pk for part_no in ("T-102", "T-105", "T-104", "T-103", "T-101")
    ]
    # 最近使ったの候補は、一覧の候補と同じ形 (単位・標準単価・2 段の字を含む)
    assert set(data["recent"][0]) == set(data["items"][0])
    assert {"unit", "standard_price", "title", "sub"} <= set(data["recent"][0])
    assert data["recent"][0]["title"] == "T-102"


@pytest.mark.django_db
def test_recent_only_on_first_page_before_typing(user, logged_in, customer, subcontractor):
    # 記録の無い DB では、字を打つ前でも空 (鍵はある)
    masters_services.create_item({"part_no": "T-900", "name": "作り物の座金", "unit": "袋"}, user)
    data = _get(logged_in, "masters:item_select")
    assert "recent" in data
    assert data["recent"] == []

    _used_items(user, customer, subcontractor)
    assert _get(logged_in, "masters:item_select")["recent"]
    # 字を打った後と、続きのページには出さない
    assert _get(logged_in, "masters:item_select", q="T")["recent"] == []
    assert _get(logged_in, "masters:item_select", page="2")["recent"] == []


@pytest.mark.django_db
def test_recent_keeps_active_and_role_filters(user, logged_in, shaft, subcontractor):
    first = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    stopped = masters_services.create_partner({"code": "C002", "name": "作り物の止めた得意先", "role": "customer"}, user)
    orders_services.create_order(first, [_line(shaft)], user, order_date=date(2026, 11, 1))
    orders_services.create_order(stopped, [_line(shaft)], user, order_date=date(2026, 11, 2))
    Partner.objects.filter(pk=stopped.pk).update(active=False)
    purchasing_services.create_purchase_order(
        subcontractor, [_line(shaft)], user, order_date=date(2026, 11, 3), send=False
    )

    def recent_ids(**params):
        return [row["id"] for row in _get(logged_in, "masters:partner_select", **params)["recent"]]

    # 得意先の欄 (role=customer) には協力企業を出さない。使用停止の取引先はどちらにも出ない
    assert recent_ids(role="customer") == [first.pk]
    assert recent_ids() == [subcontractor.pk, first.pk]


def test_select_js_puts_recent_under_heading():
    script = SELECT_JS.read_text(encoding="utf-8")
    for word in ('var RECENT_LABEL = "最近使った";', "data.recent", "optgroup: RECENT", "self.clearOptionGroups()"):
        assert word in script, word
    assert "callback(data.items || []);" not in script

    css = INPUT_CSS.read_text(encoding="utf-8")
    assert css.count(".optgroup-header") == 2
