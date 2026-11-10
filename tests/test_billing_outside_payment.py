"""請求書の外で受け取った入金 (作者の決め C107-3c。billing.services.record_outside_payment)。

一式でない明細の出荷の代金を請求書を作らずに受け取ったとき、入金の表に出荷を指す入金 (請求書は空) を記録する。
記録した出荷は請求の候補から外れ、今月の入金に入る。出荷の金額は変えない。入金を取り消すと候補に戻る。
作り物のデータだけを使う。
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from seizou.billing import services
from seizou.billing.models import Payment
from seizou.core import dashboard, mcp_tools
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
#: 金額の閲覧だけを効かせる (tests/test_core_line_totals.py と同じ)
NO_MONEY = {"department": "off", "write": "off", "money": "on"}


@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, price_basis="unit", qty="10", price="275"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(price), "price_basis": price_basis}],
        user,
        order_date=date(2026, 10, 1),
    )
    return order.lines.get()


@pytest.fixture
def shipment(user, customer, item, today):
    """部品の単位の明細 (単価 275) を 5 出荷して納品書を発行した出荷 (金額 1,375)。"""
    return shipped(user, customer, _line(user, customer, item), "5", today)


def _months(today):
    monthly = {row["key"]: row["shipment_amount"] for row in dashboard.monthly_trend(today=today)["months"]}
    trend = {row["key"]: row["shipment_amount"] for row in dashboard.shipping_trend(today=today)["months"]}
    return monthly, trend


@pytest.mark.django_db
def test_record_takes_shipment_out_of_billing(user, customer, shipment, today):
    before_paid = dashboard.payments_this_month(today)
    before_months = _months(today)

    payment = services.record_outside_payment(shipment, today, Decimal("1375"), user)

    assert payment.shipment_id == shipment.pk and payment.invoice_id is None
    assert payment.method == Payment.METHOD_CASH
    assert payment.saved_message == f"出荷 {shipment.shipment_no} の ¥1,375 を、請求書の外で受け取った入金として記録しました。"
    assert shipment not in services.billable_shipments()
    assert shipment not in services.unbilled_in_period(customer, today, today)
    note = type(shipment.delivery_note).objects.get(pk=shipment.delivery_note_id)
    assert note.has_unbilled_shipments is False
    assert dashboard.payments_this_month(today) == before_paid + Decimal("1375")
    assert _months(today) == before_months


@pytest.mark.django_db
def test_cancel_returns_shipment_to_billing(user, shipment, today):
    before_paid = dashboard.payments_this_month(today)
    payment = services.record_outside_payment(shipment, today, Decimal("1375"), user)

    services.cancel_payment(payment, user)

    assert shipment in services.billable_shipments()
    assert dashboard.payments_this_month(today) == before_paid


@pytest.mark.django_db
def test_payment_points_to_invoice_or_shipment(user, customer, shipment, today):
    invoice = services.create_invoice(customer, [shipment], user, issued_on=today)
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(invoice=invoice, shipment=shipment, received_on=today, amount=Decimal("1"))
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(received_on=today, amount=Decimal("1"))


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("amount", "method", "note", "text"),
    [
        ("0", "cash", "", "入金の金額が 0 以下です。"),
        ("abc", "cash", "", "入金の金額を数として読めません"),
        ("100", "coin", "", "知らない入金の方法です。"),
        ("100", "cash", "あ" * 501, "備考が 500 字を超えています。"),
    ],
)
def test_values_are_checked_like_record_payment(user, shipment, today, amount, method, note, text):
    with pytest.raises(ValueError) as caught:
        services.record_outside_payment(shipment, today, amount, user, method=method, note=note)

    assert str(caught.value).startswith("記録できません。")
    assert text in str(caught.value)
    assert Payment.objects.count() == 0


@pytest.mark.django_db
def test_create_screen_has_outside_link_on_unit_rows_only(settings, client, user, customer, item, shipment, today):
    lump_line = _line(user, customer, item, price_basis="lump", qty="300", price="50000")
    lump_shipment = shipped(user, customer, lump_line, "100", today)
    client.force_login(user)
    url = reverse("billing:shipment_payment_create", args=[shipment.pk])

    body = client.get(reverse("billing:invoice_create")).content.decode()
    assert f'href="{url}?next=' in body
    assert f'data-modal-form="{url}" data-row-drop' in body
    assert reverse("billing:shipment_payment_create", args=[lump_shipment.pk]) not in body
    assert "請求しない (別に受け取った)" in body

    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    body = client.get(reverse("billing:invoice_create")).content.decode()
    assert url not in body
    assert "<th>操作</th>" not in body


@pytest.mark.django_db
def test_record_screen_initials_and_posts(client, user, shipment, today):
    client.force_login(user)
    url = reverse("billing:shipment_payment_create", args=[shipment.pk])

    page = client.get(url)
    form = page.context["form"]
    assert page.status_code == 200
    assert form["amount"].value() == Decimal("1512")
    assert form["method"].value() == Payment.METHOD_CASH
    assert f"出荷 {shipment.shipment_no} の入金として記録する" in page.content.decode()

    data = {"received_on": today.isoformat(), "amount": "1375", "method": "cash", "note": ""}
    modal = client.post(url, data, **MODAL)
    assert modal.status_code == 204
    assert "seizou:saved" in json.loads(modal["HX-Trigger"])
    payment = Payment.objects.get()
    services.cancel_payment(payment, user)

    full = client.post(url, data)
    created = Payment.objects.order_by("-pk").first()
    assert full.status_code == 302
    assert full["Location"] == reverse("billing:payment_detail", args=[created.pk])
    moved = client.get(full["Location"])
    assert moved["Location"] == reverse("shipping:shipment_detail", args=[shipment.pk])


@pytest.mark.django_db
def test_cancel_screen_names_shipment(client, user, customer, shipment, item, today):
    payment = services.record_outside_payment(shipment, today, Decimal("1375"), user)
    client.force_login(user)
    url = reverse("billing:payment_cancel", args=[payment.pk])

    for headers in ({}, MODAL):
        body = client.get(url, **headers).content.decode()
        assert f'出荷 <span class="font-mono">{shipment.shipment_no}</span> の請求書の外で受け取った入金' in body

    # 請求書の入金の取消の字は今のまま
    other = shipped(user, customer, _line(user, customer, item), "1", today)
    invoice = services.create_invoice(customer, [other], user, issued_on=today)
    invoice_payment = services.record_payment(invoice, today, Decimal("275"), user)
    body = client.get(reverse("billing:payment_cancel", args=[invoice_payment.pk])).content.decode()
    assert f'請求書 <span class="font-mono">{invoice.invoice_no}</span> の入金' in body


@pytest.mark.django_db
def test_mcp_lists_outside_payment(user, customer, shipment, today):
    services.record_outside_payment(shipment, today, Decimal("1375"), user)
    other = shipped(user, customer, _line(user, customer, shipment.order_line.item), "1", today)
    invoice = services.create_invoice(customer, [other], user, issued_on=today)
    services.record_payment(invoice, today, Decimal("275"), user)

    result = mcp_tools._list_payments(user, None, 30, 50)

    assert result["ok"] is True
    rows = {row["shipment_no"] or row["invoice_no"]: row for row in result["items"]}
    outside = rows[shipment.shipment_no]
    assert outside["invoice_no"] is None and outside["customer_code"] == customer.code
    assert outside["url"] == reverse("shipping:shipment_detail", args=[shipment.pk])
    assert rows[invoice.invoice_no]["shipment_no"] is None


@pytest.mark.django_db
def test_str_of_outside_payment(user, shipment, today):
    payment = services.record_outside_payment(shipment, today, Decimal("1375"), user)

    assert str(payment) == f"出荷 {shipment.shipment_no} {today:%Y-%m-%d}"
