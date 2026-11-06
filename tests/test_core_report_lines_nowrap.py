"""帳票の明細の表で、日付・番号 (出荷番号と品番)・数・見出しを 2 行に割らない (R5 の候補 C60)。

品名の長い B 社の請求書と納品書で、出荷番号が「SH-」と「000009」に割れ、見出しの「数量」が 1 字ずつ縦に並んだ。
report.css の折らない規則を、日付の列だけから番号・数・見出しに広げ、番号の td に class="code" を付けた。
見出しの字と金額の字は期待に書かない (金額の書き方は別の候補が変える)。WeasyPrint が無くても通る (印 pdf を付けない)。
"""

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.core import reports
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

ROOT = Path(__file__).resolve().parent.parent


def load_sample_script():
    spec = importlib.util.spec_from_file_location(
        "render_sample_report", ROOT / "scripts" / "render_sample_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SAMPLE = load_sample_script()


def test_report_css_keeps_lines_codes_numbers_and_headers_on_one_line():
    """(a) 明細の表の折らない規則が、日付・番号・数・見出しの 4 つをまとめた 1 つの規則になっている。"""
    body, _ = reports.fetch_local(reports.BASE_URL + "static/css/report.css")
    css = body.decode("utf-8")
    assert (
        ".report-lines .date,\n.report-lines .code,\n.report-lines .num,\n.report-lines thead th {\n  white-space: nowrap;\n}"
        in css
    )
    # 古い、日付だけの規則が残っていない
    assert ".report-lines .date {" not in css


def test_sample_delivery_note_marks_item_codes():
    """(b) 見本の納品書の品番の枡に class="code" が付く (品番は作り物の MIHON-0001 など)。"""
    context = SAMPLE.sample_context()
    html = reports.render_html(SAMPLE.TEMPLATE, context)
    for line in context["lines"]:
        assert f'<td class="code">{line["item_code"]}</td>' in html


@pytest.fixture
def billed(user):
    """同じ受注の明細から出荷 2 つ (4 と 6) を作り、1 枚の納品書と 1 枚の請求書に入れる。作り物のデータだけ。"""
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    line = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}],
        user,
        order_date=date(2026, 11, 2),
    ).lines.get()
    first = shipping_services.ship(line, Decimal("4"), date(2026, 11, 5), user)
    second = shipping_services.ship(line, Decimal("6"), date(2026, 11, 5), user)
    note = shipping_services.create_delivery_note(customer, [first, second], user)
    first.refresh_from_db()
    second.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [first, second], user, issued_on=date(2026, 11, 30))
    return {"note": note, "invoice": invoice, "shipments": [first, second]}


def _assert_code_cells(body, shipments):
    # 明細 2 行の品番
    assert body.count('<td class="code">T-100</td>') == 2
    for shipment in shipments:
        assert f'<td class="code">{shipment.shipment_no}</td>' in body
    # 日付の列は今のまま
    assert '<td class="date">' in body


@pytest.mark.django_db
def test_invoice_print_marks_shipment_numbers_and_item_codes(logged_in, billed):
    """(c) 請求書の印刷用の画面で、出荷番号と品番の枡に class="code" が付く。"""
    response = logged_in.get(reverse("billing:invoice_print", args=[billed["invoice"].pk]))
    assert response.status_code == 200
    _assert_code_cells(response.content.decode(), billed["shipments"])


@pytest.mark.django_db
def test_delivery_note_print_marks_shipment_numbers_and_item_codes(logged_in, billed):
    """(d) 納品書の印刷用の画面で、出荷番号と品番の枡に class="code" が付く。"""
    response = logged_in.get(reverse("shipping:deliverynote_print", args=[billed["note"].pk]))
    assert response.status_code == 200
    _assert_code_cells(response.content.decode(), billed["shipments"])
