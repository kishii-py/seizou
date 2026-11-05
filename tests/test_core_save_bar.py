"""保存の帯 (N154。案 5-B)。入力の画面に書きかけの印がある間だけ、上の帯の下に貼り付く 1 行の帯を出す。

- 描く所 = core/base.html の上の帯の次の 1 か所。context に form があり、黄の知らせ (warnings) が無い画面だけ。
- 帯の出ない所 = 小窓の入力 (core/_form_modal.html は base.html を使わない)・一覧 (BaseListView。絞り込みの枠も)・
  詳細・ダッシュボード・ログイン・黄の知らせの後 (「このまま保存」の画面)。
- 帯のボタンの字の元 = 画面の下の保存のボタンと同じ (form.submit_label。無ければ「保存」)。ボタンの字は span の中 (既にある試験の字の縛り)。
- static の js/seizou-form-guard.js (出し入れと「破棄」「保存」) と js/seizou-submit-label.js (帯の字を写す) の約束を
  字で見る (ブラウザで動かさない。ブラウザでの確かめは E-50 と本体の手の確かめ)。
- 前書きの断片 (core/manual_default/index.html) の 1 節。
作り物のデータだけを使う。user と logged_in は tests/conftest.py の物を借りる。作り物の作り方と _button・MODAL は
tests/test_core_submit_label.py を、黄の知らせの作り方 (_HolidayNote・holiday_checks・holiday_data) は
tests/test_core_form_modal.py を写した。
"""

import html
import json
import re
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse

import seizou.core
from seizou.core import checks
from seizou.masters import services as masters_services
from seizou.masters.models import Holiday
from seizou.orders import services as orders_services

ORDER_DATE = date(2026, 11, 2)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
ACTION = "masters.create_holiday"
CORE = Path(seizou.core.__file__).resolve().parent
FORM_GUARD = CORE / "static" / "js" / "seizou-form-guard.js"
SUBMIT_LABEL = CORE / "static" / "js" / "seizou-submit-label.js"
SAVE_BAR = CORE / "templates" / "core" / "_save_bar.html"
MANUAL_INTRO = CORE / "manual_default" / "index.html"
HIDDEN_BAR = "data-save-bar hidden>"
BAR_WORDS = ("保存していない変更があります", "破棄")


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def plate(user):
    return masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)


@pytest.fixture
def order(user, customer, item, plate):
    """明細 2 行 (10 × 100・20 × 50) の受注。"""
    return orders_services.create_order(
        customer,
        [
            {"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")},
            {"item": plate, "qty": Decimal("20"), "unit_price": Decimal("50")},
        ],
        user,
        order_date=ORDER_DATE,
    )


class _HolidayNote(checks.Check):
    """作り物の知らせる検査 (名前に「作り物」が入っていたら黄の知らせ)。"""

    name = "T91"
    action = ACTION
    mode = "warn"

    def run(self, data, user):
        return "作り物の知らせです。確かめてから保存してください。" if "作り物" in str(data.get("name", "")) else None


@pytest.fixture
def holiday_checks():
    before = checks.registered(ACTION)
    yield
    checks.clear(ACTION)
    for check in before:
        checks.register(check)


def holiday_data(name: str) -> dict:
    return {"date": "2026-12-29", "name": name, "kind": "company"}


def text(response) -> str:
    return response.content.decode()


def _button(body):
    """入力のカードの主の保存のボタン。(ボタンの字, data-submit-label の型の並び か None, data-submit-label-plain の字 か None)。"""
    match = re.search(r'<button class="btn btn-primary" type="submit"([^>]*)>(.*?)</button>', body, re.S)
    assert match, "保存のボタンが無い"
    attrs, inner = match.group(1), match.group(2)
    label = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
    template = re.search(r'data-submit-label="([^"]*)"', attrs)
    plain = re.search(r'data-submit-label-plain="([^"]*)"', attrs)
    return (
        label,
        json.loads(html.unescape(template.group(1))) if template else None,
        html.unescape(plain.group(1)) if plain else None,
    )


def _bar(body):
    """保存の帯の HTML (<div class="sticky top-16 から最初の </div> まで)。"""
    start = body.find('<div class="sticky top-16')
    assert start >= 0, "保存の帯が無い"
    end = body.index("</div>", start)
    return body[start : end + len("</div>")]


def _bar_label(body):
    """保存の帯のボタンの字 (html の字の参照を戻した字)。"""
    match = re.search(r"<span data-save-bar-label>(.*?)</span>", _bar(body), re.S)
    assert match, "帯のボタンの字が無い"
    return html.unescape(match.group(1)).strip()


def _input_urls():
    return (
        reverse("masters:item_create"),
        reverse("masters:holiday_create"),
        reverse("orders:order_create"),
        reverse("purchasing:purchaseorder_create"),
    )


# --- 1. 帯を描く所と形 ---


def test_full_page_inputs_have_one_hidden_bar(logged_in):
    for url in _input_urls():
        response = logged_in.get(url)
        assert response.status_code == 200, url
        body = text(response)
        assert body.count(HIDDEN_BAR) == 1, url
        # 上の帯の次、<main> の前
        assert body.index("</header>") < body.index("data-save-bar") < body.index("<main"), url
        bar = _bar(body)
        assert 'role="region"' in bar, url
        assert 'aria-label="保存の帯"' in bar, url
        assert "保存していない変更があります" in bar, url
        assert "data-save-bar-discard" in bar, url
        assert "data-save-bar-save" in bar, url
        # 2 つとも送らないボタン (Ctrl+Enter が押す「最後の送るボタン」とログアウトの form に混ざらない)。主色は下の保存のボタンだけ。
        assert bar.count('type="button"') == 2, url
        assert "btn-primary" not in bar, url
        assert 'type="submit"' not in bar, url
        assert "<form" not in bar, url


def test_bar_label_is_the_submit_label(logged_in, order):
    for url in _input_urls():
        body = text(logged_in.get(url))
        assert _bar_label(body) == _button(body)[0], url
    assert _bar_label(text(logged_in.get(reverse("masters:item_create")))) == "保存"
    # 受注の編集 (明細 2 行 = 合計 2,000): 型の字は本体が変えうるので、金額で見る。
    body = text(logged_in.get(reverse("orders:order_update", args=[order.pk])))
    assert _bar_label(body) == _button(body)[0]
    assert "2,000" in _bar_label(body)


# --- 2. 帯の出ない所 ---


def test_modal_inputs_have_no_bar(logged_in):
    for url in (
        reverse("masters:item_create"),
        reverse("masters:holiday_create"),
        reverse("purchasing:purchaseorder_create"),
    ):
        response = logged_in.get(url, **MODAL)
        assert response.status_code == 200, url
        body = text(response)
        assert "<html" not in body, url
        assert "data-enter-guard" in body, url
        assert "data-save-bar" not in body, url


def test_pages_without_input_have_no_bar(client, user, order):
    client.force_login(user)
    order_list = text(client.get(reverse("orders:order_list")))
    # 絞り込みの枠のある一覧 (BaseListView。枠は form の object を持たない)
    assert "data-list-filter" in order_list
    assert "data-save-bar" not in order_list
    for url in (
        reverse("core:home"),
        reverse("masters:item_list"),
        reverse("orders:order_detail", args=[order.pk]),
    ):
        response = client.get(url)
        assert response.status_code == 200, url
        assert "data-save-bar" not in text(response), url
    client.logout()
    response = client.get(reverse("core:login"))
    assert response.status_code == 200
    assert "data-save-bar" not in text(response)


def test_warning_page_has_no_bar_and_stopped_page_has_it(logged_in, holiday_checks):
    checks.register(_HolidayNote())
    url = reverse("masters:holiday_create")
    response = logged_in.post(url, holiday_data("作り物の会社の休み"))
    body = text(response)
    assert response.status_code == 200
    assert "<html" in body
    assert "このまま保存" in body
    assert "data-save-bar" not in body
    assert Holiday.objects.count() == 0
    # 誤りで止まった画面は書きかけと見ない (N132 のまま) = 帯は描くが隠したまま
    stopped = logged_in.post(url, {})
    body = text(stopped)
    assert stopped.status_code == 200
    assert "休日の日付を入れてください。" in body
    assert body.count(HIDDEN_BAR) == 1


def test_cancel_screen_bar_keeps_button_words(logged_in, order):
    body = text(logged_in.get(reverse("orders:order_cancel", args=[order.pk])))
    assert _bar_label(body) == _button(body)[0]
    assert order.order_no in _bar_label(body)
    # 「取り消す</button>」は下のボタンの 1 つだけ (帯のボタンは </span></button> で閉じる)
    assert body.count("取り消す</button>") == 1
    assert "保存</button>" not in body
    assert "btn-primary" not in _bar(body)


# --- 3. JS の約束 (字で見る) ---


def test_form_guard_js_save_bar_words():
    script = FORM_GUARD.read_text(encoding="utf-8")
    for word in (
        'var BAR = "[data-save-bar]";',
        "function dirtyPageForm()",
        "function syncBar()",
        "bar.hidden = !dirtyPageForm()",
        '"[data-save-bar-save], [data-save-bar-discard]"',
        "'button[type=\"submit\"]'",
        "save.click()",
        "window.location.assign(window.location.pathname + window.location.search)",
        "入力した内容が消えます。閉じますか？",
    ):
        assert word in script, word
    # 確かめは小窓の閉じる 2 つの道と帯の「破棄」の 3 か所。帯の出し入れは定義 1 と呼ぶ所 3 (印を付ける・送る・破棄)。
    assert script.count("window.confirm(MESSAGE)") == 3
    assert script.count("syncBar()") == 4
    for word in (".classList", ".className", ".innerHTML", "insertAdjacentHTML", "outerHTML", "textContent", "location.reload", "fetch(", "XMLHttpRequest"):
        assert word not in script, word


def test_submit_label_js_mirrors_bar():
    script = SUBMIT_LABEL.read_text(encoding="utf-8")
    for word in (
        "function setBar(form) {",
        '"[data-save-bar]"',
        'form.matches("form[data-enter-guard]")',
        'form.closest("#seizou-modal")',
        'setNode(bar.querySelector("[data-save-bar-label]"), source.nodeValue)',
    ):
        assert word in script, word
    on_input = script[script.index("function onInput(event) {") :]
    on_input = on_input[: on_input.index("\n  }\n")]
    assert "update(form);" in on_input
    assert "setBar(form);" in on_input
    assert on_input.index("update(form);") < on_input.index("setBar(form);")
    assert "    if (form) update(form);\n" not in script
    for word in ("classList", "className", "innerHTML", "insertAdjacentHTML", "textContent", "fetch(", "XMLHttpRequest", "window.htmx"):
        assert word not in script, word


# --- 4. 説明書の前書き ---


def test_manual_intro_has_bar_sentence():
    intro = MANUAL_INTRO.read_text(encoding="utf-8")
    bar = SAVE_BAR.read_text(encoding="utf-8")
    assert "<h3>入力の画面</h3>" in intro
    for word in BAR_WORDS:
        assert f"「{word}」" in intro, word
        assert word in bar, word


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い (JS の字の形は本体の門の node --check で見る)")
def test_js_passes_node_check():
    for path in (FORM_GUARD, SUBMIT_LABEL):
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
