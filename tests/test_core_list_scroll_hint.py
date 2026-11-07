"""一覧の表の左の 1 列目を箱の左に止め、右に続きがあるときだけ箱の右の端に「→」のボタンを出す (初見テスト R5 の候補 C34 の本 B。C34-1 = a。
本人の決め 11-07 18:4x〜19:1x で、右の端と下の端の灰のぼかしをやめ、右は「→」のボタン、下は箱のいつも見えるスクロールのバーにした)。
下に続きがある印 (data-more-below) は JS が付けるが、CSS は描かない (候補 C23。決め C23-2 = a。横断の検索と進捗の盤の箱を含む。試験は末尾)。

表の固定の class (seizou-pin-first)・箱の印 (data-scroll-hint) と包み・static の js/seizou-scroll-hint.js の字と base.html の読み込み・
scripts/css/input.css の決まりの字を見る。JS は動かさない。止まり方と色とぼかしの出し入れはブラウザで見る物なので、
ここでは見ない (本体の手の確かめ)。右に続きがあるときの属性 (data-more-right) は JS が付ける物で、サーバーの HTML には無い。app.css は読まない (レーンは作り直さないので、読むと作り直すまで落ちる)。
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
#: 印を付けた箱 (class の字は変えない) と、それを包む要素
HINT_BOX = '<div class="max-h-[70vh] overflow-auto rounded-lg border border-base-300" data-scroll-hint>'
WRAPPED_BOX = re.compile(r"<div>\s*" + re.escape(HINT_BOX) + r"\s*" + re.escape(PIN_TABLE))
JS = SRC / "core" / "static" / "js" / "seizou-scroll-hint.js"
SCRIPT_SRC = re.compile(r"<script [^>]*src=\"([^\"]+)\"[^>]*>")

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
            # 箱に JS が見つける印があり、箱は包みの要素の中にある。右に続きがあるときの属性はサーバーが出さない
            assert body.count(HINT_BOX) == 1 and WRAPPED_BOX.search(body), (name, headers)
            assert "data-more-right" not in body, (name, headers)
            # 欄 (td と th) の class の字は変えない (止めるのは表の class の決まり)
            assert "sticky" not in body.split(PIN_TABLE, 1)[1].split("</table>", 1)[0], (name, headers)


def test_every_list_table_carries_the_pin_class():
    """土台の表と写しの表 (受信した注文の空からの取りに行きの表を含む) の全部に、同じ字の class がある。"""
    for path in ROWS_TEMPLATES:
        text = path.read_text(encoding="utf-8")
        tables = re.findall(r"<table [^>]*>", text)
        assert tables and all(tag == PIN_TABLE for tag in tables), path
        boxes = re.findall(r'<div class="max-h-\[70vh\][^>]*>', text)
        assert boxes == [HINT_BOX], path
        assert len(WRAPPED_BOX.findall(text)) == 1, path


def test_pin_rule_is_in_the_css_source():
    """1 列目を止める決まりは input.css にあり、見出しと合計の行を止めた欄より上に重ねる。daisyUI の table-pin-cols は使わない。"""
    css = INPUT_CSS.read_text(encoding="utf-8")
    pin = _rule(css, ".seizou-pin-first :is(thead, tbody, tfoot) > tr > :first-child")
    assert "position: sticky;" in pin and "left: 0;" in pin
    assert "z-index: 2;" in pin and "background-color: var(--color-base-100);" in pin
    assert "z-index: 3;" in _rule(css, ".seizou-pin-first thead,\n.seizou-pin-first tfoot")
    for path in ROWS_TEMPLATES:
        assert "table-pin-cols" not in path.read_text(encoding="utf-8"), path


def test_scroll_hint_js_is_plain_and_only_touches_data_attributes():
    """JS は右に続きがあるかを箱の送りの値で見て、読み込み・送り・窓の大きさ・htmx の差し替えの後に取り直す。class と HTML と通信に触らない。"""
    text = JS.read_text(encoding="utf-8")
    for word in (
        "scrollLeft",
        "scrollWidth",
        "clientWidth",
        "[data-scroll-hint]",
        "data-more-right",
        '"DOMContentLoaded"',
        '"scroll"',
        '"resize"',
        '"htmx:afterSettle"',
        "setAttribute",
        "removeAttribute",
        # 「→」のボタン (11-07 の決め): 包みに 1 つ作り (createElement。絵は createElementNS)、押すと箱を scrollBy で右へ送る
        'data-scroll-more',
        'document.createElement("button")',
        "createElementNS",
        "m9 6 6 6-6 6",
        "scrollBy",
        '"click"',
    ):
        assert word in text, word
    for word in ("classList", "className", "innerHTML", "insertAdjacentHTML", "outerHTML", "fetch(", "XMLHttpRequest"):
        assert word not in text, word


@pytest.mark.django_db
def test_base_loads_scroll_hint_once_with_defer(logged_in):
    """base.html は新しい JS を 1 回だけ defer で読む (並びの中の位置は見ない。後から足す 1 行と順が入れ替わっても通るように)。"""
    body = logged_in.get(reverse("core:home")).content.decode()

    scripts = [src for src in SCRIPT_SRC.findall(body) if src.endswith("js/seizou-scroll-hint.js")]
    assert len(scripts) == 1
    tag = re.search(r"<script [^>]*js/seizou-scroll-hint\.js[^>]*>", body).group(0)
    assert "defer" in tag


def test_more_right_button_rule_is_in_the_css_source():
    """右の端の印は「→」のボタン (包みに data-more-right がある間だけ出る)。白い丸・1px の線・影なし・右から 1rem・縦の中央・見出しと止めた欄より上。
    前の形 (包みの ::after の灰のぼかし) は無い (本人の決め 11-07 18:4x「影があると統一感がない」)。"""
    css = INPUT_CSS.read_text(encoding="utf-8")
    assert "position: relative;" in _rule(css, "[data-more-right]")
    assert "display: none;" in _rule(css, "[data-scroll-more]")
    button = _rule(css, "[data-more-right] > [data-scroll-more]")
    for word in (
        "display: flex;",
        "position: absolute;",
        "top: 50%;",
        "right: 1rem;",
        "transform: translateY(-50%);",
        "border: 1px solid var(--color-base-300);",
        "border-radius: 9999px;",
        "background: var(--color-base-100);",
        "z-index: 4;",
    ):
        assert word in button, word
    assert "box-shadow" not in button
    assert "[data-more-right]::after" not in css and "rgba(120, 120, 120, 0.28)" not in css


# ---------------------------------------------------------------- 下の端のぼかし (初見テスト R5 の候補 C23。決め C23-2 = a)

#: 横断の検索の箱 (前に mt-4 が付く。class の字は変えない) と、進捗の盤の箱 (一覧の箱と同じ字)。どちらも 1 列目を止めない
SEARCH_HINT_BOX = '<div class="mt-4 max-h-[70vh] overflow-auto rounded-lg border border-base-300" data-scroll-hint>'
PLAIN_TABLE = '<table class="table table-sm table-zebra table-pin-rows">'
SEARCH_WRAPPED_BOX = re.compile(r"<div>\s*" + re.escape(SEARCH_HINT_BOX) + r"\s*" + re.escape(PLAIN_TABLE))
BOARD_WRAPPED_BOX = re.compile(r"<div>\s*" + re.escape(HINT_BOX) + r"\s*" + re.escape(PLAIN_TABLE))
#: 進捗の盤の行の数の字 (C23 の 2) は、包みの外の後ろにある
BOARD_COUNT_AFTER_WRAP = re.compile(
    r"</table>\s*</div>\s*</div>\s*" + re.escape('<p class="mt-4 text-base-content/70"><span class="tabular-nums">')
)


def test_scroll_hint_js_marks_the_bottom_edge():
    """JS は同じ箱で、下に続きがあるかを縦の送りの値で見て、包みに別の属性 (data-more-below) を付け外しする。"""
    text = JS.read_text(encoding="utf-8")
    for word in ("scrollTop", "scrollHeight", "clientHeight", "data-more-below"):
        assert word in text, word


def test_bottom_edge_uses_the_visible_scrollbar_not_a_fade():
    """下の端の印は、箱のいつも見えるスクロールのバー (::-webkit-scrollbar。Firefox は thin) で、ぼかしは描かない
    (本人の決め 11-07 18:4x = 合計の行にまで影が掛かっていた)。JS の印 data-more-below に CSS の決まりは無い。"""
    css = INPUT_CSS.read_text(encoding="utf-8")
    assert "[data-more-below]" not in css
    assert "linear-gradient(to bottom," not in css
    bar = _rule(css, ".overflow-auto::-webkit-scrollbar,\n.overflow-x-auto::-webkit-scrollbar")
    assert "width: 8px;" in bar and "height: 8px;" in bar


@pytest.mark.django_db
def test_search_and_board_boxes_carry_the_hint(logged_in, listed):
    """横断の検索 (区切りの数だけ) と進捗の盤 (1 つ) の箱に印があり、箱は包みの要素の中にある。端の属性はサーバーが出さない。"""
    response = logged_in.get(reverse("core:search"), {"q": "T-100"})
    assert response.status_code == 200
    sections = response.context["sections"]
    assert sections
    body = response.content.decode()
    assert body.count(SEARCH_HINT_BOX) == len(sections)
    assert len(SEARCH_WRAPPED_BOX.findall(body)) == len(sections)
    assert "data-more-below" not in body and "data-more-right" not in body

    response = logged_in.get(reverse("progress:progress_list"))
    assert response.status_code == 200
    body = response.content.decode()
    assert body.count(HINT_BOX) == 1 and len(BOARD_WRAPPED_BOX.findall(body)) == 1
    assert BOARD_COUNT_AFTER_WRAP.search(body)
    assert "data-more-below" not in body and "data-more-right" not in body
