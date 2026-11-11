"""請求書の新規登録の出荷の候補の字と表の「納品書」の列に、取消の納品書の番号を出さない (依頼書 N403b。N403 の記録の迷った事 8)。

取消の納品書を指したままの出荷も請求できる (本人の決め 11-11 10:1x)。候補の字 (billing.forms.ShipmentChoiceField) と表の「納品書」の列は、
発行の納品書に入っている出荷だけ「納品書 DN-…」を出す。取消の納品書を指したままの出荷と、納品書の無い出荷は付けない。
作り物のデータは全部 services で作る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing.forms import InvoiceForm
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)


@pytest.fixture
def shipments(user):
    """同じ得意先の出荷 3 つ: 発行の納品書に入った出荷・取消の納品書を指したままの出荷・納品書の無い出荷。(出荷 3 つ, 発行の納品書, 取消の納品書)。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}], user, order_date=ORDER_DATE
    )
    line = order.lines.get()
    issued_one = shipping_services.ship(line, Decimal("2"), SHIPPED_AT, user)
    cancelled_one = shipping_services.ship(line, Decimal("3"), SHIPPED_AT, user)
    plain = shipping_services.ship(line, Decimal("4"), SHIPPED_AT, user)
    issued = shipping_services.create_delivery_note(customer, [issued_one], user)
    cancelled = shipping_services.create_delivery_note(customer, [cancelled_one], user)
    shipping_services.cancel_delivery_note(cancelled, user)
    for row in (issued_one, cancelled_one, plain):
        row.refresh_from_db()
    return (issued_one, cancelled_one, plain), issued, cancelled


@pytest.mark.django_db
def test_candidate_label_names_issued_note_only(shipments):
    (issued_one, cancelled_one, plain), issued, cancelled = shipments
    field = InvoiceForm().fields["shipments"]

    labels = {shipment.pk: field.label_from_instance(shipment) for shipment in (issued_one, cancelled_one, plain)}

    assert labels[issued_one.pk].endswith(f"・納品書 {issued.note_no}")
    assert "納品書" not in labels[cancelled_one.pk]
    assert cancelled.note_no not in labels[cancelled_one.pk]
    assert "納品書" not in labels[plain.pk]
    assert labels[cancelled_one.pk].startswith(f"出荷 {cancelled_one.shipment_no}・")


@pytest.mark.django_db
def test_create_screen_note_column_shows_issued_note_only(logged_in, shipments):
    (issued_one, cancelled_one, plain), issued, cancelled = shipments

    body = logged_in.get(reverse("billing:invoice_create")).content.decode()

    for shipment in (issued_one, cancelled_one, plain):
        assert shipment.shipment_no in body
    assert issued.note_no in body
    assert cancelled.note_no not in body
