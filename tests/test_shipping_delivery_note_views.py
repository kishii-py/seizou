"""納品書の画面と帳票 (02 の出荷の納品書、05 の 6 節)。Django の test client で HTML と表の状態を見る。

印 pdf の試験は WeasyPrint と OS の pango が要る。ほかは WeasyPrint が無くても通る。
印刷の見た目 (A4 1 枚に収まるか) は朝にブラウザで見る。
"""

import html
import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.template.defaultfilters import floatformat
from django.urls import reverse

from seizou.core import reports
from seizou.core.services import company
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.forms import DeliveryNoteForm
from seizou.shipping.models import DeliveryNote
from seizou.shipping.reports import delivery_note_context

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
PDF_NOTICE_WORDS = "PDF の準備がありません"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _customer(user, code, name):
    return masters_services.create_partner({"code": code, "name": name, "role": "customer"}, user)


def _order_line(user, customer, item, qty="50", unit_price="120"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price)}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def first_customer(user):
    return _customer(user, "C001", "作り物の得意先")


@pytest.fixture
def two_shipments(user, first_customer, item):
    """同じ得意先の出荷 2 つ (同じ明細の 37 と 5)。"""
    line = _order_line(user, first_customer, item)
    return [
        services.ship(line, Decimal("37"), SHIPPED_AT, user),
        services.ship(line, Decimal("5"), SHIPPED_AT, user),
    ]


@pytest.fixture
def note(user, first_customer, two_shipments):
    return services.create_delivery_note(first_customer, two_shipments, user)


@pytest.mark.django_db
def test_create_delivery_note_from_two_shipments(logged_in, two_shipments):
    form_page = logged_in.get(reverse("shipping:deliverynote_create"))
    body = form_page.content.decode()
    assert form_page.status_code == 200
    assert 'type="checkbox"' in body
    for shipment in two_shipments:
        assert shipment.shipment_no in body

    response = logged_in.post(
        reverse("shipping:deliverynote_create"), {"shipments": [s.pk for s in two_shipments]}
    )
    note = DeliveryNote.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("shipping:deliverynote_detail", args=[note.pk])
    assert set(note.shipments.all()) == set(two_shipments)

    detail = logged_in.get(response["Location"]).content.decode()
    assert note.note_no in detail
    for shipment in two_shipments:
        assert shipment.shipment_no in detail
    print_url = reverse("shipping:deliverynote_print", args=[note.pk])
    pdf_url = reverse("shipping:deliverynote_pdf", args=[note.pk])
    assert f'href="{print_url}" target="_blank"' in detail
    assert f'href="{pdf_url}" target="_blank"' in detail

    # 納品書に入った出荷は、次の納品書の候補に出ない。一覧に納品書が出る。
    again = logged_in.get(reverse("shipping:deliverynote_create")).content.decode()
    for shipment in two_shipments:
        assert shipment.shipment_no not in again
    listing = logged_in.get(reverse("shipping:deliverynote_list")).content.decode()
    assert note.note_no in listing and "作り物の得意先" in listing


@pytest.mark.django_db
def test_mixed_customers_is_r08_red(logged_in, user, item, two_shipments):
    other = _customer(user, "C002", "作り物のほかの得意先")
    other_shipment = services.ship(_order_line(user, other, item), Decimal("3"), SHIPPED_AT, user)

    response = logged_in.post(
        reverse("shipping:deliverynote_create"), {"shipments": [two_shipments[0].pk, other_shipment.pk]}
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body and "R08" in body
    assert DeliveryNote.objects.count() == 0
    other_shipment.refresh_from_db()
    assert other_shipment.delivery_note is None


@pytest.mark.django_db
def test_print_html_has_note_no_and_quantities(client, user, note, two_shipments):
    row = company()
    row.name = "作り物の製作所"
    row.save()
    print_url = reverse("shipping:deliverynote_print", args=[note.pk])

    anonymous = client.get(print_url)
    assert anonymous.status_code == 302
    assert reverse("core:login") in anonymous["Location"]

    client.force_login(user)
    response = client.get(print_url)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    body = response.content.decode()
    assert note.note_no in body
    assert "作り物の得意先" in body and "作り物の製作所" in body
    assert "T-100" in body and "作り物の軸" in body
    assert 'href="/static/css/report.css"' in body
    assert PDF_NOTICE_WORDS not in body

    # 数量は出荷と同じ (R08)。金額は数量 × 受注の単価を丸めた値で、合計は金額の和。
    context = delivery_note_context(note)
    assert [line["quantity"] for line in context["lines"]] == [s.qty for s in two_shipments]
    assert [line["amount"] for line in context["lines"]] == [Decimal("4440"), Decimal("600")]
    assert context["total"] == Decimal("5040")
    for shipment in two_shipments:
        assert f'<td class="num">{floatformat(shipment.qty, "g")}</td>' in body
    assert f'<td class="num">¥{floatformat(Decimal("5040"), "g")}</td>' in body


@pytest.mark.django_db
def test_pdf_falls_back_to_html_when_unavailable(monkeypatch, logged_in, note):
    def unavailable(template, context):
        raise reports.PdfUnavailable("試験のために PDF を作れない形にした")

    monkeypatch.setattr("seizou.core.reports.render_pdf", unavailable)
    response = logged_in.get(reverse("shipping:deliverynote_pdf", args=[note.pk]))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    body = response.content.decode()
    assert PDF_NOTICE_WORDS in body and "印刷用の画面をお使いください" in body
    assert note.note_no in body and "T-100" in body


@pytest.mark.pdf
@pytest.mark.django_db
def test_pdf_is_returned_when_available(logged_in, note):
    response = logged_in.get(reverse("shipping:deliverynote_pdf", args=[note.pk]))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == f'inline; filename="{note.note_no}.pdf"'
    assert response.content.startswith(b"%PDF")


@pytest.mark.django_db
def test_menu_has_delivery_note_entry(logged_in):
    response = logged_in.get(reverse("core:home"))
    body = response.content.decode()
    assert reverse("shipping:deliverynote_list") in body
    assert "納品書" in body


# --- 出荷の表と 0 件の 1 行 (初見テスト R5・候補 C74 の 2 晩目。ボタン・合計・金額は足さない) ---

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
NOTE_HEADERS = ("選ぶ", "出荷番号", "出荷日", "得意先", "受注番号", "部品", "数量")
NO_SHIPMENTS = "納品書に入っていない出荷はありません。"


def _primary_button(body):
    """主の保存のボタンの字。"""
    match = re.search(r'<button class="btn btn-primary" type="submit"[^>]*>(.*?)</button>', body, re.S)
    assert match, "保存のボタンが無い"
    return html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()


def _checked_values(body):
    """印の付いた出荷の checkbox の value の並び。"""
    tags = re.findall(r'<input type="checkbox" name="shipments" value="(\d+)"([^>]*)>', body)
    return [int(value) for value, attrs in tags if re.search(r"\schecked(?=\s|$)", attrs)]


@pytest.mark.django_db
def test_create_form_without_shipments_says_none(logged_in):
    response = logged_in.get(reverse("shipping:deliverynote_create"))

    body = response.content.decode()
    assert response.status_code == 200
    assert NO_SHIPMENTS in body
    assert 'id="id_shipments"' not in body
    # 止める処理は足さない (0 件でもボタンは出る)。
    assert _primary_button(body) == "納品書を作る"


@pytest.mark.django_db
def test_create_form_shows_shipments_as_table(logged_in, two_shipments):
    response = logged_in.get(reverse("shipping:deliverynote_create"))

    body = response.content.decode()
    assert response.status_code == 200
    assert '<table id="id_shipments"' in body
    for header in NOTE_HEADERS:
        assert f">{header}</th>" in body, header
    assert NO_SHIPMENTS not in body
    labels = re.findall(r'<span class="sr-only">([^<]*)</span>', body)
    field = DeliveryNoteForm().fields["shipments"]
    for shipment in two_shipments:
        assert shipment.shipment_no in body
        # checkbox の見えない名前は今の行の字 (受注番号を含む)。
        assert field.label_from_instance(shipment) in labels
        assert shipment.order_line.order.order_no in field.label_from_instance(shipment)


@pytest.mark.django_db
def test_create_modal_shows_table(logged_in, two_shipments):
    response = logged_in.get(reverse("shipping:deliverynote_create"), **MODAL)

    body = response.content.decode()
    assert response.status_code == 200
    assert "<html" not in body
    assert "hx-post=" in body
    assert 'id="id_shipments"' in body


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, MODAL], ids=["page", "modal"])
def test_create_form_checks_given_shipment(logged_in, two_shipments, headers):
    first, second = two_shipments

    response = logged_in.get(f"{reverse('shipping:deliverynote_create')}?shipments={second.pk}", **headers)

    body = response.content.decode()
    assert response.status_code == 200
    assert _checked_values(body) == [second.pk]
    assert f'value="{first.pk}"' in body
