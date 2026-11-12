"""請求書の小窓の明細の表が、出荷を消した出荷の行にも出荷番号の写しを出す (504 回目の本体の直し。依頼書 N403c の要望 1 と 2)。

訳: 小窓の表は、明細の出荷の外部キーが空なら「一式」と出していた。請求書に入った出荷を消せるようにした後 (N403c。
本人の決め 11-11 12:5x〜13:0x) は、出荷を消した出荷の行も外部キーが空になるので、一式でない行に「一式」と出た。
一式の行かどうかは InvoiceLine.is_lump (最後の出荷日があるか) で見て、ほかの行は出荷番号の写しを出す。
出荷の削除の画面の説明の字も、消せる物と消せない物を今の動きに合わせる (説明書の断片と同じ字)。
作り物の得意先・部品・受注だけを使い、全部 services で作る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from seizou.shipping.views import ShipmentDeleteView

HTMX = {"HTTP_HX_REQUEST": "true"}
ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "MD-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, qty="10", price_basis="unit", unit_price="100"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price), "price_basis": price_basis}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _first_cells(body: str) -> list[str]:
    """小窓の明細の表の、行ごとの 1 つ目の枡 (出荷番号の列) の字。"""
    table = body.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    cells = []
    for row in table.split("<tr>")[1:]:
        cell = row.split("<td", 1)[1].split(">", 1)[1].split("</td>", 1)[0]
        cells.append(cell.strip())
    return cells


@pytest.mark.django_db
def test_modal_shows_the_copied_number_for_a_deleted_shipment(user, logged_in, customer, item):
    line = _line(user, customer, item)
    shipment = shipping_services.ship(line, Decimal("4"), SHIPPED_AT, user)
    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    number = shipment.shipment_no
    url = reverse("billing:invoice_modal", args=[invoice.pk])

    before = logged_in.get(url, **HTMX)
    assert before.status_code == 200
    assert _first_cells(before.content.decode()) == [number]

    shipping_services.delete_shipment(shipment, user)
    row = invoice.lines.get()
    assert row.is_shipment_deleted and row.shipment_id is None

    after = logged_in.get(url, **HTMX)
    assert after.status_code == 200
    assert _first_cells(after.content.decode()) == [number]  # 出荷を消した後も、出荷番号の写しが出る (「一式」と出ない)


@pytest.mark.django_db
def test_modal_says_lump_only_for_a_lump_line(user, logged_in, customer, item):
    line = _line(user, customer, item)
    shipment = shipping_services.ship(line, Decimal("4"), SHIPPED_AT, user)
    lump = _line(user, customer, item, qty="300", price_basis="lump")
    shipping_services.ship(lump, Decimal("60"), SHIPPED_AT, user)
    orders_services.mark_completed(lump, user)
    lump.refresh_from_db()
    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON, lumps=[(lump, "1000")])

    response = logged_in.get(reverse("billing:invoice_modal", args=[invoice.pk]), **HTMX)

    assert response.status_code == 200
    assert sorted(_first_cells(response.content.decode())) == sorted([shipment.shipment_no, "一式"])


def test_shipment_delete_help_matches_what_can_be_deleted():
    """出荷の削除の画面の説明は、請求書に入った出荷を消せる事と、消せない物を今の動きのとおりに書く (古い字「納品書・請求書・入金が指す出荷」を残さない)。"""
    text = ShipmentDeleteView.screen_help
    assert "請求書に入った出荷は消せて、請求書の明細に出荷番号と出荷日が残ります。" in text
    assert "発行の納品書に入った出荷・請求書の外の入金が指す出荷と、取引先につながる出荷は消せません。" in text
    assert "納品書・請求書・入金が指す出荷" not in text
