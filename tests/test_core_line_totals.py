"""明細の合計の枠 (行の数・数量・金額) と、保存の釦の字を同じ元から出す (N149。案 5-A)。

- core の口 = core/forms.py の line_totals と、断片 core/_line_totals.html。
- 受注の入力 (新規登録と編集) の枠と釦の字。開いた形・送って止まった形・消す印の行・金額の閲覧が無い人。
- 発注の入力と一括出荷の小窓の枠 (一括出荷は単価が無いので「金額」の組が無い)。
- static の js/seizou-submit-label.js の枠の約束 (ブラウザで動かさず字で見る。ブラウザでの確かめは E-46 と本体の手の確かめ)。
作り物のデータだけを使う。作り物の作り方と _button・MODAL・NO_MONEY の形は tests/test_core_submit_label.py を写した。
"""

import html
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse

import seizou.core
from seizou.core.forms import line_totals
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services

ORDER_DATE = date(2026, 11, 2)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
NO_MONEY = {"department": "off", "write": "off", "money": "on"}
JS = Path(seizou.core.__file__).resolve().parent / "static" / "js" / "seizou-submit-label.js"
#: 枠の箱の印 (rows・qty・amount の span の印と分ける)
FRAME = re.compile(r"data-line-totals(?=[\s>])")


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


def _two_line_order(user, customer, item, plate):
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


def _button(body):
    """入力のカードの主の保存の釦。(釦の字, data-submit-label の型の並び か None, data-submit-label-plain の字 か None)。"""
    match = re.search(r'<button class="btn btn-primary" type="submit"([^>]*)>(.*?)</button>', body, re.S)
    assert match, "保存の釦が無い"
    attrs, inner = match.group(1), match.group(2)
    label = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
    template = re.search(r'data-submit-label="([^"]*)"', attrs)
    plain = re.search(r'data-submit-label-plain="([^"]*)"', attrs)
    return (
        label,
        json.loads(html.unescape(template.group(1))) if template else None,
        html.unescape(plain.group(1)) if plain else None,
    )


def _frame(body):
    """合計の枠の字。{"rows": 字, "qty": 字, "amount": 字 か None (金額の組が無い)}。枠はちょうど 1 つ。"""
    assert len(FRAME.findall(body)) == 1, "合計の枠が 1 つでない"
    found = {}
    for name in ("rows", "qty", "amount"):
        match = re.search(rf"data-line-totals-{name}>([^<]*)</span>", body)
        found[name] = match.group(1) if match else None
    return found


def _order_post(customer, *rows, initial=0, order_lines=()):
    """受注の入力の POST の値。rows は (部品, 数量, 単価, 消す印) の並び。order_lines は既にある明細 (前から順に)。"""
    data = {
        "customer": str(customer.pk) if customer is not None else "",
        "order_date": ORDER_DATE.isoformat(),
        "customer_order_no": "",
        "lines-TOTAL_FORMS": str(len(rows)),
        "lines-INITIAL_FORMS": str(initial),
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
    }
    for index, (part, qty, price, delete) in enumerate(rows):
        data.update(
            {
                f"lines-{index}-order_line": str(order_lines[index].pk) if index < len(order_lines) else "",
                f"lines-{index}-item": str(part.pk),
                f"lines-{index}-customer_part_no": "",
                f"lines-{index}-qty": qty,
                f"lines-{index}-unit": "",
                f"lines-{index}-unit_price": price,
                f"lines-{index}-due_date": "",
                f"lines-{index}-note": "",
            }
        )
        if delete:
            data[f"lines-{index}-DELETE"] = "on"
    return data


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


# --- 1. core の口 ---


def test_line_totals_counts_rows_qty_amount():
    assert line_totals([("10", "100"), ("２０", "50")]) == {"rows": 2, "qty": "30", "amount": "2,000", "show_amount": True}
    assert line_totals([("10", None)])["amount"] is None
    assert line_totals([("10", "abc")])["amount"] is None
    for unknown in ([("abc", "1")], None):
        found = line_totals(unknown)
        assert (found["rows"], found["qty"], found["amount"]) == (None, None, None), unknown
    assert line_totals([("10", "100")], show_amount=False) == {"rows": 1, "qty": "10", "amount": None, "show_amount": False}
    assert line_totals([]) == {"rows": 0, "qty": "0", "amount": "0", "show_amount": True}
    # 丸めは釦の字と同じ小数 2 桁の四捨五入 (保存の 1 円の丸めは services)。
    assert line_totals([("1.005", "1")]) == {"rows": 1, "qty": "1.01", "amount": "1.01", "show_amount": True}


# --- 2. 受注の入力 ---


def test_order_create_shows_zero_frame_and_label(logged_in):
    body = logged_in.get(reverse("orders:order_create")).content.decode()
    assert _frame(body) == {"rows": "0", "qty": "0", "amount": "0"}
    assert _button(body) == ("受注する", ["{rows} 行・合計 ¥{amount} で受注する", "{rows} 行で受注する"], "受注する")
    assert "「必須」の欄と明細の行を埋めて「受注する」を押します。" in body
    assert re.search(r'name="lines-0-qty"[^>]* data-label-qty', body)
    assert re.search(r'name="lines-0-unit_price"[^>]* data-label-price', body)
    assert re.search(r'name="lines-0-DELETE"[^>]* data-label-skip', body)
    # 「行を足す」の雛形の行にも同じ印がある (足した行も JS が数え、消す印の行を飛ばす)。
    assert re.search(r'name="lines-__prefix__-DELETE"[^>]* data-label-skip', body)
    assert re.search(r'name="lines-__prefix__-qty"[^>]* data-label-qty', body)
    # 端数の 1 文は枠の下に残る。
    assert "金額は数量 × 単価で、保存のときに得意先の端数の扱いで 1 円に丸めます。" in body


def test_order_update_shows_totals_from_lines(logged_in, user, customer, item, plate):
    order = _two_line_order(user, customer, item, plate)
    body = logged_in.get(reverse("orders:order_update", args=[order.pk])).content.decode()
    assert _frame(body) == {"rows": "2", "qty": "30", "amount": "2,000"}
    assert _button(body) == ("2 行・合計 ¥2,000 で保存", ["{rows} 行・合計 ¥{amount} で保存", "{rows} 行で保存"], "保存")
    assert "「必須」の欄と明細の行を埋めて「保存」を押します。" in body


def test_order_post_stopped_shows_sent_values(logged_in, item):
    url = reverse("orders:order_create")

    # 得意先が空で止まった形: 送った 3 × 1,500 で描き直す。
    response = logged_in.post(url, _order_post(None, (item, "3", "1500", False)))
    assert response.status_code == 200
    body = response.content.decode()
    assert _frame(body) == {"rows": "1", "qty": "3", "amount": "4,500"}
    assert _button(body)[0] == "1 行・合計 ¥4,500 で受注する"

    # 数量に数でない字を入れて止まった形: 枠は 3 つとも「—」、釦は動詞だけ。
    body = logged_in.post(url, _order_post(None, (item, "abc", "1500", False))).content.decode()
    assert _frame(body) == {"rows": "—", "qty": "—", "amount": "—"}
    assert _button(body)[0] == "受注する"


def test_order_post_skips_deleted_lines(logged_in, user, customer, item, plate):
    order = _two_line_order(user, customer, item, plate)
    lines = list(order.lines.order_by("line_no"))
    data = _order_post(
        None, (item, "10", "100", False), (plate, "20", "50", True), initial=2, order_lines=lines
    )
    response = logged_in.post(reverse("orders:order_update", args=[order.pk]), data)
    assert response.status_code == 200
    body = response.content.decode()
    # 消す印の行は数えない (JS の data-label-skip と同じ)。
    assert _frame(body) == {"rows": "1", "qty": "10", "amount": "1,000"}
    assert _button(body)[0] == "1 行・合計 ¥1,000 で保存"


def test_order_hides_amount_without_money(settings, logged_in, user, customer, item, plate):
    settings.SEIZOU = {"access_control": dict(NO_MONEY)}

    body = logged_in.get(reverse("orders:order_create")).content.decode()
    assert _frame(body) == {"rows": "0", "qty": "0", "amount": None}
    assert "data-line-totals-amount" not in body
    assert "金額 <span" not in body
    label, template, plain = _button(body)
    assert (label, template, plain) == ("受注する", ["{rows} 行で受注する"], "受注する")

    # 編集の形: 明細の初期値に単価があっても金額を出さない。
    order = _two_line_order(user, customer, item, plate)
    body = logged_in.get(reverse("orders:order_update", args=[order.pk])).content.decode()
    assert _frame(body) == {"rows": "2", "qty": "30", "amount": None}
    assert "金額 <span" not in body
    assert _button(body)[:2] == ("2 行で保存", ["{rows} 行で保存"])
    assert "¥" not in _button(body)[0]


# --- 3. 発注 ---


def test_purchase_order_shows_totals(logged_in, user, customer, maker, item, plate):
    a = _line(user, customer, item)
    b = _line(user, customer, plate)
    url = reverse("purchasing:purchaseorder_create")

    # 盤で選んだ明細から開いた形: 単価が空なので金額は「—」。
    body = logged_in.get(f"{url}?line={a.pk}&line={b.pk}").content.decode()
    assert _frame(body) == {"rows": "2", "qty": "20", "amount": "—"}
    assert _button(body)[0] == "2 行で発注する"

    # 送って止まった形 (取引先が空): 3 × 1,500 と 2 × 2,000 で 8,500。釦の字と同じ数。
    body = logged_in.post(url, _po_post(maker, (item, "3", "1500"), (plate, "2", "2000"), partner=False)).content.decode()
    assert _frame(body) == {"rows": "2", "qty": "5", "amount": "8,500"}
    assert _button(body)[0] == "2 行・合計 ¥8,500 で発注する"


def test_purchase_order_hides_amount_without_money(settings, logged_in, maker, item, plate):
    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    url = reverse("purchasing:purchaseorder_create")

    body = logged_in.post(url, _po_post(maker, (item, "3", "1500"), (plate, "2", "2000"), partner=False)).content.decode()
    assert _frame(body) == {"rows": "2", "qty": "5", "amount": None}
    assert "金額 <span" not in body
    assert _button(body)[0] == "2 行で発注する"


# --- 4. 一括出荷 ---


def test_bulk_ship_shows_rows_and_qty_without_amount(logged_in, user, customer, item):
    a = _line(user, customer, item)
    b = _line(user, customer, item)
    url = reverse("shipping:shipment_bulk_create")

    body = logged_in.get(f"{url}?line={a.pk}&line={b.pk}", **MODAL).content.decode()
    assert _frame(body) == {"rows": "2", "qty": "20", "amount": None}
    assert "金額 <span" not in body
    assert _button(body)[0] == "2 行・合計 20 個を出荷する"


# --- 5. 枠を置く画面 ---


def test_cards_include_frame_once(logged_in, user, customer, item):
    a = _line(user, customer, item)
    b = _line(user, customer, item)
    for url in (
        reverse("orders:order_create"),
        reverse("purchasing:purchaseorder_create"),
        f"{reverse('shipping:shipment_bulk_create')}?line={a.pk}&line={b.pk}",
    ):
        body = logged_in.get(url).content.decode()
        assert len(FRAME.findall(body)) == 1, url


# --- 6. JS ---


def test_js_file_keeps_totals_promises():
    text = JS.read_text(encoding="utf-8")
    for word in (
        "[data-line-totals]",
        "[data-line-totals-rows]",
        "[data-line-totals-qty]",
        "[data-line-totals-amount]",
        "found.totals",
        "setTotals(form, found.totals)",
        "fill(templates, found)",
    ):
        assert word in text, word
    for word in ("textContent", "innerHTML", "classList", "className", "insertAdjacentHTML", "fetch(", "XMLHttpRequest", "window.htmx"):
        assert word not in text, word
