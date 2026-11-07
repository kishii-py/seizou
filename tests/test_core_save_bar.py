"""入力の画面の下の行と書きかけの字 (C21。決め C21-1 = e。前の形は N154 の保存の帯)。

- 下の行 = 入力の画面 (小窓の外) の form の下の「キャンセル」「保存」の行。印の class seizou-save-row で、開いた時から窓の下の端に
  貼り付く (scripts/css/input.css の固定の決まり。app.css は取り込みで作り直すので、ここでは input.css の字を見る)。
  印を付けるのは 9 つの下の行 (core/_form_card.html・受注・発注・一括出荷・説明書の確かめ・説明書の編集・請求書の新規登録・
  納品書を作る・表示の設定の画面の全体)。ここで見るのは請求書と納品書を除く 7 つ。小窓の形には付けない。
- 書きかけの字 = core/_save_bar.html (data-save-bar)。下の行の左の字の置き場 (seizou-save-slot) に描き、ボタンを持たない。
  出し入れは js/seizou-form-guard.js の syncBar (hidden の属性だけ)。黄の知らせの後 (「このまま保存」の画面) は描かない。
- 字の出ない所 = 小窓の入力 (core/_form_modal.html は base.html を使わない)・一覧 (BaseListView。絞り込みの枠も)・
  詳細・ダッシュボード・ログイン・黄の知らせの後。
- 上の帯の下の保存の帯と、帯の「保存」「破棄」のボタン (data-save-bar-save・-discard・-label) は無い。
- static の js/seizou-form-guard.js (字の出し入れ) と js/seizou-submit-label.js (帯へ字を写さない) の約束を
  字で見る (ブラウザで動かさない。ブラウザでの確かめは E-50 と本体の手の確かめ)。
- 前書きの断片 (core/manual_default/index.html) の 1 節。
作り物のデータだけを使う。user と logged_in は tests/conftest.py の物を借りる。作り物の作り方と _button・MODAL は
tests/test_core_submit_label.py を、黄の知らせの作り方 (_HolidayNote・holiday_checks・holiday_data) は
tests/test_core_form_modal.py を写した。説明書の作り物は tests/test_manuals_views.py と同じ services.create_manual。
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

import seizou
import seizou.core
from seizou.core import checks
from seizou.manuals import services as manuals_services
from seizou.masters import services as masters_services
from seizou.masters.models import Holiday
from seizou.orders import services as orders_services

ORDER_DATE = date(2026, 11, 2)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
ACTION = "masters.create_holiday"
PACKAGE = Path(seizou.__file__).resolve().parent
CORE = Path(seizou.core.__file__).resolve().parent
FORM_GUARD = CORE / "static" / "js" / "seizou-form-guard.js"
SUBMIT_LABEL = CORE / "static" / "js" / "seizou-submit-label.js"
SAVE_BAR = CORE / "templates" / "core" / "_save_bar.html"
MANUAL_INTRO = CORE / "manual_default" / "index.html"
INPUT_CSS = Path(__file__).resolve().parents[1] / "scripts" / "css" / "input.css"
HIDDEN_BAR = "data-save-bar hidden>"
BAR_WORDS = ("保存していない変更があります",)
ROW = '<div class="seizou-save-row flex flex-wrap items-center justify-end gap-2">'
SLOT = '<div class="seizou-save-slot">'
MODAL_ROW = '<div class="mt-6 flex flex-wrap items-center justify-end gap-2 border-t border-base-300 pt-6">'
#: 下の行を持つ 7 つのテンプレート (受注・発注・確かめ・説明書の編集・core の入力のカード・表示の設定の画面の全体は HTML でも見る。
#: 一括出荷は明細の作り物が要るので字で見る)。小窓の形の見方が並びの 0・2・3 を名指しするので、足すときは最後に足す
ROW_TEMPLATES = (
    CORE / "templates" / "core" / "_form_card.html",
    PACKAGE / "orders" / "templates" / "orders" / "order_form.html",
    PACKAGE / "purchasing" / "templates" / "purchasing" / "_purchaseorder_card.html",
    PACKAGE / "shipping" / "templates" / "shipping" / "_shipment_bulk_card.html",
    PACKAGE / "manuals" / "templates" / "manuals" / "confirm.html",
    PACKAGE / "manuals" / "templates" / "manuals" / "manual_form.html",
    CORE / "templates" / "core" / "_display_settings_card.html",
)


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


@pytest.fixture
def manual(user):
    return manuals_services.create_manual(None, "作り物の段取りの手順", "steps", user)


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
    """書きかけの字の HTML (data-save-bar の付いた <p から </p> まで)。"""
    match = re.search(r"<p [^>]*\bdata-save-bar\b[^>]*>.*?</p>", body, re.S)
    assert match, "書きかけの字が無い"
    return match.group(0)


def _form(body, index):
    """index の字を囲む form の HTML (<form から </form> まで)。囲む form の開きの tag に data-enter-guard があることも見る。"""
    start = body.rfind("<form", 0, index)
    assert start >= 0, "囲む form が無い"
    assert "</form>" not in body[start:index], "囲む form が無い"
    assert "data-enter-guard" in body[start : body.index(">", start)], "囲む form に data-enter-guard が無い"
    return body[start : body.index("</form>", index) + len("</form>")]


def _row(body):
    """下の行の HTML (seizou-save-row の開きの tag から、その form の </form> まで。行は form の最後の子)。"""
    start = body.find(ROW)
    assert start >= 0, "下の行が無い"
    return body[start : body.index("</form>", start)]


def _input_urls():
    return (
        reverse("masters:item_create"),
        reverse("masters:holiday_create"),
        reverse("orders:order_create"),
        reverse("purchasing:purchaseorder_create"),
        reverse("core:display_settings"),
    )


# --- 1. 下の行と字を描く所と形 ---


def test_full_page_inputs_have_one_hidden_bar(logged_in):
    for url in _input_urls():
        response = logged_in.get(url)
        assert response.status_code == 200, url
        body = text(response)
        assert body.count(HIDDEN_BAR) == 1, url
        # 上の帯の下 (<main> の前) には描かない。字は data-enter-guard の form の中の、下の行の中にある
        assert body.index("<main") < body.index("data-save-bar"), url
        assert "sticky top-16" not in body, url
        form = _form(body, body.index("data-save-bar"))
        assert form.count(ROW) == 1, url
        row = _row(body)
        assert HIDDEN_BAR in row, url
        # 行の最初の子が字の置き場で、その中に字 (間は部品の注記の行の改行だけ)
        assert re.match(re.escape(ROW) + r"\s*" + re.escape(SLOT) + r"\s*<p ", row), url
        bar = _bar(body)
        assert "保存していない変更があります" in bar, url
        assert 'aria-hidden="true">●</span>' in bar, url
        # 字はボタンを持たない (保存のボタンは下の行の 1 つだけ)
        assert "<button" not in bar, url
        assert "<form" not in bar, url


def test_save_button_is_once_in_the_bottom_row(logged_in, order):
    for url in (*_input_urls(), reverse("orders:order_update", args=[order.pk])):
        body = text(logged_in.get(url))
        form = _form(body, body.index(ROW))
        row = _row(body)
        # 主色の保存のボタンは form の中に 1 つで、下の行の中にある。Ctrl+Enter が押す最後の送るボタンも下の行の中
        assert form.count('class="btn btn-primary" type="submit"') == 1, url
        assert row.count('class="btn btn-primary" type="submit"') == 1, url
        assert form.rindex('type="submit"') > form.index(ROW), url
        assert _button(row)[0], url
        # 帯のボタンの字は無い
        for word in ("data-save-bar-save", "data-save-bar-discard", "data-save-bar-label"):
            assert word not in body, (url, word)
    # 受注の編集 (明細 2 行 = 合計 2,000): 下の行の保存のボタンの字は今のまま (型の字は本体が変えうるので、金額で見る)
    body = text(logged_in.get(reverse("orders:order_update", args=[order.pk])))
    assert "2,000" in _button(_row(body))[0]


def test_manual_screens_have_bottom_row(logged_in, manual):
    for url in (
        reverse("manuals:manual_update", args=[manual.pk]),
        reverse("manuals:manual_protect", args=[manual.pk]),
    ):
        response = logged_in.get(url)
        assert response.status_code == 200, url
        body = text(response)
        assert body.count(HIDDEN_BAR) == 1, url
        form = _form(body, body.index(ROW))
        assert form.count(ROW) == 1, url
        row = _row(body)
        assert HIDDEN_BAR in row, url
        assert "キャンセル" in row, url
        assert form.rindex('type="submit"') > form.index(ROW), url
    # 説明書の編集: 版のメモと「新しい版として保存」は行の外 (流れの中)、「プレビュー」と「保存」は行の中
    body = text(logged_in.get(reverse("manuals:manual_update", args=[manual.pk])))
    row = _row(body)
    assert "新しい版として保存" not in row
    assert 'name="note"' not in row
    assert "新しい版として保存" in body
    assert "プレビュー</button>" in row
    assert "保存</button>" in row


def test_seven_templates_have_bottom_row():
    for path in ROW_TEMPLATES:
        source = path.read_text(encoding="utf-8")
        assert source.count(ROW) == 1, path
        assert source.count(SLOT + '{% if not warnings %}{% include "core/_save_bar.html" %}{% endif %}</div>') == 1, path
    # 小窓の形を持つ 3 つは、小窓では今の tag のまま (class を組み立てず、tag を丸ごと 2 つ書く。R19)
    for path in ROW_TEMPLATES[2], ROW_TEMPLATES[3]:
        source = path.read_text(encoding="utf-8")
        assert "{% if in_modal %}\n  " + MODAL_ROW + "\n  {% else %}\n  " + ROW in source, path
    # core の入力のカードは、小窓の tag を欄の有る無しで 2 つ (欄の無い形は上の空きと線を描かない。候補 C24) と、
    # 画面の全体の ROW を、それぞれ丸ごと 1 回ずつ書く (class を組み立てず、tag を丸ごと書く。R19)
    source = ROW_TEMPLATES[0].read_text(encoding="utf-8")
    for tag in (MODAL_ROW, '<div class="flex flex-wrap items-center justify-end gap-2">', ROW):
        assert source.count(tag) == 1, tag


def test_input_css_has_sticky_row():
    css = INPUT_CSS.read_text(encoding="utf-8")
    start = css.index(".seizou-save-row {")
    rule = css[start : css.index("}", start)]
    assert "position: sticky;" in rule
    assert "bottom: 0;" in rule
    assert ".seizou-save-slot {" in css
    assert "html:has(.seizou-save-row) {" in css
    assert "scroll-padding-bottom" in css


# --- 2. 字の出ない所 ---


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
        # 小窓の下の行は貼り付けない (今の形のまま)
        assert "seizou-save-row" not in body, url
        assert "seizou-save-slot" not in body, url
        assert MODAL_ROW in body, url


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
        assert "seizou-save-row" not in text(response), url
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
    # 黄の知らせの後も下の行は貼り付く (字の置き場は空のまま、「このまま保存」は行の中)
    row = _row(body)
    assert re.match(re.escape(ROW) + r"\s*" + re.escape(SLOT) + "</div>", row)
    assert "このまま保存</button>" in row
    # 誤りで止まった画面は書きかけと見ない (N132 のまま) = 字は描くが隠したまま
    stopped = logged_in.post(url, {})
    body = text(stopped)
    assert stopped.status_code == 200
    assert "休日の日付を入れてください。" in body
    assert body.count(HIDDEN_BAR) == 1


def test_cancel_screen_bar_keeps_button_words(logged_in, order):
    body = text(logged_in.get(reverse("orders:order_cancel", args=[order.pk])))
    assert body.count(HIDDEN_BAR) == 1
    # 「取り消す</button>」は下の行のボタンの 1 つだけ (字はボタンを持たない)
    assert body.count("取り消す</button>") == 1
    assert "取り消す</button>" in _row(body)
    assert "保存</button>" not in body
    assert "<button" not in _bar(body)


# --- 3. JS の約束 (字で見る) ---


def test_form_guard_js_save_bar_words():
    script = FORM_GUARD.read_text(encoding="utf-8")
    for word in (
        'var BAR = "[data-save-bar]";',
        "function dirtyPageForm()",
        "function syncBar()",
        "bar.hidden = !dirtyPageForm()",
        "入力した内容が消えます。閉じますか？",
    ):
        assert word in script, word
    # 帯の「保存」「破棄」の受けは無い (保存は下の行のボタン、捨てるのは「キャンセル」と beforeunload の確認)
    for word in (
        "[data-save-bar-save]",
        "[data-save-bar-discard]",
        "save.click()",
        "window.location.assign(window.location.pathname + window.location.search)",
    ):
        assert word not in script, word
    # 確かめは小窓の閉じる 2 つの道だけ。字の出し入れは定義 1 と呼ぶ所 2 (印を付ける・送る)。
    assert script.count("window.confirm(MESSAGE)") == 2
    assert script.count("syncBar()") == 3
    for word in (".classList", ".className", ".innerHTML", "insertAdjacentHTML", "outerHTML", "textContent", "location.reload", "fetch(", "XMLHttpRequest"):
        assert word not in script, word


def test_submit_label_js_mirrors_bar():
    script = SUBMIT_LABEL.read_text(encoding="utf-8")
    # 帯のボタンが無いので、帯へ字を写さない (保存のボタンの字を埋め直す update だけ)
    for word in ("setBar", "[data-save-bar]", "data-save-bar-label"):
        assert word not in script, word
    on_input = script[script.index("function onInput(event) {") :]
    on_input = on_input[: on_input.index("\n  }\n")]
    assert on_input.endswith("    if (form) update(form);")
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
    assert "破棄" not in intro
    assert "破棄" not in bar


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い (JS の字の形は本体の門の node --check で見る)")
def test_js_passes_node_check():
    for path in (FORM_GUARD, SUBMIT_LABEL):
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
