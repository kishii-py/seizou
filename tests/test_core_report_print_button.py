"""帳票の土台 (core/report_base.html) の上の「印刷」のボタン (初見テスト R5 の候補 C61)。

ボタンは土台の block の外に 1 つだけ置き、report.css で画面で見るときだけ出す (ブラウザの印刷と PDF は print の media)。
見本の納品書の context は scripts/render_sample_report.py の sample_context を使う (中身は作り物)。
印 pdf の試験は WeasyPrint と OS の pango が要る。ほかは WeasyPrint が無くても通る。
ブラウザの画面で出る・隠れる・押すと印刷の窓が開くことは、手で見る。
"""

import importlib.util
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.core import reports
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services

ROOT = Path(__file__).resolve().parent.parent
BUTTON = (
    '<div class="report-actions"><button type="button" class="report-print-button" '
    'onclick="window.print()">印刷</button></div>'
)
PDF_NOTICE_WORDS = "PDF の準備がありません"
ORDER_DATE = date(2026, 11, 2)
DUE_DATE = date(2026, 11, 20)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)


def load_sample_script():
    spec = importlib.util.spec_from_file_location(
        "render_sample_report", ROOT / "scripts" / "render_sample_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SAMPLE = load_sample_script()


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def line(user, customer, item):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": DUE_DATE}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def shipment(user, line):
    return shipping_services.ship(line, Decimal("4"), SHIPPED_AT, user)


@pytest.fixture
def note(user, customer, shipment):
    return shipping_services.create_delivery_note(customer, [shipment], user)


@pytest.fixture
def invoice(user, customer, shipment, note):
    shipment.refresh_from_db()
    return billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)


@pytest.fixture
def workorder(user, line):
    """工程 2 つ (1 = 切断、2 = 曲げ) の指示書。"""
    cut = Process.objects.create(code="CUT", name="切断", sort_order=1)
    bend = Process.objects.create(code="BEND", name="曲げ", sort_order=2)
    return workorders_services.issue_workorder(
        line,
        Decimal("4"),
        [{"process": cut, "planned_qty": Decimal("4")}, {"process": bend, "planned_qty": Decimal("4")}],
        user,
    )


def _unavailable(template, context):
    raise reports.PdfUnavailable("試験のために PDF を作れない形にした")


def test_sample_report_has_one_print_button_above_title():
    """(a) 見本の納品書の HTML に、ボタンが題の上に 1 つだけある。帳票に form は無い。"""
    html = reports.render_html(SAMPLE.TEMPLATE, SAMPLE.sample_context())

    assert html.count(BUTTON) == 1
    assert html.index(BUTTON) < html.index('<h1 class="report-title">')
    assert "<form" not in html


def test_report_css_shows_print_button_on_screen_only():
    """(b) 隠す規則は media の screen の塊より前にあり、塊の中でだけ flex で出す。"""
    body, _ = reports.fetch_local(reports.BASE_URL + "static/css/report.css")
    css = body.decode("utf-8")

    screen = re.search(r"@media screen \{(.*?)\n\}", css, re.S)
    assert screen, "@media screen の塊が無い"
    hide = re.search(r"^\.report-actions \{\s*display: none;\s*\}", css, re.M)
    assert hide, "ボタンの行を隠す規則が無い"
    # 同じ強さの選び方では後に書いた方が勝つので、隠す規則が後にあると画面でも出ない
    assert hide.start() < screen.start()

    show = re.search(r"\.report-actions \{(.*?)\}", screen.group(1), re.S)
    assert show, "media の screen の塊の中にボタンの行の規則が無い"
    assert "display: flex;" in show.group(1)
    assert "display: none" not in show.group(1)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name, target",
    [
        ("billing:invoice_print", "invoice"),
        ("shipping:deliverynote_print", "note"),
        ("shipping:shipment_label", "shipment"),
        ("workorders:workorder_tag", "workorder"),
    ],
)
def test_print_pages_have_one_print_button(request, logged_in, name, target):
    """(c) 請求書・納品書・出荷ラベル・現品票の印刷用の画面に、ボタンが 1 つだけある。"""
    row = request.getfixturevalue(target)

    response = logged_in.get(reverse(name, args=[row.pk]))

    assert response.status_code == 200
    assert response.content.decode().count(BUTTON) == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name, target, patched",
    [
        ("billing:invoice_pdf", "invoice", "seizou.billing.views.render_pdf"),
        ("workorders:workorder_tag_pdf", "workorder", "seizou.core.reports.render_pdf"),
    ],
)
def test_pdf_fallback_pages_have_one_print_button(request, monkeypatch, logged_in, name, target, patched):
    """(c) PDF を作れないときの印刷用の HTML (PDF の準備が無い旨の 1 行の画面) にも、ボタンが 1 つだけある。"""
    row = request.getfixturevalue(target)
    monkeypatch.setattr(patched, _unavailable)

    response = logged_in.get(reverse(name, args=[row.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert PDF_NOTICE_WORDS in body
    assert body.count(BUTTON) == 1


@pytest.mark.pdf
def test_sample_pdf_has_no_print_button():
    """(d) 見本の納品書の PDF は A4 1 枚のままで、どのページにもボタンの箱が無い (PDF は print の media)。"""
    document = reports._render_document(SAMPLE.TEMPLATE, SAMPLE.sample_context())

    assert len(document.pages) == 1
    for page in document.pages:
        tags = {box.element_tag for box in page._page_box.descendants()}
        assert "button" not in tags
