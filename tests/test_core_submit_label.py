"""保存のボタンの字に、押した結果の対象と数と金額を書く (N138。決め 11-04 23:0x「ボタンに金額を入れて、押す前の迷いを減らす」)。

- core の口 = core/forms.py の SubmitLabelMixin・label_number と、入力のカードのボタンの data-submit-label の印。
- 出荷・一括出荷・発注・受入・入金のボタンの字 (開いた形と、送って止まった形)。金額の閲覧が無い人の発注の字。
- 取消の確かめの画面 (受注・指示書・納品書・請求書・発注) のボタンの字に対象の番号。
- static の js/seizou-submit-label.js の約束 (ブラウザで動かさず字で見る。ブラウザでの確かめは本体の手の確かめ) と、
  base.html の読み込みの 1 行。
作り物のデータだけを使う。作り物の作り方は tests/test_orders_board_bulk.py と tests/test_core_screen_polish.py を写した。
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
from django import forms
from django.contrib.auth import get_user_model
from django.urls import reverse

import seizou.core
from seizou.billing import services as billing_services
from seizou.core.forms import SubmitLabelMixin, label_number
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
NO_MONEY = {"department": "off", "write": "off", "money": "on"}
JS = Path(seizou.core.__file__).resolve().parent / "static" / "js" / "seizou-submit-label.js"


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
def maker(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def plate(user):
    return masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)


def _line(user, customer, item, qty="10"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _button(body, css="btn btn-primary"):
    """入力のカードの主の保存のボタン。(ボタンの字, data-submit-label の型の並び か None, data-submit-label-plain の字 か None)。

    css はボタンの class の字 (既定は主の色。取消の確かめの実行のボタンは赤の btn btn-error = 候補 C24)。
    """
    match = re.search(r'<button class="' + re.escape(css) + r'" type="submit"([^>]*)>(.*?)</button>', body, re.S)
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


# --- 1. core の口 ---


class _Sample(SubmitLabelMixin, forms.Form):
    submit_label_plain = "発注する"

    def __init__(self, *args, found=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.found = found or {}

    def submit_label_templates(self):
        return ["{rows} 行・合計 ¥{amount} で発注する", "{rows} 行で発注する"]

    def submit_label_values(self):
        return self.found


def test_label_number_rounds_and_groups():
    assert label_number(Decimal("45000.00")) == "45,000"
    assert label_number(Decimal("1234.5")) == "1,234.5"
    assert label_number(Decimal("1.005")) == "1.01"
    assert label_number("１，２００") == "1,200"
    assert label_number("10") == "10"
    for value in (None, "", "abc", True, "1e999999"):
        assert label_number(value) is None, value


def test_mixin_fills_the_first_complete_template():
    assert _Sample(found={"rows": "3", "amount": "45,000"}).submit_label == "3 行・合計 ¥45,000 で発注する"
    assert _Sample(found={"rows": "3", "amount": None}).submit_label == "3 行で発注する"
    assert _Sample(found={}).submit_label == "発注する"
    assert json.loads(_Sample().submit_label_template) == ["{rows} 行・合計 ¥{amount} で発注する", "{rows} 行で発注する"]


# --- 2. 出荷 ---


def test_ship_form_writes_qty_and_unit(logged_in, user, customer, item):
    line = _line(user, customer, item)
    url = reverse("shipping:shipment_create")

    body = logged_in.get(url, {"order_line": line.pk}).content.decode()
    assert _button(body) == ("出荷する", ["{qty} 個を出荷する"], "出荷する")
    assert "「必須」の欄を埋めて「出荷する」を押します。" in body
    assert f'data-label-line="{line.pk}"' in body
    assert re.search(r'<input type="text" name="qty"[^>]* data-label-qty', body)

    body = logged_in.get(url, {"order_line": line.pk, "qty": "10"}).content.decode()
    assert _button(body)[0] == "10 個を出荷する"

    # 送って止まった形 (出荷日が空) は、送った数で描き直す。
    body = logged_in.post(url, {"order_line": line.pk, "qty": "3", "shipped_at": ""}).content.decode()
    assert _button(body)[0] == "3 個を出荷する"

    # 小窓の形も同じボタン。
    body = logged_in.get(url, {"order_line": line.pk, "qty": "4"}, **MODAL).content.decode()
    assert _button(body)[0] == "4 個を出荷する"

    # 明細の決まっていない形は「出荷する」のまま (単位が分からないので JS の型を置かない)。
    body = logged_in.get(url).content.decode()
    assert _button(body) == ("出荷する", None, None)


# --- 3. 一括出荷 ---


def test_bulk_ship_writes_rows_and_total(logged_in, user, customer, item, plate):
    a = _line(user, customer, item)
    b = _line(user, customer, item, qty="20")
    c = _line(user, customer, plate)
    url = reverse("shipping:shipment_bulk_create")

    body = logged_in.get(f"{url}?line={a.pk}&line={b.pk}").content.decode()
    assert _button(body) == (
        "2 行・合計 30 個を出荷する",
        ["{rows} 行・合計 {qty} 個を出荷する", "{rows} 行を出荷する"],
        "出荷する",
    )
    assert "「必須」の欄を埋めて「出荷する」を押します。" in body
    assert re.search(rf'name="qty_{a.pk}"[^>]* data-label-qty', body)
    assert re.search(rf'name="skip_{a.pk}"[^>]* data-label-skip', body)

    # 単位の違う行が混ざると合計を書かない。
    body = logged_in.get(f"{url}?line={a.pk}&line={c.pk}").content.decode()
    assert _button(body)[:2] == ("2 行を出荷する", ["{rows} 行を出荷する"])

    # 送って止まった形 (出荷日が空): 出さない行を除き、送った数で描き直す。
    data = {"line": [str(a.pk), str(b.pk)], "shipped_at": "", f"qty_{a.pk}": "7", f"qty_{b.pk}": "20", f"skip_{b.pk}": "on"}
    body = logged_in.post(url, data).content.decode()
    assert _button(body)[0] == "1 行・合計 7 個を出荷する"


# --- 4. 発注 ---


def _po_post(maker, *rows, partner=True):
    data = {
        "partner": str(maker.pk) if partner else "",
        "order_date": ORDER_DATE.isoformat(),
        "note": "",
        "lines-TOTAL_FORMS": str(len(rows)),
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
    }
    for index, (part, qty, price) in enumerate(rows):
        data.update(
            {
                f"lines-{index}-item": str(part.pk),
                f"lines-{index}-qty": qty,
                f"lines-{index}-unit": "",
                f"lines-{index}-unit_price": price,
                f"lines-{index}-due_date": "",
                f"lines-{index}-note": "",
            }
        )
    return data


def test_purchase_order_writes_rows_and_total(logged_in, user, customer, maker, item, plate):
    a = _line(user, customer, item)
    b = _line(user, customer, plate)
    url = reverse("purchasing:purchaseorder_create")

    # 盤で選んだ明細から開いた形: 単価が空なので行の数だけ。
    body = logged_in.get(f"{url}?line={a.pk}&line={b.pk}").content.decode()
    assert _button(body) == (
        "2 行で発注する",
        ["{rows} 行・合計 ¥{amount} で発注する", "{rows} 行で発注する"],
        "発注する",
    )
    assert "「必須」の欄と明細を 1 行以上入れて「発注する」を押します。" in body
    assert re.search(r'name="lines-0-qty"[^>]* data-label-qty', body)
    assert re.search(r'name="lines-0-unit_price"[^>]* data-label-price', body)
    # 「行を足す」の雛形の行にも同じ印がある (足した行も JS が数える)。
    assert re.search(r'name="lines-__prefix__-unit_price"[^>]* data-label-price', body)

    # 送って止まった形 (取引先が空): 3 × 1,500 と 2 × 2,000 で 8,500。
    body = logged_in.post(url, _po_post(maker, (item, "3", "1500"), (plate, "2", "2000"), partner=False)).content.decode()
    assert _button(body)[0] == "2 行・合計 ¥8,500 で発注する"

    # 新しく開いた形は「発注する」。
    body = logged_in.get(url).content.decode()
    assert _button(body)[0] == "発注する"


def test_purchase_order_hides_total_without_money(settings, logged_in, user, customer, maker, item, plate):
    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    url = reverse("purchasing:purchaseorder_create")

    body = logged_in.post(url, _po_post(maker, (item, "3", "1500"), (plate, "2", "2000"), partner=False)).content.decode()
    label, template, _plain = _button(body)
    assert label == "2 行で発注する"
    assert template == ["{rows} 行で発注する"]
    assert "¥" not in label


# --- 5. 受入 ---


def test_receipt_writes_qty_and_unit(logged_in, user, maker, item):
    po = purchasing_services.create_purchase_order(
        maker,
        [{"item": item, "qty": Decimal("5"), "unit_price": Decimal("300")}],
        user,
        order_date=ORDER_DATE,
        send=False,
    )
    line = po.lines.get()
    url = reverse("purchasing:receipt_create")

    body = logged_in.get(url, {"line": line.pk}).content.decode()
    assert _button(body) == ("5 個を受け入れる", ["{qty} 個を受け入れる"], "受け入れる")
    assert "「必須」の欄を埋めて「受け入れる」を押します。" in body
    assert f'data-label-line="{line.pk}"' in body

    body = logged_in.get(url).content.decode()
    assert _button(body) == ("受け入れる", None, None)


# --- 6. 入金 ---


def _invoice(user, customer, item):
    line = _line(user, customer, item)
    shipment = shipping_services.ship(line, Decimal("4"), SHIPPED_AT, user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    return billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)


def test_payment_writes_amount(logged_in, user, customer, item):
    invoice = _invoice(user, customer, item)
    url = reverse("billing:payment_create", args=[invoice.pk])

    body = logged_in.get(url).content.decode()
    assert _button(body) == ("入金として記録する", ["¥{amount} を入金として記録する"], "入金として記録する")
    assert re.search(r'name="amount"[^>]* data-label-amount', body)

    # 送って止まった形 (入金日が空) は、送った金額で描き直す。
    body = logged_in.post(url, {"received_on": "", "amount": "12000", "method": "transfer", "note": ""}).content.decode()
    assert _button(body)[0] == "¥12,000 を入金として記録する"


# --- 7. 取消の確かめ ---


def test_cancel_screens_name_the_target(logged_in, user, customer, maker, item):
    order_line = _line(user, customer, item)
    process = masters_services.create_process({"code": "CUT", "name": "作り物の切断", "sort_order": 1, "use_in_plan": True}, user)
    workorder = workorders_services.issue_workorder(
        order_line, Decimal("4"), [{"process": process, "planned_qty": Decimal("4")}], user
    )
    invoice = _invoice(user, customer, item)
    note_line = _line(user, customer, item)
    shipment = shipping_services.ship(note_line, Decimal("2"), SHIPPED_AT, user)
    note = shipping_services.create_delivery_note(customer, [shipment], user)
    po = purchasing_services.create_purchase_order(
        maker,
        [{"item": item, "qty": Decimal("5"), "unit_price": Decimal("300")}],
        user,
        order_date=ORDER_DATE,
        send=False,
    )
    for url, number in (
        (reverse("orders:order_cancel", args=[order_line.order.pk]), order_line.order.order_no),
        (reverse("workorders:workorder_cancel", args=[workorder.pk]), workorder.wo_no),
        (reverse("shipping:deliverynote_cancel", args=[note.pk]), note.note_no),
        (reverse("billing:invoice_cancel", args=[invoice.pk]), invoice.invoice_no),
        (reverse("purchasing:purchaseorder_cancel", args=[po.pk]), po.po_no),
    ):
        body = logged_in.get(url).content.decode()
        assert _button(body, "btn btn-error") == (f"{number} を取り消す", None, None), url
        assert f"{number} を取り消す</button>" in body, url


# --- 8. JS と読み込み ---


def test_js_file_keeps_promises():
    text = JS.read_text(encoding="utf-8")
    for word in (
        '"use strict"',
        'document.addEventListener("input", onInput)',
        'document.addEventListener("change", onInput)',
        'button[data-submit-label]',
        '"data-submit-label-plain"',
        "JSON.parse",
        "[data-label-qty]",
        "[data-label-price]",
        "[data-label-skip]",
        "[data-label-amount]",
        "[data-label-line]",
        "/\\{(rows|qty|amount)\\}/g",
        'normalize("NFKC")',
        'toLocaleString("ja-JP", { maximumFractionDigits: 2 })',
        "node.nodeValue = text",
    ):
        assert word in text, word
    for word in ("classList", "className", "innerHTML", "insertAdjacentHTML", "textContent", "fetch(", "XMLHttpRequest", "window.htmx"):
        assert word not in text, word


def test_base_loads_js_after_modal(logged_in):
    body = logged_in.get(reverse("core:home")).content.decode()
    scripts = re.findall(r'<script src="([^"]+)"', body)
    label = [i for i, src in enumerate(scripts) if src.endswith("js/seizou-submit-label.js")]
    modal = [i for i, src in enumerate(scripts) if src.endswith("js/seizou-modal.js")]
    assert len(label) == 1 and len(modal) == 1
    assert label[0] > modal[0]
    assert "defer" in re.search(r"<script [^>]*js/seizou-submit-label\.js[^>]*>", body).group(0)


def test_js_passes_node_check():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node が無い (node --check を回せない)")
    result = subprocess.run([node, "--check", str(JS)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_js_file_counts_picked_rows():
    # 請求書の新規登録の出荷 (候補 C74): 印を付けた行だけを、行の data- の字で数え、隠れた行は数えない。
    text = JS.read_text(encoding="utf-8")
    for word in ("[data-label-pick]", "data-label-pick-qty", "data-label-pick-amount", ".hidden"):
        assert word in text, word
