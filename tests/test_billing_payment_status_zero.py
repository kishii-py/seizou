"""合計が 0 円以下の請求書の入金の状態 (作者の決め C107-4: 入金を待つ物ではないので「入金済み」と同じ扱い)。

一式の途中の出荷だけを入れた請求書は合計 0 円になる (一式の明細は全部を出し終えた出荷で全額、ほかの出荷は 0 = 決め PRICE-1c)。
作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services
from seizou.billing.models import Invoice
from seizou.billing.templatetags.billing_tags import payment_badge
from seizou.core.status_colors import PAYMENT_TONES
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

OCT_10 = date(2026, 10, 10)
OCT_31 = date(2026, 10, 31)


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先 C001", "role": "customer"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, price_basis):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("300"), "unit_price": Decimal("50000") if price_basis == "lump" else Decimal("100"), "price_basis": price_basis}],
        user,
        order_date=date(2026, 10, 1),
    )
    return order.lines.get()


def _zero_invoice(user, customer, item):
    """一式 (300・50000) を 100 だけ出荷して納品書を発行し、その出荷だけで作った合計 0 の請求書。"""
    line = _line(user, customer, item, "lump")
    shipment = shipped(user, customer, line, "100", OCT_10)
    invoice = services.create_invoice(customer, [shipment], user, issued_on=OCT_31)
    assert invoice.total == Decimal("0")
    return invoice


@pytest.mark.django_db
def test_zero_total_invoice_is_paid(user, customer, item):
    invoice = _zero_invoice(user, customer, item)

    assert invoice.payment_status() == Invoice.PAYMENT_PAID
    assert invoice.payment_status_label() == "入金済み"
    assert payment_badge(invoice)["tone"] == PAYMENT_TONES[Invoice.PAYMENT_PAID]
    assert payment_badge(invoice)["label"] == "入金済み"


@pytest.mark.django_db
def test_positive_total_keeps_unpaid_and_partial(user, customer, item):
    line = _line(user, customer, item, "unit")
    shipment = shipped(user, customer, line, "10", OCT_10)
    invoice = services.create_invoice(customer, [shipment], user, issued_on=OCT_31)
    assert invoice.total > 0

    assert invoice.payment_status() == Invoice.PAYMENT_UNPAID
    services.record_payment(invoice, OCT_31, Decimal("1"), user)
    invoice.refresh_from_db()
    assert invoice.payment_status() == Invoice.PAYMENT_PARTIAL


@pytest.mark.django_db
def test_cancelled_zero_invoice_label_is_dash(user, customer, item):
    invoice = _zero_invoice(user, customer, item)

    cancelled = services.cancel_invoice(invoice, user)

    assert cancelled.payment_status_label() == "—"


@pytest.mark.django_db
def test_unpaid_filter_does_not_list_zero_invoice(client, user, customer, item):
    invoice = _zero_invoice(user, customer, item)
    client.force_login(user)

    body = client.get(reverse("billing:invoice_list") + "?f_unpaid=1").content.decode()

    assert invoice.invoice_no not in body
