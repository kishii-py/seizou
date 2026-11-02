"""出荷ラベル (印刷用の HTML と PDF)・出荷の詳細のボタン・納品書の QR。

Django の test client で HTML を見る。印 pdf の試験は WeasyPrint と OS の pango が要る。
印刷の見た目 (A4 縦の上半分に収まるか・QR の大きさ) とスマホのカメラで読めるかは朝に目で見る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import reports
from seizou.core.services import company
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.reports import SHIPMENT_LABEL_TEMPLATE, delivery_note_context, shipment_label_context

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
CUSTOMER_ORDER_NO = "CPO-5512"
BANK_ACCOUNT = "作り物の銀行 普通 1234567"
PDF_NOTICE_WORDS = "PDF の準備がありません"
LABEL_URL_NAMES = ("shipping:shipment_label", "shipping:shipment_label_pdf")


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def company_row(db):
    row = company()
    row.name = "作り物の製作所"
    row.address = "作り物の県 作り物の市 1-2-3"
    row.phone = "000-0000-0000"
    row.bank_account = BANK_ACCOUNT
    row.save()
    return row


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def order(user, customer, item):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("50"), "unit_price": Decimal("120"), "unit": "箱"}],
        user,
        order_date=ORDER_DATE,
        customer_order_no=CUSTOMER_ORDER_NO,
    )


@pytest.fixture
def shipment(user, order):
    return services.ship(order.lines.get(), Decimal("37"), SHIPPED_AT, user)


@pytest.fixture
def note(user, customer, shipment):
    return services.create_delivery_note(customer, [shipment], user)


def _absolute(name, pk):
    return "http://testserver" + reverse(name, args=[pk])


@pytest.mark.django_db
def test_label_views_require_login(client, shipment):
    for name in LABEL_URL_NAMES:
        response = client.get(reverse(name, args=[shipment.pk]))
        assert response.status_code == 302, name
        assert response["Location"].startswith(reverse("core:login")), name


@pytest.mark.django_db
def test_label_shows_shipment(logged_in, company_row, shipment, order):
    response = logged_in.get(reverse("shipping:shipment_label", args=[shipment.pk]))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    body = response.content.decode()

    for words in (
        "作り物の得意先",
        "T-100",
        "作り物の軸",
        shipment.shipment_no,
        "2026年11月5日",
        order.order_no,
        CUSTOMER_ORDER_NO,
        "37 箱",
        "作り物の製作所",
        "作り物の県 作り物の市 1-2-3",
    ):
        assert words in body, words
    assert "<svg" in body and 'role="img"' in body
    # QR の下の字 = 出荷の詳細の絶対 URL
    assert _absolute("shipping:shipment_detail", shipment.pk) in body
    # 出荷元の情報は名前・住所・電話だけ (振込先は出さない)
    assert BANK_ACCOUNT not in body
    assert "<form" not in body
    assert PDF_NOTICE_WORDS not in body


@pytest.mark.django_db
def test_shipment_detail_has_label_buttons(logged_in, shipment):
    response = logged_in.get(reverse("shipping:shipment_detail", args=[shipment.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "ラベル" in body and "PDF" in body
    label_url = reverse("shipping:shipment_label", args=[shipment.pk])
    pdf_url = reverse("shipping:shipment_label_pdf", args=[shipment.pk])
    assert f'href="{label_url}" target="_blank"' in body
    assert f'href="{pdf_url}" target="_blank"' in body
    # 土台の詳細の物は残る
    assert shipment.shipment_no in body
    assert "一覧に戻る" in body and "編集" in body
    assert reverse("shipping:shipment_update", args=[shipment.pk]) in body


@pytest.mark.django_db
def test_label_pdf_falls_back_to_html_when_unavailable(monkeypatch, logged_in, shipment):
    def unavailable(template, context):
        raise reports.PdfUnavailable("試験のために PDF を作れない形にした")

    monkeypatch.setattr("seizou.core.reports.render_pdf", unavailable)
    response = logged_in.get(reverse("shipping:shipment_label_pdf", args=[shipment.pk]))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    body = response.content.decode()
    assert PDF_NOTICE_WORDS in body and "印刷用の画面をお使いください" in body
    assert shipment.shipment_no in body


@pytest.mark.pdf
@pytest.mark.django_db
def test_label_pdf_is_returned_when_available(logged_in, shipment):
    response = logged_in.get(reverse("shipping:shipment_label_pdf", args=[shipment.pk]))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert response["Content-Disposition"] == f'inline; filename="{shipment.shipment_no}.pdf"'


@pytest.mark.pdf
@pytest.mark.django_db
def test_label_pdf_is_single_a4_page(company_row, shipment):
    context = {
        **shipment_label_context(shipment),
        "qr_text": _absolute("shipping:shipment_detail", shipment.pk),
    }

    document = reports._render_document(SHIPMENT_LABEL_TEMPLATE, context)

    assert len(document.pages) == 1
    page = document.pages[0]
    # A4 縦 = 210mm × 297mm (WeasyPrint の長さは CSS の px。1mm = 96 / 25.4 px)
    assert round(page.width * 25.4 / 96) == 210
    assert round(page.height * 25.4 / 96) == 297


@pytest.mark.django_db
def test_delivery_note_has_qr(logged_in, note):
    response = logged_in.get(reverse("shipping:deliverynote_print", args=[note.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert body.count("<svg") == 1
    assert body.count('role="img"') == 1
    assert response.context["qr_text"] == _absolute("shipping:deliverynote_detail", note.pk)
    # 得意先に渡す紙なので、QR の中身の字は出さない
    assert response.context["qr_text"] not in body


@pytest.mark.django_db
def test_delivery_note_pdf_fallback_has_qr(monkeypatch, logged_in, note):
    def unavailable(template, context):
        raise reports.PdfUnavailable("試験のために PDF を作れない形にした")

    monkeypatch.setattr("seizou.core.reports.render_pdf", unavailable)
    response = logged_in.get(reverse("shipping:deliverynote_pdf", args=[note.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert PDF_NOTICE_WORDS in body
    assert body.count("<svg") == 1


@pytest.mark.django_db
def test_delivery_note_context_has_no_qr_key(note):
    assert set(delivery_note_context(note)) == {"company", "note", "customer", "lines", "total"}
