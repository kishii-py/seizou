"""請求書の紙の頭の「ご請求金額 (税込)」と支払期日の枠と、合計の下の「お振込先」の枠 (見つけ print-02)。

得意先の経理の人が最初に探す「いくらをいつまでに」を宛名の下に出し、振込先の口座の名義を途中で折らない。
金額は context の合計をそのまま出す (テンプレートも CSS も計算しない)。印 pdf の試験は WeasyPrint と OS の pango が要る。
作り物のデータだけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.billing.reports import invoice_context
from seizou.core import reports
from seizou.core import services as core_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

INVOICE_TEMPLATE = "billing/invoice.html"
ORDER_DATE = date(2026, 11, 2)
ISSUED_ON = date(2026, 11, 30)
# 名義が長い作り物の振込先 (会社の欄の幅では途中で折れていた長さ)
BANK = "作り物銀行 見本支店 普通 0000000 名義 ミホンノカイシャカブシキガイシャ"
AMOUNT_BOX = 'class="report-amount"'
BANK_BOX = 'class="report-bank"'


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _customer(user, code, name, **terms):
    return masters_services.create_partner({"code": code, "name": name, "role": "customer", **terms}, user)


def _invoice(user, customer, item, quantities=("4", "6")):
    line = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}],
        user,
        order_date=ORDER_DATE,
    ).lines.get()
    shipments = [shipped(user, customer, line, qty) for qty in quantities]
    return billing_services.create_invoice(customer, shipments, user, issued_on=ISSUED_ON)


@pytest.fixture
def invoice(user, item):
    """締日 20・翌月 25 日払いの得意先の請求書 (小計 15,000・消費税 1,500・合計 16,500。支払期日 2026-12-25)。"""
    customer = _customer(user, "C001", "作り物の得意先", closing_day=20, payment_month="next", payment_day=25)
    return _invoice(user, customer, item)


@pytest.fixture
def invoice_without_due(user, item):
    """支払の欄の無い得意先の請求書 (支払期日は空)。"""
    return _invoice(user, _customer(user, "C002", "作り物の支払の欄の無い得意先"), item, quantities=("10",))


def _css():
    body, _ = reports.fetch_local(reports.BASE_URL + "static/css/report.css")
    return body.decode("utf-8")


def test_report_css_has_amount_box_and_bank_box():
    """(a) 2 つの枠の規則: 1pt の黒線、金額は 16pt の太字、振込先は語ごとの nowrap で空白の所でだけ折る
    (word-break: keep-all は WeasyPrint 70 が読まず警告を出すので使わない)。"""
    css = _css()
    amount = css[css.index(".report-amount {") : css.index(".report-bank {")]
    assert "border: 1pt solid #000;" in amount
    assert ".report-amount-total {\n  font-size: 16pt;\n  font-weight: 700;\n}" in amount
    bank = css[css.index(".report-bank {") : css.index(".report-docno {")]
    assert "border: 1pt solid #000;" in bank and "page-break-inside: avoid;" in bank
    assert ".report-bank-text span {\n  white-space: nowrap;\n}" in bank
    assert "word-break: keep-all;" not in css  # 注記には書いてよい。規則としては置かない


def _print(client, invoice):
    response = client.get(reverse("billing:invoice_print", args=[invoice.pk]))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_invoice_print_shows_amount_and_due_date_box_under_greeting(logged_in, invoice):
    """(b) 「下記のとおり…」の下、明細の表の上に、ご請求金額 (税込) の合計と支払期日の枠がある。"""
    body = _print(logged_in, invoice)
    box = body[body.index(AMOUNT_BOX) : body.index("</table>", body.index(AMOUNT_BOX))]
    assert "ご請求金額 (税込)" in box
    assert '<td class="report-amount-total">¥16,500</td>' in box
    assert "お支払期日" in box and "2026年12月25日" in box
    assert body.index("下記のとおりご請求申し上げます。") < body.index(AMOUNT_BOX) < body.index('class="report-lines"')
    # 番号などの表には支払期日を出さない (枠にだけ。2 か所に出さない = 本人の決め 11-07 23:3x)
    assert "<th>支払期日</th>" not in body


@pytest.mark.django_db
def test_invoice_print_without_due_date_has_amount_box_only(logged_in, invoice_without_due):
    """(c) 支払期日の無い請求書では、枠に金額の行だけがあり、「支払期日」の字がどこにも無い。"""
    body = _print(logged_in, invoice_without_due)
    assert AMOUNT_BOX in body and "ご請求金額 (税込)" in body
    assert '<td class="report-amount-total">¥16,500</td>' in body
    assert "支払期日" not in body


@pytest.mark.django_db
def test_bank_box_is_under_totals_and_breaks_only_at_spaces(logged_in, user, item, invoice):
    """(d) 振込先は会社の欄でなく、明細の表の下の「お振込先」の枠に、語ごとの span で出る。無ければ枠ごと出ない。"""
    assert BANK_BOX not in _print(logged_in, invoice)

    core_services.update_company(core_services.company(), {"bank_account": BANK}, user)
    # 振込先は発行の時の写しを読む (依頼書 N357。決め 11-08 00:0x)。前に作った請求書の刷り直しには、後で入れた振込先の枠が出ない。
    assert BANK_BOX not in _print(logged_in, invoice)
    # 振込先を入れた後に作った請求書で枠を見る (同じ得意先・同じ期間の 2 枚目は R23 で作れないので、別の作り物の得意先で作る)
    body = _print(logged_in, _invoice(user, _customer(user, "C003", "作り物の二つ目の得意先"), item))
    assert body.index('class="report-lines"') < body.index(BANK_BOX)
    box = body[body.index(BANK_BOX) :]
    assert "お振込先" in box
    for word in BANK.split():
        assert f"<span>{word}</span>" in box, word
    assert "振込先" not in body[: body.index('class="report-lines"')]
    assert "振込先" not in body[body.index('class="report-company"') : body.index('class="report-lines"')]


def _line_texts(box):
    """箱の中の行 (LineBox) ごとの字。"""
    return [
        "".join(text.text for text in line.descendants() if getattr(text, "text", None))
        for line in box.descendants()
        if type(line).__name__ == "LineBox"
    ]


@pytest.mark.pdf
@pytest.mark.django_db
def test_pdf_keeps_each_bank_word_on_one_line(user, item, invoice):
    """(e) PDF でも、振込先の語 (名義のカタカナを含む) は 1 つの行の中に収まる (途中で割れない)。枠は 1 ページ目の合計の下。"""
    core_services.update_company(core_services.company(), {"bank_account": BANK}, user)
    # 振込先は発行の時の写しを読む (依頼書 N357。決め 11-08 00:0x)。振込先を入れた後に、別の作り物の得意先で作った請求書の context で PDF を作る
    after = _invoice(user, _customer(user, "C003", "作り物の二つ目の得意先"), item)
    document = reports._render_document(INVOICE_TEMPLATE, invoice_context(after))

    assert len(document.pages) == 1
    cells = [
        box
        for box in document.pages[0]._page_box.descendants()
        if type(box).__name__ == "TableCellBox" and box.element.get("class") == "report-bank-text"
    ]
    assert len(cells) == 1
    lines = _line_texts(cells[0])
    assert lines, "振込先の枠に行が無い"
    for word in BANK.split():
        assert any(word in line for line in lines), (word, lines)
    texts = [box.text for box in document.pages[0]._page_box.descendants() if getattr(box, "text", None)]
    assert "ご請求金額 (税込)" in texts and "¥16,500" in texts
