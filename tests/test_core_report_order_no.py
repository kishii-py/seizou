"""納品書と請求書の明細の品名の下に、得意先の注文番号を出す (見つけ print-03)。

得意先は自分の注文番号で照合するので、現品票と出荷ラベルに出ている番号を納品書と請求書の明細にも出す。
context の鍵 customer_order_no は受注の customer_order_no の写し (読むだけ。model と migration は変えない)。空なら出さない。
印 pdf の試験は WeasyPrint と OS の pango が要る。作り物のデータだけ。
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

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
CUSTOMER_ORDER_NO = "PO-2611-0007"
ORDER_NO_LINE = f'<p class="report-order-no">注文番号 {CUSTOMER_ORDER_NO}</p>'


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _order_line(user, customer, item, customer_order_no=""):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}],
        user,
        order_date=ORDER_DATE,
        customer_order_no=customer_order_no,
    ).lines.get()


@pytest.fixture
def documents(user, customer, item):
    """注文番号のある受注と無い受注から 1 つずつ出荷し (4 と 6)、1 枚の納品書と 1 枚の請求書に入れる。"""
    with_no = _order_line(user, customer, item, CUSTOMER_ORDER_NO)
    without = _order_line(user, customer, item)
    shipments = [
        shipping_services.ship(with_no, Decimal("4"), SHIPPED_AT, user),
        shipping_services.ship(without, Decimal("6"), SHIPPED_AT, user),
    ]
    note = shipping_services.create_delivery_note(customer, shipments, user)
    for shipment in shipments:
        shipment.refresh_from_db()
    invoice = billing_services.create_invoice(customer, shipments, user, issued_on=ISSUED_ON)
    return {"note": note, "invoice": invoice}


def test_report_css_makes_order_no_small():
    """(a) 品名の下の注文番号は 8pt (列は増やさないので表の幅は変わらない)。"""
    body, _ = reports.fetch_local(reports.BASE_URL + "static/css/report.css")
    assert ".report-lines .report-order-no {\n  font-size: 8pt;" in body.decode("utf-8")


@pytest.mark.django_db
def test_contexts_copy_customer_order_no_per_line(documents):
    """(b) 納品書と請求書の context の各行に customer_order_no (受注の写し。無い受注は空の字) が入る。"""
    for lines in (delivery_note_context(documents["note"])["lines"], invoice_context(documents["invoice"])["lines"]):
        by_qty = {line["quantity"]: line["customer_order_no"] for line in lines}
        assert by_qty == {Decimal("4"): CUSTOMER_ORDER_NO, Decimal("6"): ""}


@pytest.mark.django_db
def test_print_html_shows_order_no_under_item_name_only_when_set(logged_in, documents):
    """(c) 印刷用の画面の品名の枡で、注文番号のある行は品名の下に「注文番号 …」、無い行は品名だけ。"""
    for url in (
        reverse("shipping:deliverynote_print", args=[documents["note"].pk]),
        reverse("billing:invoice_print", args=[documents["invoice"].pk]),
    ):
        body = logged_in.get(url).content.decode()
        assert body.count(f"<td>作り物の軸{ORDER_NO_LINE}</td>") == 1, url
        assert body.count("<td>作り物の軸</td>") == 1, url
        assert body.count("注文番号") == 1, url


@pytest.mark.pdf
@pytest.mark.django_db
def test_delivery_note_pdf_prints_order_no_in_8pt(documents):
    """(d) PDF でも注文番号の行が出て、字の大きさは 8pt (WeasyPrint の長さは px。1pt = 96 / 72 px)。"""
    document = reports._render_document(DELIVERY_NOTE_TEMPLATE, delivery_note_context(documents["note"]))
    assert len(document.pages) == 1
    boxes = [
        box
        for box in document.pages[0]._page_box.descendants()
        if getattr(box, "element_tag", None) == "p" and box.element.get("class") == "report-order-no"
    ]
    assert boxes, "注文番号の行が無い"
    text = "".join(child.text for child in boxes[0].descendants() if getattr(child, "text", None))
    assert text == f"注文番号 {CUSTOMER_ORDER_NO}"
    assert round(boxes[0].style["font_size"] * 72 / 96) == 8
