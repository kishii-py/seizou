"""取り消した請求書・納品書・指示書の帳票の題の横に出る「取消」の印 (初見テスト R5 の候補 C58)。

Django の test client で HTML を見る。印 pdf の試験は WeasyPrint と OS の pango が要る。
印の見た目 (黒い枠・「取 消」と割れないか・現品票が A4 の上半分に収まるか) は朝に目で見る。

取消の関数は、行を読み直してロックした別の物を状態を変えて返す (渡した物の status は発行のまま)。
context の関数を直に呼ぶ試験は、取消の関数の戻り値を渡す。URL を開く試験は view が DB から読み直す。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.billing.reports import invoice_context
from seizou.core import reports
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from seizou.shipping.reports import delivery_note_context
from seizou.workorders import services as workorders_services
from seizou.workorders.reports import WORKORDER_TAG_TEMPLATE, workorder_tag_context
from tests.billing_helpers import shipped as _shipped

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
MARK = '<span class="report-cancelled">取消</span>'
MARK_CLASS = "report-cancelled"
PDF_NOTICE_WORDS = "PDF の準備がありません"
CANCEL_REASON = "作り物の取消"


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
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def line(user, customer, item):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def invoice(user, customer, line):
    """発行の請求書 (入金の無い請求書なので取り消せる)。"""
    shipment = _shipped(user, customer, line, "4")
    return billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)


@pytest.fixture
def note(user, customer, line):
    """発行の納品書 (請求書に入っていないので取り消せる)。"""
    shipment = shipping_services.ship(line, Decimal("4"), SHIPPED_AT, user)
    return shipping_services.create_delivery_note(customer, [shipment], user)


@pytest.fixture
def workorder(user, line):
    """発行の指示書 (実績が 0 件なので取り消せる)。"""
    cut = Process.objects.create(code="CUT", name="切断", sort_order=1)
    return workorders_services.issue_workorder(
        line, Decimal("4"), [{"process": cut, "planned_qty": Decimal("4")}], user
    )


def _unavailable(template, context):
    raise reports.PdfUnavailable("試験のために PDF を作れない形にした")


def _title(body):
    """帳票の題の行 (<h1 class="report-title">…</h1>) の中身を返す。"""
    found = re.findall(r'<h1 class="report-title">(.*?)</h1>', body, re.S)
    assert len(found) == 1, found
    return found[0]


def _html(response):
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    return response.content.decode()


# (a) 請求書の印刷用の画面


@pytest.mark.django_db
def test_invoice_print_shows_mark_only_when_cancelled(logged_in, user, invoice):
    url = reverse("billing:invoice_print", args=[invoice.pk])
    assert MARK_CLASS not in _html(logged_in.get(url))

    billing_services.cancel_invoice(invoice, user)

    body = _html(logged_in.get(url))
    assert body.count(MARK) == 1
    assert MARK in _title(body)


# (b) 請求書の PDF を作れないときの HTML (billing の views は render_pdf を名前で import している)


@pytest.mark.django_db
def test_invoice_pdf_fallback_shows_mark_when_cancelled(monkeypatch, logged_in, user, invoice):
    billing_services.cancel_invoice(invoice, user)
    monkeypatch.setattr("seizou.billing.views.render_pdf", _unavailable)

    body = _html(logged_in.get(reverse("billing:invoice_pdf", args=[invoice.pk])))
    assert PDF_NOTICE_WORDS in body
    assert body.count(MARK) == 1


# (c) 納品書の印刷用の画面と、PDF を作れないときの HTML


@pytest.mark.django_db
def test_delivery_note_print_shows_mark_only_when_cancelled(logged_in, user, note):
    url = reverse("shipping:deliverynote_print", args=[note.pk])
    assert MARK_CLASS not in _html(logged_in.get(url))

    shipping_services.cancel_delivery_note(note, user)

    body = _html(logged_in.get(url))
    assert body.count(MARK) == 1
    assert MARK in _title(body)


@pytest.mark.django_db
def test_delivery_note_pdf_fallback_shows_mark_when_cancelled(monkeypatch, logged_in, user, note):
    shipping_services.cancel_delivery_note(note, user)
    monkeypatch.setattr("seizou.core.reports.render_pdf", _unavailable)

    body = _html(logged_in.get(reverse("shipping:deliverynote_pdf", args=[note.pk])))
    assert PDF_NOTICE_WORDS in body
    assert body.count(MARK) == 1


# (d) 現品票と、PDF を作れないときの HTML


@pytest.mark.django_db
def test_workorder_tag_shows_mark_only_when_cancelled(logged_in, user, workorder):
    url = reverse("workorders:workorder_tag", args=[workorder.pk])
    assert MARK_CLASS not in _html(logged_in.get(url))

    workorders_services.cancel_workorder(workorder, user, reason=CANCEL_REASON)

    body = _html(logged_in.get(url))
    assert body.count(MARK) == 1
    assert MARK in _title(body)


@pytest.mark.django_db
def test_workorder_tag_pdf_fallback_shows_mark_when_cancelled(monkeypatch, logged_in, user, workorder):
    workorders_services.cancel_workorder(workorder, user, reason=CANCEL_REASON)
    monkeypatch.setattr("seizou.core.reports.render_pdf", _unavailable)

    body = _html(logged_in.get(reverse("workorders:workorder_tag_pdf", args=[workorder.pk])))
    assert PDF_NOTICE_WORDS in body
    assert body.count(MARK) == 1


# (e) 3 つの帳票の context の cancelled


@pytest.mark.django_db
def test_contexts_say_cancelled(user, invoice, note, workorder):
    assert invoice_context(invoice)["cancelled"] is False
    assert delivery_note_context(note)["cancelled"] is False
    assert workorder_tag_context(workorder)["cancelled"] is False

    cancelled_invoice = billing_services.cancel_invoice(invoice, user)
    cancelled_note = shipping_services.cancel_delivery_note(note, user)
    cancelled_workorder = workorders_services.cancel_workorder(workorder, user, reason=CANCEL_REASON)

    assert invoice_context(cancelled_invoice)["cancelled"] is True
    assert delivery_note_context(cancelled_note)["cancelled"] is True
    assert workorder_tag_context(cancelled_workorder)["cancelled"] is True


# (f) 取消の後も、詳細の印刷用の画面と PDF の link は残り、開ける (隠さない・断らない)


def _assert_links_open(client, detail_url, urls):
    body = _html(client.get(detail_url))
    for url in urls:
        assert f'href="{url}"' in body, url
        assert client.get(url).status_code == 200, url


@pytest.mark.django_db
def test_cancelled_invoice_detail_keeps_report_links(monkeypatch, logged_in, user, invoice):
    billing_services.cancel_invoice(invoice, user)
    monkeypatch.setattr("seizou.billing.views.render_pdf", _unavailable)

    _assert_links_open(
        logged_in,
        reverse("billing:invoice_detail", args=[invoice.pk]),
        [reverse("billing:invoice_print", args=[invoice.pk]), reverse("billing:invoice_pdf", args=[invoice.pk])],
    )


@pytest.mark.django_db
def test_cancelled_delivery_note_detail_keeps_report_links(monkeypatch, logged_in, user, note):
    shipping_services.cancel_delivery_note(note, user)
    monkeypatch.setattr("seizou.core.reports.render_pdf", _unavailable)

    _assert_links_open(
        logged_in,
        reverse("shipping:deliverynote_detail", args=[note.pk]),
        [reverse("shipping:deliverynote_print", args=[note.pk]), reverse("shipping:deliverynote_pdf", args=[note.pk])],
    )


@pytest.mark.django_db
def test_cancelled_workorder_detail_keeps_tag_links(monkeypatch, logged_in, user, workorder):
    workorders_services.cancel_workorder(workorder, user, reason=CANCEL_REASON)
    monkeypatch.setattr("seizou.core.reports.render_pdf", _unavailable)

    _assert_links_open(
        logged_in,
        reverse("workorders:workorder_detail", args=[workorder.pk]),
        [
            reverse("workorders:workorder_tag", args=[workorder.pk]),
            reverse("workorders:workorder_tag_pdf", args=[workorder.pk]),
        ],
    )


# (g) cancelled を渡さない帳票 (出荷ラベル) には出ない。取消の納品書に入った出荷のラベルでも出ない


@pytest.mark.django_db
def test_shipment_label_has_no_mark(logged_in, user, note):
    shipment = note.shipments.get()
    shipping_services.cancel_delivery_note(note, user)

    body = _html(logged_in.get(reverse("shipping:shipment_label", args=[shipment.pk])))
    assert MARK_CLASS not in body


# (h) 印が題の行の高さを増やしても、取消の指示書の現品票は A4 縦の 1 枚に収まる


@pytest.mark.pdf
@pytest.mark.django_db
def test_cancelled_tag_pdf_is_single_a4_page(user, line):
    processes = [
        Process.objects.create(code=f"P{n}", name=f"作り物の工程{n}", sort_order=n) for n in range(1, 6)
    ]
    five = workorders_services.issue_workorder(
        line, Decimal("4"), [{"process": p, "planned_qty": Decimal("4")} for p in processes], user
    )
    cancelled = workorders_services.cancel_workorder(five, user, reason=CANCEL_REASON)
    scan_url = "http://testserver" + reverse("workorders:workorder_scan", args=[cancelled.pk])
    context = {**workorder_tag_context(cancelled), "qr_text": scan_url}
    assert MARK in reports.render_html(WORKORDER_TAG_TEMPLATE, context)

    document = reports._render_document(WORKORDER_TAG_TEMPLATE, context)

    assert len(document.pages) == 1
    page = document.pages[0]
    # A4 縦 = 210mm × 297mm (WeasyPrint の長さは CSS の px。1mm = 96 / 25.4 px)
    assert round(page.width * 25.4 / 96) == 210
    assert round(page.height * 25.4 / 96) == 297
