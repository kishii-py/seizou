"""出荷の詳細の「請求書の外で受け取った入金」の枠と取消 (決め C107-3c。shipping_tags.shipment_outside_payments)。

記録した入金を出荷の詳細から読み、押し間違えたときに入金を取り消して、出荷を請求の候補に戻せる。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

NOV_05 = date(2026, 11, 5)
SECTION = "請求書の外で受け取った入金</h2>"
#: 金額の閲覧だけを効かせる (tests/test_core_line_totals.py と同じ)
NO_MONEY = {"department": "off", "write": "off", "money": "on"}


@pytest.fixture
def shipment(user):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("275")}],
        user,
        order_date=date(2026, 11, 1),
    )
    return shipped(user, customer, order.lines.get(), "5", NOV_05)


@pytest.mark.django_db
def test_detail_lists_outside_payment_with_cancel_link(client, user, shipment):
    payment = billing_services.record_outside_payment(shipment, NOV_05, Decimal("1375"), user)
    client.force_login(user)

    body = client.get(reverse("shipping:shipment_detail", args=[shipment.pk])).content.decode()

    assert SECTION in body
    assert "2026-11-05" in body and "現金" in body and "¥1,375" in body
    cancel_url = reverse("billing:payment_cancel", args=[payment.pk])
    assert f'data-modal-form="{cancel_url}"' in body


@pytest.mark.django_db
def test_cancelled_payment_shows_mark_without_link(client, user, shipment):
    payment = billing_services.record_outside_payment(shipment, NOV_05, Decimal("1375"), user)
    billing_services.cancel_payment(payment, user)
    client.force_login(user)

    body = client.get(reverse("shipping:shipment_detail", args=[shipment.pk])).content.decode()

    assert SECTION in body
    assert '<span class="badge badge-soft badge-ghost">取消</span>' in body
    assert reverse("billing:payment_cancel", args=[payment.pk]) not in body


@pytest.mark.django_db
def test_amount_is_hidden_without_money(settings, client, user, shipment):
    billing_services.record_outside_payment(shipment, NOV_05, Decimal("1375"), user)
    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    client.force_login(user)

    body = client.get(reverse("shipping:shipment_detail", args=[shipment.pk])).content.decode()

    assert SECTION in body
    assert "¥1,375" not in body


@pytest.mark.django_db
def test_no_section_without_payments(client, user, shipment):
    client.force_login(user)

    body = client.get(reverse("shipping:shipment_detail", args=[shipment.pk])).content.decode()

    assert SECTION not in body
