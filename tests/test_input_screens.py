"""入力の画面の直し (初見テスト R3 の所見)。Django の test client で HTML を見て、JS と CSS は字で約束を見る。

ブラウザでの確かめ (候補の一覧の位置・Enter・⋮ の位置・保存の位置・金額・残り・小窓) は tests/e2e の
E-06・E-08・E-38・E-12・E-05。
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

import seizou.connect
import seizou.core
import seizou.orders
import seizou.shipping
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.shipping import services as shipping_services

CORE = Path(seizou.core.__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
SELECT_JS = CORE / "static" / "js" / "seizou-select.js"
INPUT_CSS = REPO / "scripts" / "css" / "input.css"
APP_CSS = CORE / "static" / "css" / "app.css"
ROW_MENU_JS = CORE / "static" / "js" / "seizou-row-menu.js"
ORDERS = Path(seizou.orders.__file__).resolve().parent
CONNECT = Path(seizou.connect.__file__).resolve().parent
SHIPPING = Path(seizou.shipping.__file__).resolve().parent
MENU_TEMPLATES = (
    ORDERS / "templates" / "orders" / "_line_menu.html",
    ORDERS / "templates" / "orders" / "_row_menu.html",
    CONNECT / "templates" / "connect" / "_inbox_row.html",
)
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
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def order(user, customer, shaft):
    """明細 1 行 (数量 10・単価 100) の受注。"""
    return orders_services.create_order(
        customer,
        [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )


# --- 1. 候補の一覧を body の直下に描き、取りに行っている間の Enter を止める -------------------------


def test_select_js_draws_list_under_body_and_waits_for_load_on_enter():
    text = SELECT_JS.read_text(encoding="utf-8")
    for word in (
        "dropdownParent",
        '"body"',
        'closest("dialog")',
        "ts.loading > 0",
        "control_input.value.trim()",
        'ts.on("load"',
        "positionDropdown",
        'document.addEventListener("scroll"',
    ):
        assert word in text, word
    # 取った字を HTML として差し込まない。innerHTML の字は小窓を開く htmx の swap の名前の 1 か所 (今のまま) だけ。
    assert ".innerHTML" not in text and "insertAdjacentHTML" not in text
    assert text.count("innerHTML") == 1 and 'swap: "innerHTML"' in text
    # class は今の 3 行 (通信の誤りの知らせ・「詳細を見る」の link・部品から写した知らせ) だけ。どれも固定の字で、
    # テンプレートで使っている class (app.css に入っている物) だけ。新しい行で class を足さない。
    assert text.count("className =") == 3


def test_select_js_renames_the_loading_class():
    """読み込み中の class を部品の既定の loading から変える (daisyUI の .loading と重なり、欄が正方形に伸びて
    候補の一覧が欄から離れて窓の下の端に出る。初見テスト R4 の 1)。"""
    text = SELECT_JS.read_text(encoding="utf-8")
    assert 'loadingClass: "ts-loading"' in text
    assert 'loadingClass: "loading"' not in text
    assert ".ts-wrapper.loading" not in INPUT_CSS.read_text(encoding="utf-8")


def test_input_css_styles_list_under_body():
    source = INPUT_CSS.read_text(encoding="utf-8")
    assert "body > .ts-dropdown {" in source
    assert "body > .ts-dropdown .active {" in source
    built = APP_CSS.read_text(encoding="utf-8")
    assert len(re.findall(r"body ?> ?\.ts-dropdown", built)) >= 2


# --- 2. ⋮ のメニューを左のメニューに隠さない ------------------------------------------------------


def test_row_menu_js_keeps_left_of_main():
    text = ROW_MENU_JS.read_text(encoding="utf-8")
    for word in ('querySelector("main")', "minLeft", "anchor.left"):
        assert word in text, word
    assert "className" not in text and "classList.add" not in text


@pytest.mark.django_db
def test_order_detail_line_menu_is_above_sidebar(logged_in, order):
    body = logged_in.get(reverse("orders:order_detail", args=[order.pk])).content.decode()

    assert '<ul class="menu dropdown-content z-50 w-56' in body
    assert "z-40 w-56" not in body


def test_row_menu_templates_have_no_z40():
    for path in MENU_TEMPLATES:
        assert "z-40" not in path.read_text(encoding="utf-8"), path


# --- 3. 右下の「AI に質問」が保存と最後の行に重ならない (N160 の形。決め C37 で一度外し、本人の決め 11-07 09:3x で戻した。左のメニューの入口は 11-07 16:5x で外した) ---

MAIN_WITH_ROOM = '<main class="flex-1 p-4 pb-34 lg:p-6 lg:pb-34">'
MAIN_PLAIN = '<main class="flex-1 p-4 lg:p-6">'


@pytest.mark.django_db
def test_main_has_room_for_assistant_button(logged_in):
    body = logged_in.get(reverse("core:home")).content.decode()

    assert 'id="assistant-float"' in body
    assert MAIN_WITH_ROOM in body
    assert MAIN_PLAIN not in body


@pytest.mark.django_db
def test_main_has_no_room_without_assistant(logged_in, monkeypatch):
    from seizou.core import context_processors

    names = [name for name in context_processors.part_names() if name != "assistant"]
    monkeypatch.setattr(context_processors, "part_names", lambda: names)

    body = logged_in.get(reverse("core:home")).content.decode()

    assert 'id="assistant-toggle"' not in body
    assert 'id="assistant-float"' not in body
    assert MAIN_PLAIN in body
    assert MAIN_WITH_ROOM not in body


def test_app_css_has_bottom_room():
    built = APP_CSS.read_text(encoding="utf-8")
    assert ".pb-34{" in built
    assert ".lg\\:pb-34{" in built


# --- 4-1. 受注の入力の金額 --------------------------------------------------------------------

ORDER_LINES_JS = ORDERS / "static" / "orders" / "order-lines.js"
AMOUNT_HEADER = '<th class="whitespace-nowrap text-right font-medium">金額</th>'


def _line_headers(body: str) -> list[str]:
    """受注の入力の明細の表の見出しの字 (タグと「必須」の印を除く)。"""
    thead = re.search(r"<thead>(.*?)</thead>", body, re.S).group(1)
    cells = re.findall(r"<th[^>]*>(.*?)</th>", thead, re.S)
    return [re.sub(r"<span[^>]*>.*?</span>", "", cell, flags=re.S).strip() for cell in cells]


@pytest.mark.django_db
def test_order_form_shows_amount_after_unit_price(logged_in):
    body = logged_in.get(reverse("orders:order_create")).content.decode()

    headers = _line_headers(body)
    assert headers[headers.index("単価") + 1] == "金額"
    assert body.count("data-line-amount") >= 1
    assert "orders/order-lines.js" in body
    assert "金額は保存のときに計算します" not in body
    assert "金額は数量 × 単価で (単価の単位が「式」の行は単価のまま)、保存のときに得意先の端数の扱いで 1 円に丸めます。" in body


@pytest.mark.django_db
def test_order_form_single_mode_keeps_amount(settings, logged_in):
    settings.SEIZOU = {"order_form": "single"}
    body = logged_in.get(reverse("orders:order_create")).content.decode()

    assert "orders/order-lines.js" in body
    assert AMOUNT_HEADER in body
    assert "data-line-amount" in body


@pytest.mark.django_db
def test_order_form_without_money_has_no_amount(settings, logged_in):
    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}
    body = logged_in.get(reverse("orders:order_create")).content.decode()

    assert 'name="lines-0-unit_price"' not in body
    assert AMOUNT_HEADER not in body
    assert "data-line-amount" not in body


def test_order_lines_js_shows_amount_as_text():
    text = ORDER_LINES_JS.read_text(encoding="utf-8")
    for word in ("data-line-amount", "textContent", 'normalize("NFKC")', "data-add-order-line"):
        assert word in text, word
    assert "innerHTML" not in text


def _line_cell_classes(body: str, name: str) -> list[str]:
    """受注の入力の明細で、name の欄を包む td の class の字 (空白で分けた並び)。"""
    match = re.search(rf'<td class="([^"]*)">\s*<(?:select|input)[^>]*name="{re.escape(name)}"', body)
    assert match, f"{name} の欄の td が無い"
    return match.group(1).split()


@pytest.mark.django_db
def test_order_form_line_widths_fit_unit_and_fix_item(logged_in):
    """単位の列を 80px に狭め、部品の列の幅を決める (初見テスト R5 の候補 C35 の 1 と 2。C35-3 の a)。"""
    body = logged_in.get(reverse("orders:order_create")).content.decode()

    item = _line_cell_classes(body, "lines-0-item")
    assert "min-w-56" in item
    assert "w-56" in item
    unit = _line_cell_classes(body, "lines-0-unit")
    assert "min-w-20" in unit
    assert "min-w-32" not in unit
    assert "min-w-32" in _line_cell_classes(body, "lines-0-qty")

    thead = re.search(r"<thead>(.*?)</thead>", body, re.S).group(1)
    fixed = [
        re.sub(r"<span[^>]*>.*?</span>", "", text, flags=re.S).strip()
        for attrs, text in re.findall(r"<th([^>]*)>(.*?)</th>", thead, re.S)
        if "w-56" in re.search(r'class="([^"]*)"', attrs).group(1).split()
    ]
    assert fixed == ["部品"]

    template = re.search(r'<template id="order-line-template">(.*?)</template>', body, re.S).group(1)
    assert "min-w-20" in _line_cell_classes(template, "lines-__prefix__-unit")

    # 部品の列を箱の左の端に止める (C35 の 4。C34-1 の a の class)。明細の表 = <tbody id="order-lines"> を持つ表
    lines_table = body[body.rfind("<table", 0, body.index('<tbody id="order-lines">')) :]
    assert "seizou-pin-first" in re.match(r'<table class="([^"]*)"', lines_table).group(1).split()


@pytest.mark.django_db
def test_order_form_says_delete_is_at_right_end(logged_in):
    """表の上の説明で「消す」の置き場を言う (初見テスト R5 の候補 C35 の 3。C35-3 の a)。"""
    body = logged_in.get(reverse("orders:order_create")).content.decode()

    assert "行を消すときは、表の右の端の「消す」に印を付けて保存します。" in body
    assert "行を消すときは「消す」に印を付けて保存します。" not in body


# --- 4-2. 受注の入力の得意先の品番 ---------------------------------------------------------------


def _create_post(customer, item, **line):
    return {
        "customer": customer.pk,
        "order_date": ORDER_DATE.isoformat(),
        "customer_order_no": "PO-0001",
        "lines-TOTAL_FORMS": "1",
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-item": item.pk,
        "lines-0-qty": "10",
        "lines-0-unit": "",
        "lines-0-unit_price": "100",
        "lines-0-due_date": "",
        "lines-0-note": "",
        **{f"lines-0-{key}": value for key, value in line.items()},
    }


def _update_post(order, **line):
    row = order.lines.get()
    data = {
        "customer": order.customer_id,
        "order_date": order.order_date.isoformat(),
        "customer_order_no": order.customer_order_no,
        "note": order.note,
        "lines-TOTAL_FORMS": "1",
        "lines-INITIAL_FORMS": "1",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-order_line": row.pk,
        "lines-0-item": row.item_id,
        "lines-0-qty": str(row.qty),
        "lines-0-unit": row.unit_code,
        "lines-0-unit_price": str(row.unit_price),
        "lines-0-due_date": "",
        "lines-0-note": "",
    }
    data.update({f"lines-0-{key}": value for key, value in line.items()})
    return data


@pytest.mark.django_db
def test_create_order_saves_customer_part_no(logged_in, customer, shaft):
    body = logged_in.get(reverse("orders:order_create")).content.decode()
    assert "得意先の品番" in _line_headers(body)

    response = logged_in.post(reverse("orders:order_create"), _create_post(customer, shaft, customer_part_no="RB-3110"))

    order = Order.objects.get()
    assert response.status_code == 302
    assert order.lines.get().customer_part_no == "RB-3110"


@pytest.mark.django_db
def test_edit_order_shows_and_changes_customer_part_no(user, logged_in, customer, shaft):
    order = orders_services.create_order(
        customer,
        [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100"), "customer_part_no": "RB-3110"}],
        user,
        order_date=ORDER_DATE,
    )
    body = logged_in.get(reverse("orders:order_update", args=[order.pk])).content.decode()
    assert re.search(r'<input[^>]*name="lines-0-customer_part_no"[^>]*value="RB-3110"', body)

    response = logged_in.post(
        reverse("orders:order_update", args=[order.pk]), _update_post(order, customer_part_no="RB-3111")
    )

    assert response.status_code == 302
    assert order.lines.get().customer_part_no == "RB-3111"


@pytest.mark.django_db
def test_hidden_customer_part_no_is_not_shown(settings, logged_in):
    settings.SEIZOU = {"hidden_fields": ["customer_part_no"]}
    body = logged_in.get(reverse("orders:order_create")).content.decode()

    assert "得意先の品番" not in _line_headers(body)
    assert "lines-0-customer_part_no" not in body


def test_customer_part_no_is_hideable():
    from seizou.orders.forms import hideable_fields

    assert ("customer_part_no", "得意先の品番") in hideable_fields()


def _order_lines_section(client, order) -> str:
    """受注の詳細の明細の節 (id="order-lines-title" から </section> まで)。頭の「出荷登録」のメニューの品番と分けて見る。"""
    body = client.get(reverse("orders:order_detail", args=[order.pk])).content.decode()
    start = body.index('id="order-lines-title"')
    return body[start : body.index("</section>", start)]


@pytest.mark.django_db
def test_order_detail_item_cell_splits_part_no_and_name(logged_in, order, shaft):
    """初見テスト R5 の V53 (候補 C15 の 1): 部品の欄は、品番だけを部品の小窓の link にし、品名は link の外の薄い字。

    品番の span は折り返さない印つき (489 回目の本体の直し。幅 1280 の画面で表がはみ出さないよう、品名だけを折り返せるようにした)。
    """
    section = _order_lines_section(logged_in, order)

    modal = re.escape(reverse("masters:item_modal", args=[shaft.pk]))
    assert re.search(rf'<span class="whitespace-nowrap font-mono"><a class="link master-link" href="{modal}"[^>]*>T-100</a></span>', section)
    assert '</a></span> <span class="text-base-content/70">作り物の軸</span>' in section


@pytest.mark.django_db
def test_order_detail_shows_customer_part_no_under_item(user, logged_in, customer, shaft):
    """初見テスト R5 の V30 (候補 C15 の 2): 得意先の品番のある明細は、部品の欄の 2 行目に「得意先の品番」とその値を出す。"""
    order = orders_services.create_order(
        customer,
        [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100"), "customer_part_no": "RB-3110"}],
        user,
        order_date=ORDER_DATE,
    )

    section = _order_lines_section(logged_in, order)

    assert "得意先の品番" in section and "RB-3110" in section


@pytest.mark.django_db
def test_order_detail_has_no_customer_part_no_line_when_blank(logged_in, order):
    """得意先の品番が空の明細は 2 行目を出さない (行は今の 1 行の高さのまま)。"""
    assert "得意先の品番" not in _order_lines_section(logged_in, order)


@pytest.mark.django_db
def test_order_detail_line_menu_button_has_border(logged_in, order):
    """初見テスト R5 の V19 (候補 C15 の 3): 受注の詳細の明細の ⋮ は枠つき (見出しの「⋯」を小さくした形)。字と aria-label は今のまま。

    生産管理ボードと受信した注文の ⋮ は今の字のまま (tests/test_orders_board.py と tests/test_connect_inbox_menu.py が見る)。
    """
    section = _order_lines_section(logged_in, order)

    summary = '<summary class="btn btn-outline btn-xs border-base-300" aria-label="行の操作" title="行の操作">⋮</summary>'
    assert summary in section


# --- 4-3. 出荷の入力の残り -----------------------------------------------------------------

SHIPMENT_FORM_JS = SHIPPING / "static" / "shipping" / "shipment-form.js"
NO_LINE_HELP = "受注の明細を選ぶと、残りの数が出ます。"


@pytest.fixture
def plate(user):
    return masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)


@pytest.fixture
def split_order(user, customer, shaft, plate):
    """明細 10 と 5 の受注。1 行目に 4 を出荷した後 (残り 6 と 5)。"""
    order = orders_services.create_order(
        customer,
        [
            {"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100")},
            {"item": plate, "qty": Decimal("5"), "unit_price": Decimal("200")},
        ],
        user,
        order_date=ORDER_DATE,
    )
    shipping_services.ship(order.lines.get(line_no=1), Decimal("4"), SHIPPED_AT, user)
    return order


@pytest.mark.django_db
def test_orderline_candidates_end_with_remaining(logged_in, split_order):
    first = split_order.lines.get(line_no=1)
    data = logged_in.get(reverse("orders:orderline_select")).json()

    assert data["items"][0]["text"].endswith(" 残り 6")
    assert " 残り" not in str(first)


@pytest.mark.django_db
def test_shipment_form_shows_remaining_under_qty(logged_in, split_order):
    first = split_order.lines.get(line_no=1)
    response = logged_in.get(reverse("shipping:shipment_create"), {"order_line": first.pk})

    body = response.content.decode()
    assert "残り 6" in body
    assert re.search(r'<p id="id_qty_helptext"[^>]*>残り 6</p>', body)
    assert "shipping/shipment-form.js" in body
    assert "shipping/shipment_form.html" in [template.name for template in response.templates]


@pytest.mark.django_db
def test_shipment_form_without_line_shows_hint(logged_in, split_order):
    for params in ({}, {"order_line": "abc"}, {"order_line": "999999"}):
        body = logged_in.get(reverse("shipping:shipment_create"), params).content.decode()
        assert NO_LINE_HELP in body, params


def test_shipment_form_js_writes_text_only():
    text = SHIPMENT_FORM_JS.read_text(encoding="utf-8")
    for word in ("textContent", "残り", "_helptext", NO_LINE_HELP):
        assert word in text, word
    for word in ("innerHTML", "insertAdjacentHTML", "className", "classList.add", "fetch("):
        assert word not in text, word


def _qty_input(body: str) -> str:
    match = re.search(r'<input[^>]*name="qty"[^>]*>', body)
    assert match, "数量の欄が無い"
    return match.group(0)


@pytest.mark.django_db
def test_shipment_form_qty_has_setting_and_remaining(settings, logged_in, split_order):
    """初見テスト R5 の V61 (候補 C20 の 3): 数量の欄に、会社の設定 (いつも) と残りの数 (明細が決まっている時だけ) の data 属性を置く。

    JS (shipping/shipment-form.js) が打った数を残りと比べ、押す前に欄の下の字で知らせるため。明細を決めずに開いて後から選ぶ形でも、
    設定で字を分けられるように、設定の属性は明細の有無に依らず置く。
    """
    settings.SEIZOU = {"over_shipment": "stop"}
    first = split_order.lines.get(line_no=1)
    qty = _qty_input(logged_in.get(reverse("shipping:shipment_create"), {"order_line": first.pk}).content.decode())
    assert 'data-over-shipment="stop"' in qty
    assert 'data-remaining="6"' in qty

    settings.SEIZOU = {"over_shipment": "warn"}
    for params in ({}, {"order_line": "abc"}, {"order_line": "999999"}):
        qty = _qty_input(logged_in.get(reverse("shipping:shipment_create"), params).content.decode())
        assert 'data-over-shipment="warn"' in qty, params
        assert "data-remaining" not in qty, params


@pytest.mark.django_db
def test_shipment_form_helptext_turns_yellow_only_by_data_tone(logged_in, split_order):
    """欄の下の字は data-tone="warning" の間だけ黄 (決まった class。JS は class を触らない = R19)。class を足すのは form の warning_help_fields の欄だけ。"""
    first = split_order.lines.get(line_no=1)
    body = logged_in.get(reverse("shipping:shipment_create"), {"order_line": first.pk}).content.decode()
    assert '<p id="id_qty_helptext" class="mt-2 text-xs text-base-content/60 data-[tone=warning]:text-warning">残り 6</p>' in body
    assert 'data-tone="warning"' not in body
    # 黄の class を足すのは数量の欄の下の字だけ (ほかの欄の下の字の class は今のまま)
    assert body.count("data-[tone=warning]:text-warning") == 1


def test_shipment_form_js_compares_qty_with_remaining():
    text = SHIPMENT_FORM_JS.read_text(encoding="utf-8")
    for word in (
        "data-over-shipment",
        "data-remaining",
        "data-label-line",
        "help.dataset.tone = \"warning\"",
        "delete help.dataset.tone",
        " 超えています。",
        "このままでは保存できません。",
        "保存の時に確かめの知らせが出ます。",
        'qty.addEventListener("input"',
    ):
        assert word in text, word
    # 聞き手は欄そのものに付ける (小窓の差し替えのたびに document の聞き手が増えないように)
    assert "document.addEventListener(\"input\"" not in text
    assert "document.addEventListener(\"change\"" not in text


@pytest.mark.django_db
def test_shipment_post_still_saves(logged_in, split_order):
    second = split_order.lines.get(line_no=2)
    response = logged_in.post(
        reverse("shipping:shipment_create"),
        {"order_line": second.pk, "qty": "2", "shipped_at": SHIPPED_AT.isoformat(), "note": ""},
    )

    assert response.status_code == 302
    assert orders_services.remaining_qty(second) == Decimal("3")


# --- 4-4. 出荷の入力から部品の小窓 -----------------------------------------------------------


@pytest.mark.django_db
def test_orderline_modal_moves_to_item_modal(logged_in, order):
    line = order.lines.get()
    response = logged_in.get(reverse("orders:orderline_modal", args=[line.pk]), HTTP_HX_REQUEST="true")

    assert response.status_code == 302
    assert response["Location"] == reverse("masters:item_modal", args=[line.item_id])


@pytest.mark.django_db
def test_orderline_modal_requires_login_and_known_line(client, logged_in, order):
    line = order.lines.get()
    logged_in.logout()
    response = client.get(reverse("orders:orderline_modal", args=[line.pk]))
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]

    user = get_user_model().objects.get(username="tester")
    client.force_login(user)
    assert client.get(reverse("orders:orderline_modal", args=[999999])).status_code == 404


def test_orderline_select_carries_modal_url_template():
    from seizou.core.widgets import RemoteSelect

    html = RemoteSelect("orders:orderline_select").render("order_line", None)

    assert 'data-modal-url="' in html
    assert reverse("orders:orderline_modal", args=[0]) in html


# --- R4 4-3b. 部品を選んだら単位と単価を写す (05 の 2 節の「選んだら写す」) ------------------------------------


@pytest.fixture
def priced(user):
    return masters_services.create_item(
        {"part_no": "T-300", "name": "作り物の座金", "unit": "袋", "standard_price": Decimal("1234.50")}, user
    )


@pytest.mark.django_db
def test_item_candidates_carry_unit_and_standard_price(logged_in, priced, shaft):
    data = logged_in.get(reverse("masters:item_select"), {"q": "T-"}).json()
    rows = {row["id"]: row for row in data["items"]}

    # 単位のコードは数量単位の一覧の名前に当たる字だけ (「袋」は一覧の「袋（俵）」と字が違うので空。決め UNIT-1)
    assert rows[priced.pk] == {"id": priced.pk, "text": "T-300 作り物の座金", "title": "T-300", "sub": "作り物の座金・袋", "unit": "袋", "unit_code": "", "standard_price": "1234.50"}
    # 標準単価の無い部品は null (JS は写さない)
    assert rows[shaft.pk]["unit"] == "個"
    assert rows[shaft.pk]["unit_code"] == "H87"
    assert rows[shaft.pk]["standard_price"] is None


@pytest.mark.django_db
def test_item_candidates_hide_standard_price_without_money(settings, logged_in, priced):
    # 金額の判定が on で、Access の行の無い利用者 (金額の閲覧なし) には標準単価を返さない。単位は返す。
    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}
    row = logged_in.get(reverse("masters:item_select"), {"q": "T-300"}).json()["items"][0]

    assert row == {"id": priced.pk, "text": "T-300 作り物の座金", "title": "T-300", "sub": "作り物の座金・袋", "unit": "袋", "unit_code": ""}


@pytest.mark.django_db
def test_other_candidates_stay_id_and_text(logged_in, customer):
    row = logged_in.get(reverse("masters:partner_select")).json()["items"][0]

    assert set(row) == {"id", "text", "title", "sub"}


def test_select_js_copies_unit_and_price_into_same_row():
    text = SELECT_JS.read_text(encoding="utf-8")
    for word in (
        'select.closest("tr")',
        '{ key: "unit", suffix: "-unit", label: "単位" }',
        '{ key: "standard_price", suffix: "-unit_price", label: "単価" }',
        # 欄が空か前に写したままの値のときだけ写す (人が打った字は残す)
        'now !== "" && now !== input.getAttribute("data-copied")',
        # 明細の金額の目安 (orders/order-lines.js) が数え直す
        'input.dispatchEvent(new Event("input", { bubbles: true }))',
        # 発注の明細の工程の最初の値 (C100): 同じ決まりで選ぶ欄に写す 1 か所
        '{ key: "default_process", suffix: "-process", label: "工程" }',
        "function copyChoice(select, value)",
        'row.querySelector(\'select[name$="\' + spec.suffix + \'"]\')',
    ):
        assert word in text, word


def test_select_js_chooses_unit_code_in_unit_select():
    """明細の単位の欄が選ぶ欄 (数量単位の一覧) のときは、候補の unit_code を値に選ぶ (決め UNIT-1。R5 の候補 C108 の その 5)。"""
    text = SELECT_JS.read_text(encoding="utf-8")
    for word in (
        'var CHOICE_VALUE = { unit: "unit_code" };',
        # 字を打って探す欄は選んである値の候補しか持たないので、写す値の候補を足してから選ぶ
        ".addOption(",
        ".setValue(",
        # 字の欄の写しの道 (単価の欄と、単位が字の欄のとき) は残る
        'row.querySelector(\'input[name$="\' + spec.suffix + \'"]\')',
    ):
        assert word in text, word


def test_select_js_copy_note_matches_copy_values_wording(priced):
    text = SELECT_JS.read_text(encoding="utf-8")
    # 知らせの字は masters.services.copy_values の「写した」の文と同じ形 (単位と単価を部品から写しました。)
    _values, messages = masters_services.copy_values(priced, {"unit": "", "unit_price": ""}, set())
    assert messages == ["単位と単価を部品から写しました。"]
    assert 'var COPIED_TAIL = "を部品から写しました。";' in text
    assert 'copied.join("と") + COPIED_TAIL' in text
    # 知らせは字だけ (textContent) で、class は固定の字 (app.css に入っている物)
    assert 'copyNote.className = "mt-2 text-xs text-base-content/60";' in text
    assert 'copyNote.setAttribute("role", "status");' in text
    built = APP_CSS.read_text(encoding="utf-8")
    for name in (".mt-2", ".text-xs", "text-base-content\\/60"):
        assert name in built, name


@pytest.mark.django_db
def test_shipment_form_order_line_explains_candidate_head(logged_in, split_order):
    """初見テスト R5 の V106 (候補 C20 の 5): 受注の明細の欄の下で、候補の頭の「-1」が明細の行の番号だと言う (候補の字そのものは変えない)。"""
    body = logged_in.get(reverse("shipping:shipment_create")).content.decode()
    assert '<p id="id_order_line_helptext" class="mt-2 text-xs text-base-content/60">候補の頭は「受注番号-行の番号」です (末尾の -1 は 1 行目)。</p>' in body
