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
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services

CORE = Path(seizou.core.__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
SELECT_JS = CORE / "static" / "js" / "seizou-select.js"
INPUT_CSS = REPO / "scripts" / "css" / "input.css"
APP_CSS = CORE / "static" / "css" / "app.css"
ROW_MENU_JS = CORE / "static" / "js" / "seizou-row-menu.js"
ORDERS = Path(seizou.orders.__file__).resolve().parent
CONNECT = Path(seizou.connect.__file__).resolve().parent
MENU_TEMPLATES = (
    ORDERS / "templates" / "orders" / "_line_menu.html",
    ORDERS / "templates" / "orders" / "_row_menu.html",
    CONNECT / "templates" / "connect" / "_inbox_row.html",
)
ORDER_DATE = date(2026, 11, 2)


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
    # class は今の 2 行 (知らせと「詳細を見る」の link) だけ。新しい行で class を足さない。
    assert text.count("className =") == 2


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


# --- 3. 右下の「AI に質問」が保存と最後の行に重ならない ---------------------------------------------

MAIN_WITH_ROOM = '<main class="flex-1 p-4 pb-24 lg:p-6 lg:pb-24">'
MAIN_PLAIN = '<main class="flex-1 p-4 lg:p-6">'


@pytest.mark.django_db
def test_main_has_room_for_assistant_button(logged_in):
    body = logged_in.get(reverse("core:home")).content.decode()

    assert 'id="assistant-toggle"' in body
    assert MAIN_WITH_ROOM in body
    assert MAIN_PLAIN not in body


@pytest.mark.django_db
def test_main_has_no_room_without_assistant(logged_in, monkeypatch):
    from seizou.core import context_processors

    names = [name for name in context_processors.part_names() if name != "assistant"]
    monkeypatch.setattr(context_processors, "part_names", lambda: names)

    body = logged_in.get(reverse("core:home")).content.decode()

    assert 'id="assistant-toggle"' not in body
    assert MAIN_PLAIN in body
    assert MAIN_WITH_ROOM not in body


def test_app_css_has_bottom_room():
    built = APP_CSS.read_text(encoding="utf-8")
    assert ".pb-24{" in built
    assert ".lg\\:pb-24{" in built


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
    assert "金額は数量 × 単価で、保存のときに得意先の端数の扱いで 1 円に丸めます。" in body


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

