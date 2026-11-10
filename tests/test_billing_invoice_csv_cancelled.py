"""請求書の一覧の CSV の「残」の欄 (開発の記録 docs/nights/20261111/N377.md の (2))。

取消の請求書の残は空の欄にし (Invoice.get_balance_display が None を返し、core の display_value が先に読む)、
発行の請求書の残は balance() の字のまま。画面の一覧の取消の行は今までどおり「—」(billing/_row.html)。
CSV の残の列を足しても、取り消した請求書の分だけ売掛が多く出ないようにするため。作り物のデータだけを使う。
"""

import csv
import io
import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services
from seizou.billing.models import Invoice
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

ISSUED_ON = date(2026, 11, 30)
#: 画面の一覧の取消の行の「残」の欄 (billing/_row.html の 11 行。右寄せの「—」)
CANCELLED_BALANCE_CELL = '<td class="whitespace-nowrap text-right"><span class="text-base-content/60">—</span></td>'


@pytest.fixture
def invoices(user):
    """同じ出荷を請求した取消の請求書 1 枚と発行の請求書 1 枚 (発行の方は一部入金で、残と合計が違う)。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=date(2026, 11, 2),
    )
    shipment = shipped(user, customer, order.lines.get(), "4")
    cancelled = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    services.cancel_invoice(cancelled, user)
    issued = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    services.record_payment(issued, ISSUED_ON, Decimal("100"), user)
    cancelled.refresh_from_db()
    issued.refresh_from_db()
    assert cancelled.status == Invoice.STATUS_CANCELLED
    assert issued.status == Invoice.STATUS_ISSUED
    assert issued.balance() != issued.total
    return cancelled, issued


def _csv_rows(client):
    response = client.get(reverse("billing:invoice_list"), {"format": "csv"})
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    header, body = rows[0], rows[1:]
    return header, {row[0]: row for row in body}


@pytest.mark.django_db
def test_csv_balance_is_empty_for_cancelled_invoice(client, user, invoices):
    cancelled, issued = invoices
    client.force_login(user)

    header, rows = _csv_rows(client)

    balance = header.index("残")
    assert cancelled.invoice_no in rows and issued.invoice_no in rows
    assert rows[cancelled.invoice_no][balance] == ""
    assert rows[issued.invoice_no][balance] == str(issued.balance())
    # 取消の請求書の合計の欄は数のまま (残だけを空にする)
    assert rows[cancelled.invoice_no][header.index("合計")] == str(cancelled.total)


@pytest.mark.django_db
def test_balance_method_is_unchanged_for_cancelled_invoice(invoices):
    """balance() は取消の請求書でも合計 − 入金済み (入金の検査・MCP の道具・詳細の画面が読む)。一覧の欄の値だけが None。"""
    cancelled, issued = invoices

    assert cancelled.balance() == cancelled.total
    assert cancelled.get_balance_display() is None
    assert issued.get_balance_display() == issued.balance()


@pytest.mark.django_db
def test_screen_list_still_shows_dash_for_cancelled_balance(client, user, invoices):
    cancelled, issued = invoices
    client.force_login(user)

    body = client.get(reverse("billing:invoice_list")).content.decode()

    cancelled_row = re.search(rf'<tr id="row-{cancelled.pk}".*?</tr>', body, re.S)
    issued_row = re.search(rf'<tr id="row-{issued.pk}".*?</tr>', body, re.S)
    assert cancelled_row is not None and issued_row is not None
    assert CANCELLED_BALANCE_CELL in cancelled_row.group(0)
    assert CANCELLED_BALANCE_CELL not in issued_row.group(0)
