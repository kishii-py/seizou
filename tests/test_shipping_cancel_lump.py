"""一式の行で請求した明細の出荷の納品書の取消 (作者の決め C107-6 を、本人の決め 11-11 13:0x で「止めずに知らせる」に変えた)。

発行の請求書に一式の行 (出荷に結び付かない請求書の明細) がある受注の明細の出荷も「請求書に入った出荷」と数える。
前はその出荷の納品書を取り消せなかったが、納品書は出荷の記録を刷った物で、取り消しても請求書の一式の行 (写し) は変わらないので、
取り消せる形にし、検査の層の R27 が知らせる (本人の決め 11-11 10:1x・13:0x「それでOK」)。
知らせの文は shipping.services.billed_notice の 1 か所。形の手本 = tests/test_shipping_cancel_note.py。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.models import DeliveryNote

OCT_10 = date(2026, 10, 10)
NOV_30 = date(2026, 11, 30)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
#: 入力のカード (core/_form_card.html) の form の頭
FORM_CARD = '<form method="post" class="rounded-xl'


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, price_basis="lump", qty="300", price="50000"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(price), "price_basis": price_basis}],
        user,
        order_date=date(2026, 10, 1),
    )
    return order.lines.get()


def _lump_billed(user, customer, item):
    """一式 (300・50000) を 100 出荷・納品書を発行・受注を取消 → 一式の行だけの請求書。(納品書, 出荷, 請求書)。"""
    line = _line(user, customer, item)
    shipment = services.ship(line, Decimal("100"), OCT_10, user)
    note = services.create_delivery_note(customer, [shipment], user)
    orders_services.cancel_order(line.order, user)
    line.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [], user, issued_on=NOV_30, lumps=[(line, Decimal("16667"))])
    return note, shipment, invoice


@pytest.mark.django_db
def test_lump_billed_note_can_be_cancelled(user, customer, item):
    """一式の行で請求した明細の出荷の納品書も取り消せ、知らせの文は請求書の番号と「請求書は変わりません」を書く。一式の行は変わらない。"""
    note, shipment, invoice = _lump_billed(user, customer, item)
    expected = (
        f"納品書 {note.note_no} の出荷 {shipment.shipment_no} の受注の明細は、請求書 {invoice.invoice_no} の一式の行で請求済みです。"
        "納品書を取り消しても、請求書は変わりません。"
    )
    lump_row = invoice.lines.get()

    assert services.lump_billed_shipments(note) == [(shipment, invoice)]
    assert services.billed_notice(note) == expected
    assert services.cancel_delivery_note(note, user).status == DeliveryNote.STATUS_CANCELLED
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_CANCELLED
    invoice.refresh_from_db()
    assert invoice.status == invoice.STATUS_ISSUED
    assert (invoice.lines.get().qty, invoice.lines.get().amount) == (lump_row.qty, lump_row.amount)


@pytest.mark.django_db
def test_cancelling_lump_invoice_clears_notice(user, customer, item):
    note, _shipment, invoice = _lump_billed(user, customer, item)

    billing_services.cancel_invoice(invoice, user)

    assert services.billed_notice(note) == ""
    assert services.cancel_delivery_note(note, user).status == DeliveryNote.STATUS_CANCELLED


@pytest.mark.django_db
def test_shipment_line_invoice_comes_first(user, customer, item):
    lump = _line(user, customer, item)
    unit = _line(user, customer, item, price_basis="unit", qty="10", price="100")
    lump_shipment = services.ship(lump, Decimal("100"), OCT_10, user)
    unit_shipment = services.ship(unit, Decimal("4"), OCT_10, user)
    note = services.create_delivery_note(customer, [lump_shipment, unit_shipment], user)
    orders_services.cancel_order(lump.order, user)
    lump.refresh_from_db()
    unit_shipment.refresh_from_db()
    invoice = billing_services.create_invoice(
        customer, [unit_shipment], user, issued_on=NOV_30, lumps=[(lump, Decimal("16667"))]
    )

    notice = services.billed_notice(note)

    assert f"出荷 {unit_shipment.shipment_no} は請求書 {invoice.invoice_no} に入っています。" in notice
    assert "一式の行" not in notice


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, MODAL])
def test_cancel_screen_shows_lump_invoice_link(client, user, customer, item, headers):
    note, shipment, invoice = _lump_billed(user, customer, item)
    client.force_login(user)

    body = client.get(reverse("shipping:deliverynote_cancel", args=[note.pk]), **headers).content.decode()

    # 押す前に知らせの黄の 1 行と、請求済みの出荷と請求書の link の行が出て、取り消すボタンも出る
    assert services.billed_notice(note) in body
    assert "取り消せません。" not in body
    assert f'href="{reverse("billing:invoice_detail", args=[invoice.pk])}' in body
    assert f"{invoice.invoice_no}</a>の一式の行 で請求済み" in body
    assert f'href="{reverse("shipping:shipment_detail", args=[shipment.pk])}"' in body
    assert FORM_CARD in body
    assert f"{note.note_no} を取り消す</button>" in body

    detail = client.get(reverse("shipping:deliverynote_detail", args=[note.pk])).content.decode()
    assert "で請求済みです (出荷 1 件)。" in detail
    assert invoice.invoice_no in detail


@pytest.mark.django_db
def test_unit_only_note_has_no_lump_rows(user, customer, item):
    unit = _line(user, customer, item, price_basis="unit", qty="10", price="100")
    shipment = services.ship(unit, Decimal("4"), OCT_10, user)
    note = services.create_delivery_note(customer, [shipment], user)

    assert services.lump_billed_shipments(note) == []
    assert services.billed_notice(note) == ""
