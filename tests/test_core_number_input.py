"""数の欄の字 (初見テスト 5 回目 U43・V73・U124・a-flagged-12。候補 C43)。

1. 数の欄 (core の DecimalInput) の開いた時の字を、詳細と一覧と同じ字 (seizou_tags.comma) にする。印 data-number を付ける。
   送って止まった画面は、使う人が打った字のまま (全角と「,」は外れた字)。
2. 受注と発注の明細の数量と単価の欄を、字の欄 (DecimalInput) にする。type="number" を出さず、「1,732」「１，７３２」も通す。
3. 欄を離れた時に 3 桁ごとの区切りを付ける JS (core の static の js/seizou-number.js)。base.html の読み込みと、字の整え方
   (node の vm で format と focusout を動かす。DOM は作り物の小さな物) と、部品の単価を写す JS (js/seizou-select.js) の 1 行を見る。
作り物のデータだけを使う。ブラウザでの確かめ (離れた時の字・書きかけの知らせ) は本体の手。
"""

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
from seizou.core import fields as company_fields
from seizou.core.widgets import DecimalInput
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import Order

ORDER_DATE = date(2026, 11, 2)
JS_DIR = Path(seizou.core.__file__).resolve().parent / "static" / "js"
NUMBER_JS = JS_DIR / "seizou-number.js"
SELECT_JS = JS_DIR / "seizou-select.js"
SCRIPT_TAG = re.compile(r"<script [^>]*src=\"([^\"]+)\"[^>]*>")
NO_NODE = "node が無い (JS の字の形は本体の門の node --check で見る)"


@pytest.fixture
def no_registration(monkeypatch):
    """会社ごとの項目の登録を空から始める (ほかの試験の登録を残さない)。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def text(response) -> str:
    return response.content.decode()


def tag_of(body: str, name: str) -> str:
    """name の欄の <input …> の字。"""
    start = body.index(f'name="{name}"')
    return body[body.rindex("<input", 0, start) : body.index(">", start) + 1]


# --- 1. 開いた時の字 ---


@pytest.mark.parametrize(("price", "shown"), [("1200", "1,200"), ("12.5", "12.5"), ("480", "480"), ("1234567.8", "1,234,567.8")])
def test_item_update_shows_price_like_detail(logged_in, user, price, shown):
    item = masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "standard_price": Decimal(price)}, user
    )
    tag = tag_of(text(logged_in.get(reverse("masters:item_update", args=[item.pk]))), "standard_price")
    assert f'value="{shown}"' in tag
    assert 'type="text"' in tag and 'inputmode="decimal"' in tag
    assert " data-number" in tag


def test_item_create_field_has_number_mark(logged_in):
    tag = tag_of(text(logged_in.get(reverse("masters:item_create"))), "standard_price")
    assert " data-number" in tag and "value=" not in tag


def test_widget_formats_decimal_like_comma():
    widget = DecimalInput()
    assert widget.format_value(Decimal("2.50")) == "2.5"
    assert widget.format_value(Decimal("1200.00")) == "1,200"
    assert widget.format_value(Decimal("480.00")) == "480"
    assert widget.format_value(Decimal("0.00")) == "0"
    assert widget.format_value(Decimal("-1500.25")) == "-1,500.25"
    # 字 (打った字・字の初期値) と None は今のまま。
    assert widget.format_value("12.50") == "12.50"
    assert widget.format_value(None) is None
    assert widget.attrs["data-number"] is True and widget.attrs["inputmode"] == "decimal"


def test_company_number_field_shows_like_detail(logged_in, user, monkeypatch, no_registration):
    # B 社の膜厚 (会社ごとの項目の数)。試験の置き場に拡張の表は無いので、拡張の表の値を読む関数を作り物の値に差し替える。
    company_fields.register("masters.Item", "fields.ItemExt", [{"label": "膜厚", "type": "number"}])
    monkeypatch.setattr(company_fields, "ext_value", lambda obj, name: Decimal("2.50") if name == "ext01" else None)
    item = masters_services.create_item({"part_no": "T-110", "name": "作り物のめっきの軸"}, user)
    tag = tag_of(text(logged_in.get(reverse("masters:item_update", args=[item.pk]))), "ext__ext01")
    assert 'value="2.5"' in tag and " data-number" in tag


@pytest.mark.parametrize(("typed", "kept"), [("12.50", "12.50"), ("1,200", "1200"), ("１２．５０", "12.50")])
def test_stopped_form_keeps_typed_text(logged_in, typed, kept):
    # 品名が空で止まる。欄は打った字のまま (全角は半角に、「,」は外れた字。サーバーの normalize_number を通した後の字)。
    response = logged_in.post(
        reverse("masters:item_create"), {"part_no": "P-4004", "name": "", "standard_price": typed}
    )
    assert response.status_code == 200
    assert f'value="{kept}"' in tag_of(text(response), "standard_price")


# --- 2. 受注と発注の明細の欄 ---


def _order_post(customer, item, qty, price):
    """受注の新規登録の POST の値 (明細 1 行)。形は tests/test_core_line_totals.py の _order_post と同じ。"""
    return {
        "customer": str(customer.pk),
        "order_date": ORDER_DATE.isoformat(),
        "customer_order_no": "",
        "lines-TOTAL_FORMS": "1",
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-order_line": "",
        "lines-0-item": str(item.pk),
        "lines-0-customer_part_no": "",
        "lines-0-qty": qty,
        "lines-0-unit": "",
        "lines-0-unit_price": price,
        "lines-0-due_date": "",
        "lines-0-note": "",
    }


def test_order_update_line_fields_are_text_like_detail(logged_in, user, customer, item):
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("1500"), "unit_price": Decimal("1732")}], user, order_date=ORDER_DATE
    )
    body = text(logged_in.get(reverse("orders:order_update", args=[order.pk])))
    price = tag_of(body, "lines-0-unit_price")
    assert 'type="text"' in price and 'inputmode="decimal"' in price and 'value="1,732"' in price
    assert " data-number" in price and " data-label-price" in price
    qty = tag_of(body, "lines-0-qty")
    assert 'type="text"' in qty and 'value="1,500"' in qty and " data-label-qty" in qty
    for name in ("lines-0-qty", "lines-0-unit_price", "lines-__prefix__-qty", "lines-__prefix__-unit_price"):
        tag = tag_of(body, name)
        assert 'type="number"' not in tag and "step=" not in tag, name
        assert 'class="input w-full"' in tag, name
    # 明細の欄の右寄せは残す (字の欄の印で右に寄せる)。
    assert "[&_input[type=number]]:text-right [&_input[inputmode=decimal]]:text-right" in body


def test_purchase_order_line_fields_are_text(logged_in):
    body = text(logged_in.get(reverse("purchasing:purchaseorder_create")))
    for name in ("lines-0-qty", "lines-0-unit_price"):
        tag = tag_of(body, name)
        assert 'type="text"' in tag and 'inputmode="decimal"' in tag and " data-number" in tag, name
        assert 'type="number"' not in tag and "step=" not in tag, name
        assert 'class="input w-full"' in tag, name
    assert " data-label-qty" in tag_of(body, "lines-0-qty")
    assert " data-label-price" in tag_of(body, "lines-0-unit_price")
    assert "[&_input[type=number]]:text-right [&_input[inputmode=decimal]]:text-right" in body


@pytest.mark.parametrize("typed", ["1,732", "１，７３２", "1732"])
def test_order_line_price_accepts_commas_and_full_width(logged_in, customer, item, typed):
    response = logged_in.post(reverse("orders:order_create"), _order_post(customer, item, "1,000", typed))
    assert response.status_code == 302
    line = Order.objects.get().lines.get()
    assert (line.qty, line.unit_price) == (Decimal("1000"), Decimal("1732"))


def test_order_line_stopped_keeps_typed_text(logged_in, customer, item):
    # 数量が 0 で止まる (下限の検査)。単価の欄は打った字のまま (全角と「,」は外れた字)。
    response = logged_in.post(reverse("orders:order_create"), _order_post(customer, item, "0", "１２．５０"))
    assert response.status_code == 200
    body = text(response)
    assert "数量は 0 より大きい数にしてください。" in body
    assert 'value="12.50"' in tag_of(body, "lines-0-unit_price")
    assert not Order.objects.exists()


# --- 3. 欄を離れた時に区切りを付ける JS ---

#: node で JS を動かす台。document と window と HTMLInputElement だけの作り物の中で読み、format の結果と focusout の後の字を返す。
NODE_HARNESS = """
const fs = require("fs");
const vm = require("vm");
const listeners = {};
class HTMLInputElement {
  constructor(value, marked) {
    this.writes = 0;
    this.current = value;
    this.marked = marked;
  }
  get value() { return this.current; }
  set value(text) { this.writes += 1; this.current = text; }
  matches(selector) { return this.marked && selector === "input[data-number]"; }
}
const context = { document: { addEventListener(type, fn) { listeners[type] = fn; } }, window: {}, HTMLInputElement };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const cases = JSON.parse(process.argv[2]);
const left = cases.map((text) => {
  const input = new HTMLInputElement(text, true);
  listeners.focusout({ target: input });
  return [input.value, input.writes];
});
const other = new HTMLInputElement("2500", false);
listeners.focusout({ target: other });
listeners.focusout({ target: { value: "2500" } });
console.log(JSON.stringify({
  format: cases.map((text) => context.window.seizouNumber.format(text)),
  left: left,
  other: [other.value, other.writes],
  events: Object.keys(listeners),
}));
"""

#: (打った字, 離れた後の字)。読めない字は触らない。丸めない。
FORMAT_CASES = [
    ("2500", "2,500"),
    ("8800", "8,800"),
    ("1234500", "1,234,500"),
    ("12.50", "12.5"),
    ("12.345", "12.345"),
    ("12.3456", "12.3456"),
    ("760.00", "760"),
    ("1200.00", "1,200"),
    ("1,200", "1,200"),
    ("１，２００", "1,200"),
    ("１２．５０", "12.5"),
    (" 480 ", "480"),
    ("0012", "12"),
    ("0.50", "0.5"),
    ("0.00", "0"),
    ("-0", "0"),
    ("-1500.25", "-1,500.25"),
    ("12e", "12e"),
    ("abc", "abc"),
    ("12.", "12."),
    ("", ""),
]


def _run_number_js(cases):
    result = subprocess.run(
        ["node", "-e", NODE_HARNESS, str(NUMBER_JS), json.dumps(cases)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_base_loads_number_js_once_with_defer(logged_in):
    # 並びの中の位置は見ない (同じ並びの最後に 1 行を足すほかの依頼と、順が入れ替わっても通るように)。
    body = text(logged_in.get(reverse("core:home")))
    found = [src for src in SCRIPT_TAG.findall(body) if src.startswith("/static/js/seizou-number.js?v=")]
    assert len(found) == 1
    assert "defer" in re.search(r"<script [^>]*js/seizou-number\.js[^>]*>", body).group(0)


def test_number_js_words():
    script = NUMBER_JS.read_text(encoding="utf-8")
    for word in (
        'document.addEventListener("focusout"',
        '"input[data-number]"',
        'normalize("NFKC")',
        "replace(/,/g, \"\")",
        "window.seizouNumber = { format: format };",
    ):
        assert word in script, word
    # event を送らない (書きかけの印と保存のボタンの字を動かさない)。Number に直して丸めない。通信しない。HTML として差し込まない。
    for word in ("dispatchEvent(", "new Event(", "toLocaleString(", "parseFloat(", "Number(", "fetch(", "innerHTML", "className"):
        assert word not in script, word


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
def test_number_js_passes_node_check():
    result = subprocess.run(["node", "--check", str(NUMBER_JS)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
def test_number_js_formats_like_detail_when_leaving():
    typed = [case[0] for case in FORMAT_CASES]
    found = _run_number_js(typed)
    assert found["events"] == ["focusout"]
    assert dict(zip(typed, found["format"])) == dict(FORMAT_CASES)
    for (before, after), (value, writes) in zip(FORMAT_CASES, found["left"]):
        assert value == after, before
        # 字が変わる時だけ書く (触っていない「1,200」の欄を通っても書かない)。
        assert writes == (0 if before == after else 1), before
    # 印の無い欄 (一覧の絞りの数・整数の欄) と input でない物は触らない。
    assert found["other"] == ["2500", 0]


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
def test_number_js_matches_server_text_for_the_same_number():
    # 部品の候補の口が返す字 (Decimal の「760.00」) を写した字と、詳細と開いた時の字 (comma) が同じ (選び直しの比べ data-copied が合う)。
    values = ["760.00", "1200.00", "12.50", "0.00", "1234567.89"]
    found = _run_number_js(values)["format"]
    assert found == [DecimalInput().format_value(Decimal(value)) for value in values]


def test_select_js_formats_copied_number_fields():
    script = SELECT_JS.read_text(encoding="utf-8")
    line = 'if (input.hasAttribute("data-number") && window.seizouNumber) value = window.seizouNumber.format(value);'
    assert line in script
    # 整えるのは字にした後・比べの前 (写した字と data-copied が、整えた後の字になる)。
    assert script.index("value = String(value);") < script.index(line) < script.index("if (now === value) return;")
    assert script.index(line) < script.index('input.setAttribute("data-copied", value);')
    # 「前に写したままの値か」の比べの字は変えない。
    assert 'now !== "" && now !== input.getAttribute("data-copied")' in script
