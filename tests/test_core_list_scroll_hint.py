"""一覧の表の左の 1 列目を箱の左に止める (初見テスト R5 の候補 C34 の本 B。C34-1 = a)。

表の固定の class (seizou-pin-first) と、scripts/css/input.css の決まりの字を見る。止まり方と色はブラウザで見る物なので、
ここでは見ない (本体の手の確かめ)。app.css は読まない (レーンは作り直さないので、読むと作り直すまで落ちる)。
作り物のデータは全部 services で作る。
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse

import seizou
from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services

SRC = Path(seizou.__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[1]
INPUT_CSS = ROOT / "scripts" / "css" / "input.css"

#: 1 列目を止める表 (土台の表と、その写し。受信した注文の空からの取りに行きの表を含む)
ROWS_TEMPLATES = [
    SRC / "core" / "templates" / "core" / "_rows.html",
    SRC / "orders" / "templates" / "orders" / "_rows.html",
    SRC / "orders" / "templates" / "orders" / "_board_rows.html",
    SRC / "connect" / "templates" / "connect" / "_rows.html",
]

PIN_TABLE = '<table class="table table-sm table-zebra table-pin-rows seizou-pin-first">'

#: 行がある形で開く一覧 (生産管理ボード・受注・請求書・工程・指示書)
LISTS = [
    "orders:orderline_list",
    "orders:order_list",
    "billing:invoice_list",
    "masters:process_list",
    "workorders:workorder_list",
]


@pytest.fixture
def listed(user):
    """5 つの一覧のどれにも 1 行以上ある形 (受注 2 つ・出荷 1 つと納品書と請求書・工程 1 つ・指示書 1 枚)。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    cut = masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)
    lines = [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}]
    order = orders_services.create_order(customer, lines, user, order_date=date(2026, 11, 2))
    workorders_services.issue_workorder(
        order.lines.get(), Decimal("10"), [{"process": cut, "planned_qty": Decimal("10")}], user
    )
    shipped = orders_services.create_order(customer, lines, user, order_date=date(2026, 11, 2))
    shipment = shipping_services.ship(shipped.lines.get(), Decimal("10"), date(2026, 11, 3), user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    billing_services.create_invoice(customer, [shipment], user, issued_on=date(2026, 11, 4))


def _rule(css: str, selector: str) -> str:
    """input.css の中の、selector で始まる決まりの { } の中身。"""
    start = css.index(selector + " {")
    return css[start : css.index("}", start)]


@pytest.mark.django_db
def test_lists_pin_the_first_column(logged_in, listed):
    """5 つの一覧の表に 1 列目を止める class がある。画面の全体と、htmx の要求 (行の部分だけを返す道) の両方。"""
    for name in LISTS:
        for headers in ({}, {"HTTP_HX_REQUEST": "true"}):
            response = logged_in.get(reverse(name), {"f_open": "0"}, **headers)
            assert response.status_code == 200, (name, headers)
            assert response.context["rows"], (name, headers)
            body = response.content.decode()
            assert body.count(PIN_TABLE) == 1, (name, headers)
            # 欄 (td と th) の class の字は変えない (止めるのは表の class の決まり)
            assert "sticky" not in body.split(PIN_TABLE, 1)[1].split("</table>", 1)[0], (name, headers)


def test_every_list_table_carries_the_pin_class():
    """土台の表と写しの表 (受信した注文の空からの取りに行きの表を含む) の全部に、同じ字の class がある。"""
    for path in ROWS_TEMPLATES:
        text = path.read_text(encoding="utf-8")
        tables = re.findall(r"<table [^>]*>", text)
        assert tables and all(tag == PIN_TABLE for tag in tables), path


def test_pin_rule_is_in_the_css_source():
    """1 列目を止める決まりは input.css にあり、見出しと合計の行を止めた欄より上に重ねる。daisyUI の table-pin-cols は使わない。"""
    css = INPUT_CSS.read_text(encoding="utf-8")
    pin = _rule(css, ".seizou-pin-first :is(thead, tbody, tfoot) > tr > :first-child")
    assert "position: sticky;" in pin and "left: 0;" in pin
    assert "z-index: 2;" in pin and "background-color: var(--color-base-100);" in pin
    assert "z-index: 3;" in _rule(css, ".seizou-pin-first thead,\n.seizou-pin-first tfoot")
    for path in ROWS_TEMPLATES:
        assert "table-pin-cols" not in path.read_text(encoding="utf-8"), path
