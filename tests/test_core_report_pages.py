"""2 ページ以上の請求書と納品書の紙 (見つけ print-01 と決め 11-07 21:0x)。

小計・消費税・合計 (tfoot) は最後のページだけに出し、各ページの下の端を「書類の番号　n / N」にする。
印 pdf の試験は WeasyPrint と OS の pango が要る (ページごとの字は WeasyPrint の箱の木から拾う。
tests/test_core_report_print_button.py と同じ _page_box の歩き方)。ほかは WeasyPrint が無くても通る。作り物のデータだけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.billing.reports import invoice_context
from seizou.core import reports
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from seizou.shipping.reports import DELIVERY_NOTE_TEMPLATE, delivery_note_context

INVOICE_TEMPLATE = "billing/invoice.html"
ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
SEPARATOR = "　"  # 番号とページの間の全角の空白 (report.css の .report-docno の値)
LINES = 30  # 1 ページに入る明細は 20 行ほどなので、2 ページになる数


@pytest.fixture
def billed(user):
    """同じ受注の明細 (30 個) から出荷 30 個 (各 1 個) を作り、1 枚の納品書と 1 枚の請求書に入れる。作り物のデータだけ。"""
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    line = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(LINES), "unit_price": Decimal("1500")}],
        user,
        order_date=ORDER_DATE,
    ).lines.get()
    shipments = [shipping_services.ship(line, Decimal("1"), SHIPPED_AT, user) for _ in range(LINES)]
    note = shipping_services.create_delivery_note(customer, shipments, user)
    for shipment in shipments:
        shipment.refresh_from_db()
    invoice = billing_services.create_invoice(customer, shipments, user, issued_on=ISSUED_ON)
    return {"note": note, "invoice": invoice}


def _css():
    body, _ = reports.fetch_local(reports.BASE_URL + "static/css/report.css")
    return body.decode("utf-8")


def test_report_css_keeps_totals_on_last_page_and_names_document_in_footer():
    """(a) tfoot を行の群にする規則と、番号の枡の string-set と、下の端の 2 行目の content がある。"""
    css = _css()
    assert ".report-lines tfoot {\n  display: table-row-group;\n  page-break-inside: avoid;\n}" in css
    assert '.report-docno {\n  string-set: report-docno content() "　";\n}' in css
    start = css.index("@bottom-center {")
    bottom = css[start : css.index("font-family", start)]
    # 1 行目はブラウザの印刷の「n / N」のまま (string() を読めない)。2 行目を PDF の道具が使う
    assert 'content: counter(page) " / " counter(pages);' in bottom
    assert 'content: string(report-docno) counter(page) " / " counter(pages);' in bottom
    assert bottom.index("content: counter(page)") < bottom.index("content: string(report-docno)")


@pytest.mark.django_db
def test_print_html_marks_document_number_cell(logged_in, billed):
    """(b) 請求書と納品書の印刷用の画面で、番号の枡 (1 つだけ) に class="report-docno" が付く。"""
    invoice, note = billed["invoice"], billed["note"]
    for url, number in (
        (reverse("billing:invoice_print", args=[invoice.pk]), invoice.invoice_no),
        (reverse("shipping:deliverynote_print", args=[note.pk]), note.note_no),
    ):
        body = logged_in.get(url).content.decode()
        assert body.count('class="report-docno"') == 1, url
        assert f'<td class="report-docno">{number}</td>' in body, url


def _texts(box):
    return [child.text for child in box.descendants() if getattr(child, "text", None)]


def _body_and_footer(page):
    """ページの本文の字 (つなげた物) と、下の中央の枡 (@bottom-center) の字。"""
    body, footer = [], []
    for child in page._page_box.children:
        if type(child).__name__ == "MarginBox":
            if child.at_keyword == "@bottom-center":
                footer = _texts(child)
        else:
            body.extend(_texts(child))
    return "".join(body), "".join(footer)


def _assert_totals_on_last_page_only(document, number, total_labels):
    pages = document.pages
    assert len(pages) >= 2
    for index, page in enumerate(pages, 1):
        body, footer = _body_and_footer(page)
        assert footer == f"{number}{SEPARATOR}{index} / {len(pages)}"
        assert "品番" in body, index  # 明細の見出しは毎ページ
        for label in total_labels:
            assert (label in body) == (index == len(pages)), (label, index)


@pytest.mark.pdf
@pytest.mark.django_db
def test_invoice_pdf_shows_totals_on_last_page_only_and_number_on_every_page(billed):
    """(c) 30 行の請求書の PDF は 2 ページ以上。小計・消費税・合計は最後のページだけ。下の端は毎ページ「番号　n / N」。"""
    invoice = billed["invoice"]
    document = reports._render_document(INVOICE_TEMPLATE, invoice_context(invoice))
    _assert_totals_on_last_page_only(document, invoice.invoice_no, ("小計", "消費税", "合計"))


@pytest.mark.pdf
@pytest.mark.django_db
def test_delivery_note_pdf_shows_total_on_last_page_only_and_number_on_every_page(billed):
    """(d) 30 行の納品書の PDF も同じ。合計は最後のページだけ、下の端は毎ページ「番号　n / N」。"""
    note = billed["note"]
    document = reports._render_document(DELIVERY_NOTE_TEMPLATE, delivery_note_context(note))
    _assert_totals_on_last_page_only(document, note.note_no, ("合計",))


@pytest.mark.pdf
def test_report_without_number_cell_keeps_plain_page_number():
    """(e) 番号の枡の無い帳票 (見本の納品書) の下の端は「1 / 1」のまま (頭に空白が付かない)。"""
    from tests.test_core_reports import SAMPLE

    document = reports._render_document(SAMPLE.TEMPLATE, SAMPLE.sample_context())
    assert len(document.pages) == 1
    _body, footer = _body_and_footer(document.pages[0])
    assert footer == "1 / 1"


# ---------------------------------------------------------------- 明細の 1 行をページの境で割らない

LONG_NAME_LINES = 30


def _long_invoice_context():
    """作り物の請求書の context (表を読まない)。20 行目から品名が 2 行になり、行の規則が無いと 1 行がページの境で上下に分かれる形。"""
    lines = [
        {
            "shipped_at": SHIPPED_AT,
            "shipment_no": f"SH-{i:06d}",
            "item_code": f"MIHON-{i:04d}",
            "item_name": f"見本のフランジ 外径 {80 + i}",
            "quantity": 1,
            "unit": "個",
            "unit_price": 1500,
            "price_unit": "個",
            "amount": 1500,
        }
        for i in range(1, LONG_NAME_LINES + 1)
    ]
    return {
        "company": {"name": "架空精機株式会社", "address": "〒000-0000 見本県架空市見本町 1-2-3", "phone": "000-000-0000"},
        "invoice": {"number": "IV-000123", "issued_on": ISSUED_ON, "period_from": ORDER_DATE, "period_to": ISSUED_ON, "due_on": None},
        "customer": {"name": "架空商事株式会社"},
        "lines": lines,
        "subtotal": 1500 * LONG_NAME_LINES,
        "tax": 150 * LONG_NAME_LINES,
        "total": 1650 * LONG_NAME_LINES,
        "tax_rate_percent": 10,
        "cancelled": False,
    }


def test_report_css_keeps_each_line_on_one_page():
    """(f) 明細の行 (tr) をページの境で割らない規則がある。"""
    assert ".report-lines tr {\n  page-break-inside: avoid;\n}" in _css()


@pytest.mark.pdf
def test_pdf_does_not_split_a_line_across_pages():
    """(g) 品名が 2 行になる明細があっても、同じ明細の行が 2 つのページに分かれて出ない。"""
    document = reports._render_document(INVOICE_TEMPLATE, _long_invoice_context())
    assert len(document.pages) >= 2
    pages_of_row = {}
    for index, page in enumerate(document.pages, 1):
        for table in page._page_box.descendants():
            if type(table).__name__ != "TableBox" or table.element.get("class") != "report-lines":
                continue
            for group in table.children:  # 行の群 (thead・tbody・tfoot)。ほかの表の tr も暗黙の tbody に包まれるので、明細の表だけを見る
                if type(group).__name__ == "TableRowGroupBox" and group.element.tag == "tbody":
                    for row in group.children:
                        pages_of_row.setdefault(id(row.element), set()).add(index)
    assert len(pages_of_row) == LONG_NAME_LINES
    split = [pages for pages in pages_of_row.values() if len(pages) > 1]
    assert split == [], split
