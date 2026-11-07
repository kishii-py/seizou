"""請求書の外で受け取った入金を、請求の候補の出荷にだけ記録させる (作者の決め C107-10。billing.services.record_outside_payment の断り)。

断る出荷 = 発行の請求書に入った出荷・取消でない外の入金がもうある出荷・発行の納品書に入っていない出荷 (この順)。
作り直し (reissue_invoice) は、取り消す請求書に入っていた出荷のうち外の入金のある物を写さず、保存の後の知らせに並べる。
作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services
from seizou.billing.models import Invoice, Payment
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from tests.billing_helpers import shipped

NOV_05 = date(2026, 11, 5)
NOV_10 = date(2026, 11, 10)
NOV_30 = date(2026, 11, 30)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def line(user, customer):
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("100"), "unit_price": Decimal("275")}],
        user,
        order_date=date(2026, 11, 1),
    )
    return order.lines.get()


@pytest.mark.django_db
def test_invoiced_shipment_is_refused_until_invoice_cancelled(user, customer, line):
    shipment = shipped(user, customer, line, "5", NOV_05)
    invoice = services.create_invoice(customer, [shipment], user, issued_on=NOV_30)

    with pytest.raises(ValueError) as caught:
        services.record_outside_payment(shipment, NOV_10, Decimal("1375"), user)

    assert str(caught.value) == (
        f"記録できません。出荷 {shipment.shipment_no} は請求書 {invoice.invoice_no} に入っています。"
        "代金は請求書の入金として記録してください。"
    )
    assert Payment.objects.count() == 0
    services.cancel_invoice(invoice, user)
    assert services.record_outside_payment(shipment, NOV_10, Decimal("1375"), user).shipment_id == shipment.pk


@pytest.mark.django_db
def test_second_outside_payment_is_refused_until_first_cancelled(user, customer, line):
    shipment = shipped(user, customer, line, "5", NOV_05)
    first = services.record_outside_payment(shipment, NOV_10, Decimal("1375"), user)

    with pytest.raises(ValueError) as caught:
        services.record_outside_payment(shipment, NOV_10, Decimal("1375"), user)

    assert str(caught.value) == (
        f"記録できません。出荷 {shipment.shipment_no} には、請求書の外で受け取った入金 (2026-11-10) がもう記録されています。"
        "直すときは、出荷の詳細でその入金を取り消してから記録し直してください。"
    )
    assert Payment.objects.count() == 1
    services.cancel_payment(first, user)
    services.record_outside_payment(shipment, NOV_10, Decimal("1375"), user)
    assert Payment.objects.filter(status=Payment.STATUS_RECORDED).count() == 1


@pytest.mark.django_db
def test_shipment_without_issued_note_is_refused(user, customer, line):
    no_note = shipping_services.ship(line, Decimal("3"), NOV_05, user)
    cancelled = shipped(user, customer, line, "4", NOV_05)
    shipping_services.cancel_delivery_note(cancelled.delivery_note, user)
    cancelled.refresh_from_db()

    for shipment in (no_note, cancelled):
        with pytest.raises(ValueError) as caught:
            services.record_outside_payment(shipment, NOV_10, Decimal("100"), user)
        assert str(caught.value) == (
            f"記録できません。出荷 {shipment.shipment_no} は発行の納品書に入っていません。納品書を発行してから記録してください。"
        )
    assert Payment.objects.count() == 0


@pytest.mark.django_db
def test_screen_shows_stop_notice_in_modal_and_toast(client, user, customer, line):
    shipment = shipped(user, customer, line, "5", NOV_05)
    services.create_invoice(customer, [shipment], user, issued_on=NOV_30)
    client.force_login(user)
    url = reverse("billing:shipment_payment_create", args=[shipment.pk])
    data = {"received_on": NOV_10.isoformat(), "amount": "1375", "method": "cash", "note": ""}

    modal = client.post(url, data, **MODAL)
    body = modal.content.decode()
    assert modal.status_code == 200
    assert 'data-notice="stop"' in body
    assert "記録できません。" in body

    full = client.post(url, data)
    body = full.content.decode()
    assert full.status_code == 200
    toasts = body[body.index('id="seizou-toasts"') :]
    assert 'data-notice="stop"' in toasts
    assert "記録できません。" in toasts
    assert Payment.objects.count() == 0


@pytest.mark.django_db
def test_reissue_does_not_copy_outside_paid_shipment(client, user, customer, line):
    paid = shipped(user, customer, line, "5", NOV_05)
    other = shipped(user, customer, line, "6", NOV_10)
    invoice = services.create_invoice(customer, [paid, other], user, issued_on=NOV_30)
    # その 15 より前に記録した入金の形 (services では、請求書に入った出荷に外の入金を記録できない)。
    Payment.objects.create(shipment=paid, received_on=NOV_10, amount=Decimal("1375"))

    plan = services.reissue_plan(invoice)
    assert plan["shipments"] == [other]
    assert [shipment for shipment, _payment in plan["outside_paid"]] == [paid]

    created = services.reissue_invoice(invoice, user, issued_on=NOV_30)
    assert [row.shipment_id for row in created.lines.all()] == [other.pk]
    assert f"別に受け取った入金があるので入れない出荷: {paid.shipment_no}。" in created.saved_message
    assert created.saved_level == "warning"
    assert Invoice.objects.get(pk=invoice.pk).status == Invoice.STATUS_CANCELLED
