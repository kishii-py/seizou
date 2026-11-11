"""帳票の写し (core.ReportCopy。R34 の 2 段目) を開く 2 つの口 (core:report_copy・core:report_copy_pdf) と、詳細の画面の「発行の時の写し」の枠。

- (a) 請求書の詳細に枠が出る (行の帳票の名前・日時・作った人・「開く」と「PDF」の link・説明の 1 行・「削除」が無い・添付ファイルの枠より上)。
- (b) 納品書の詳細と発注の詳細にも出る。発注を変えて第 2 版にした後は 2 行で、2 行目が「第 2 版」。
- (c) 写しの無い記録の詳細 (受注の詳細・写しの行を消した請求書の詳細) には出ない。
- (d) report_copy は写しの html と同じ字を返す (発行の後に受注の単価を直しても発行の時の単価のまま)。
- (e) report_copy_pdf は PDF を返す (印 pdf)。WeasyPrint の無い形では知らせの 1 行を入れた HTML を返し、表の字は変えない。
- (f) 金額の閲覧の判定が on で金額の閲覧の無い人は、2 つの口が金額の門の画面 (403) で、詳細に枠が出ない。log では開けて記録に 1 行。ログインしていない人はログインへ。
- (g) 写しを読む問い合わせは、写しを持つ 3 つの model (keeps_report_copy) の詳細だけで 1 回。ほかの詳細と小窓では増えない。
取引先・部品・会社の値は作り物だけ。相手への HTTP は偽の物に差し替える (外へは出ない)。開発サーバーは起こさない (本文の字と位置で見る)。
"""

import logging
import re
import sys
from datetime import date
from decimal import Decimal

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.accounts import services as accounts_services
from seizou.billing import services as billing_services
from seizou.billing.models import Invoice
from seizou.connect import services as connect_services
from seizou.core import services as core_services
from seizou.core.models import ReportCopy
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrder
from seizou.shipping import services as shipping_services
from seizou.shipping.models import DeliveryNote
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ORDER_DATE = date(2026, 10, 1)
SHIPPED_AT = date(2026, 10, 10)
ISSUED_ON = date(2026, 10, 31)
PASSWORD = "kumo-tests-2611"
ENV_NAME = "SEIZOU_PARTNER_TESTCOPYVIEW_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"
ALL_ON = {"department": "on", "write": "on", "money": "on"}
ALL_LOG = {"department": "log", "write": "log", "money": "log"}

PANEL_HEADING = "発行の時の写し"
PANEL_TEXT = "発行した時に残した帳票です。後で受注や部品マスターを直しても、取り消した後も、発行した時の形のまま開きます (取消の印は付きません)。"
PDF_NOTICE_HTML = '<p role="alert">PDF の準備がありません。写しの画面をお使いください</p>'
HTMX = {"HTTP_HX_REQUEST": "true"}


# ---------------------------------------------------------------- 道具


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-410", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def no_http(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


@pytest.fixture
def fake_post(monkeypatch):
    post = FakePost()
    monkeypatch.setattr("requests.post", post)
    return post


def _customer(user, code="C001"):
    return masters_services.create_partner({"code": code, "name": f"作り物の得意先 {code}", "role": "customer"}, user)


def _line(user, customer, item, qty="10", unit_price="1500"):
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price)}], user, order_date=ORDER_DATE
    )
    return order.lines.get()


def _ship(user, line, qty):
    shipment = shipping_services.ship(line, Decimal(qty), SHIPPED_AT, user, send=False)
    shipment.refresh_from_db()
    return shipment


def _invoice(user, item, code="C001"):
    customer = _customer(user, code)
    shipment = _ship(user, _line(user, customer, item), "10")
    return billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)


def _note(user, item, code="C002", unit_price="1650"):
    customer = _customer(user, code)
    line = _line(user, customer, item, qty="50", unit_price=unit_price)
    shipment = _ship(user, line, "50")
    return shipping_services.create_delivery_note(customer, [shipment], user, issued_on=SHIPPED_AT), line


def _subcontractor(user, code, *, connected=False, monkeypatch=None):
    partner = masters_services.create_partner({"code": code, "name": f"作り物の協力企業 {code}", "role": "subcontractor"}, user)
    if connected:
        monkeypatch.setenv(ENV_NAME, _new_secret())
        connect_services.register_connection(partner, PARTNER_URL, ENV_NAME, "send", user)
    return partner


def _purchase_order(user, partner, item, qty="37"):
    return purchasing_services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("250"), "due_date": date(2026, 11, 20)}],
        user,
        order_date=ORDER_DATE,
    )


def _account(login_id, *, permissions=(), **flags):
    accounts_services.issue_account(login_id, PASSWORD, [], list(permissions), flags, None)
    return get_user_model().objects.get(username=login_id)


def _copies(obj):
    return list(core_services.report_copies(obj))


def _body(client, name, obj):
    response = client.get(reverse(name, args=[obj.pk]))
    assert response.status_code == 200, name
    return response.content.decode()


def _panel(body):
    """本文の中の「発行の時の写し」の枠 (section) の字。無ければ None。"""
    match = re.search(r'<section id="report-copies".*?</section>', body, re.S)
    return match.group(0) if match else None


def _copy_urls(copy):
    return reverse("core:report_copy", args=[copy.pk]), reverse("core:report_copy_pdf", args=[copy.pk])


def _rows(panel):
    return re.findall(r"<tr class=\"hover:bg-base-200\">(.*?)</tr>", panel, re.S)


def _access_records(caplog):
    return [r for r in caplog.records if r.name == "seizou.access"]


def _copy_queries(client, url, **extra):
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url, **extra)
    assert response.status_code == 200, url
    return [q["sql"] for q in queries.captured_queries if "core_reportcopy" in q["sql"]]


# ---------------------------------------------------------------- (a) 請求書の詳細の枠


@pytest.mark.django_db
def test_a_invoice_detail_shows_the_copy_panel_above_attachments(client, user, item):
    invoice = _invoice(user, item)
    [copy] = _copies(invoice)
    client.force_login(user)

    body = _body(client, "billing:invoice_detail", invoice)

    panel = _panel(body)
    assert panel is not None
    assert PANEL_HEADING in panel
    assert PANEL_TEXT in panel
    assert '<span class="badge badge-sm tabular-nums">1</span>' in panel  # 件数の印
    [row] = _rows(panel)
    assert "請求書" in row
    assert "第 " not in row  # 版が 1 なら版の字は出さない
    assert timezone.localtime(copy.created_at).strftime("%Y-%m-%d %H:%M") in row
    assert "tester" in row
    for url in _copy_urls(copy):
        assert f'href="{url}" target="_blank" rel="noopener"' in row, url
    assert "開く</a>" in row
    assert "PDF</a>" in row
    assert "削除" not in panel  # 写しを消す口は作らない
    # 枠は添付ファイルの枠より上
    assert body.index('id="report-copies"') < body.index('id="attachments"')


# ---------------------------------------------------------------- (b) 納品書と発注の詳細の枠


@pytest.mark.django_db
def test_b_delivery_note_and_purchase_order_details_show_the_panel(client, user, item, fake_post, monkeypatch):
    note, _ = _note(user, item)
    partner = _subcontractor(user, "S001", connected=True, monkeypatch=monkeypatch)
    order = _purchase_order(user, partner, item)
    assert order.status == PurchaseOrder.STATUS_SENT
    client.force_login(user)

    note_panel = _panel(_body(client, "shipping:deliverynote_detail", note))
    [note_copy] = _copies(note)
    [row] = _rows(note_panel)
    assert "納品書" in row
    for url in _copy_urls(note_copy):
        assert f'href="{url}" target="_blank" rel="noopener"' in row, url

    [first] = _copies(order)
    [row] = _rows(_panel(_body(client, "purchasing:purchaseorder_detail", order)))
    assert "発注書" in row and "第 " not in row

    line = order.lines.get()
    changed = purchasing_services.change_purchase_order(order, {}, [{"line": line, "qty": Decimal("41")}], user)
    assert changed.revision_no == 2

    panel = _panel(_body(client, "purchasing:purchaseorder_detail", changed))
    assert '<span class="badge badge-sm tabular-nums">2</span>' in panel
    old_row, new_row = _rows(panel)
    old, new = _copies(changed)
    assert old.pk == first.pk
    assert "第 " not in old_row
    assert "発注書 第 2 版" in new_row
    for url in _copy_urls(new):
        assert f'href="{url}" target="_blank" rel="noopener"' in new_row, url


# ---------------------------------------------------------------- (c) 写しの無い記録には枠を出さない


@pytest.mark.django_db
def test_c_records_without_copies_show_no_panel(client, user, item):
    invoice = _invoice(user, item)
    client.force_login(user)

    order_body = _body(client, "orders:order_detail", invoice.lines.first().shipment.order_line.order)
    assert _panel(order_body) is None
    assert PANEL_HEADING not in order_body

    # この機能より前に発行した記録の代わり (試験の中だけで写しの行を消す。消す口は作らない)
    ReportCopy.objects.filter(model="billing.invoice", object_pk=invoice.pk).delete()
    invoice_body = _body(client, "billing:invoice_detail", invoice)
    assert _panel(invoice_body) is None
    assert PANEL_HEADING not in invoice_body
    assert 'id="attachments"' in invoice_body  # 添付ファイルの枠は今のまま出る


# ---------------------------------------------------------------- (d) 写しの HTML の口


@pytest.mark.django_db
def test_d_report_copy_returns_the_html_of_the_issue(client, user, item):
    note, line = _note(user, item, unit_price="1650")
    [copy] = _copies(note)
    assert "¥1,650" in copy.html

    # 請求書は作らずに受注の単価を直す (印刷用の画面は今の単価になる)
    orders_services.update_order(line.order, {}, [{"id": line.pk, "unit_price": Decimal("1700")}], user)
    client.force_login(user)
    assert "¥1,700" in _body(client, "shipping:deliverynote_print", note)

    response = client.get(reverse("core:report_copy", args=[copy.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "text/html; charset=utf-8"
    body = response.content.decode()
    assert body == copy.html
    assert "¥1,650" in body
    assert "¥1,700" not in body


@pytest.mark.django_db
def test_d_missing_copy_is_404(client, user):
    client.force_login(user)
    for name in ("core:report_copy", "core:report_copy_pdf"):
        assert client.get(reverse(name, args=[999999])).status_code == 404, name


# ---------------------------------------------------------------- (e) 写しの PDF の口


@pytest.mark.pdf
@pytest.mark.django_db
def test_e_report_copy_pdf_returns_pdf(client, user, item):
    invoice = _invoice(user, item)
    [copy] = _copies(invoice)
    client.force_login(user)

    response = client.get(reverse("core:report_copy_pdf", args=[copy.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == f'inline; filename="{invoice.invoice_no}-1.pdf"'
    assert response.content.startswith(b"%PDF")


@pytest.mark.django_db
def test_e_report_copy_pdf_without_weasyprint_returns_html_with_notice(client, user, item, monkeypatch):
    for name in list(sys.modules):
        if name.startswith("weasyprint."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "weasyprint", None)
    invoice = _invoice(user, item)
    [copy] = _copies(invoice)
    before = copy.html
    client.force_login(user)

    response = client.get(reverse("core:report_copy_pdf", args=[copy.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "text/html; charset=utf-8"
    body = response.content.decode()
    assert f"<body>{PDF_NOTICE_HTML}" in body  # <body> の直後
    assert body.count(PDF_NOTICE_HTML) == 1
    assert body.replace(PDF_NOTICE_HTML, "", 1) == before
    copy.refresh_from_db()
    assert copy.html == before  # 表の字は変えない
    assert PDF_NOTICE_HTML not in copy.html


# ---------------------------------------------------------------- (f) 金額の門とログイン


@pytest.mark.django_db
def test_f_money_on_refuses_both_gates_and_hides_the_panel(settings, client, user, item):
    invoice = _invoice(user, item)
    note, _ = _note(user, item)
    [invoice_copy] = _copies(invoice)
    [note_copy] = _copies(note)
    settings.SEIZOU = {"access_control": dict(ALL_ON)}

    client.force_login(_account("jimu", permissions=["billing", "shipping"]))
    for copy in (invoice_copy, note_copy):
        for url in _copy_urls(copy):
            response = client.get(url)
            assert response.status_code == 403, url
            html = response.content.decode()
            assert "この書面は開けません" in html
            assert f"{copy.title}の写しは金額が主な内容のため、金額の閲覧の権限が無い方は開けません。" in html
            assert copy.html not in html
    for name, obj, copy in (
        ("billing:invoice_detail", invoice, invoice_copy),
        ("shipping:deliverynote_detail", note, note_copy),
    ):
        body = _body(client, name, obj)
        assert _panel(body) is None, name
        assert PANEL_HEADING not in body, name
        for url in _copy_urls(copy):
            assert url not in body, url

    client.force_login(_account("keiri", permissions=["billing", "shipping"], see_money=True))
    assert _panel(_body(client, "billing:invoice_detail", invoice)) is not None
    assert client.get(reverse("core:report_copy", args=[invoice_copy.pk])).content.decode() == invoice_copy.html


@pytest.mark.django_db
def test_f_money_log_opens_and_records_one_line(settings, client, user, item, caplog):
    invoice = _invoice(user, item)
    [copy] = _copies(invoice)
    settings.SEIZOU = {"access_control": dict(ALL_LOG)}
    client.force_login(_account("jimu"))
    assert _panel(_body(client, "billing:invoice_detail", invoice)) is not None
    caplog.set_level(logging.WARNING, logger="seizou.access")
    caplog.clear()

    response = client.get(reverse("core:report_copy", args=[copy.pk]))

    assert response.status_code == 200
    assert response.content.decode() == copy.html
    [record] = _access_records(caplog)
    assert record.getMessage() == "記録だけ (金額の閲覧): jimu が 請求書の写しを開いた (金額の閲覧)"


@pytest.mark.django_db
def test_f_signed_out_goes_to_login(client, user, item):
    invoice = _invoice(user, item)
    [copy] = _copies(invoice)

    for url in _copy_urls(copy):
        response = client.get(url)
        assert response.status_code == 302, url
        assert response["Location"].startswith(reverse("core:login")), url


# ---------------------------------------------------------------- (g) 問い合わせは写しを持つ詳細だけ


def test_g_only_three_models_keep_report_copies():
    keeping = {model._meta.label_lower for model in apps.get_models() if getattr(model, "keeps_report_copy", False)}
    assert keeping == {"billing.invoice", "shipping.deliverynote", "purchasing.purchaseorder"}
    assert Invoice.keeps_report_copy is True
    assert DeliveryNote.keeps_report_copy is True
    assert PurchaseOrder.keeps_report_copy is True


@pytest.mark.django_db
def test_g_copy_query_runs_once_only_on_details_that_keep_copies(client, user, item, no_http):
    invoice = _invoice(user, item)
    note, _ = _note(user, item)
    order = _purchase_order(user, _subcontractor(user, "S002"), item)
    client.force_login(user)

    order_detail = reverse("orders:order_detail", args=[invoice.lines.first().shipment.order_line.order_id])
    assert _copy_queries(client, order_detail) == []
    for name, obj in (
        ("billing:invoice_detail", invoice),
        ("shipping:deliverynote_detail", note),
        ("purchasing:purchaseorder_detail", order),
    ):
        assert len(_copy_queries(client, reverse(name, args=[obj.pk]))) == 1, name
    # 小窓には置かない
    for name, obj in (
        ("billing:invoice_modal", invoice),
        ("purchasing:purchaseorder_modal", order),
    ):
        assert _copy_queries(client, reverse(name, args=[obj.pk]), **HTMX) == [], name
