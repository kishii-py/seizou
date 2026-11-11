"""作り直しで写す一式の行の出荷が全部消えた時の R09 の文 (billing.services._refuse_unbillable_lump の copying。依頼書 N403b)。

一式の行で請求した明細の出荷は、R27 の知らせの後に消せる (N403)。全部消してからその請求書を作り直すと、写す一式の行は R09 で止まり、
作り直しの画面ではその行を外せない。止まった時の文で、作り直す前の請求書を取り消してから請求書の新規登録で作り直す道を書く
(本体の決め 11-11 15:0x。N403 の記録の迷った事 9 の (1))。請求書の新規登録で出荷の無い一式の明細を載せる時の文は今のまま。
作り物のデータは全部 services で作る。
"""

from datetime import date
from decimal import Decimal

import pytest

from seizou.billing import services as billing_services
from seizou.billing.models import Invoice
from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
R09_HEAD = "発行できません。請求書の明細は、出荷済みの物だけ。"


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, qty, price_basis):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100"), "price_basis": price_basis}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def lump_gone(user, customer, item):
    """一式 300 を 60 と 40 で出荷して完了の印で終え、単価の明細の出荷 4 と一緒に一式の行 (金額 1000) で請求した後に、
    一式の明細の出荷を 2 つとも消した物 (納品書は作らない)。(一式の明細, 単価の出荷, 請求書) を返す。"""
    lump = _line(user, customer, item, "300", "lump")
    first = shipping_services.ship(lump, Decimal("60"), SHIPPED_AT, user)
    second = shipping_services.ship(lump, Decimal("40"), SHIPPED_AT, user)
    orders_services.mark_completed(lump, user)
    lump.refresh_from_db()
    unit = shipping_services.ship(_line(user, customer, item, "10", "unit"), Decimal("4"), SHIPPED_AT, user)
    invoice = billing_services.create_invoice(customer, [unit], user, issued_on=ISSUED_ON, lumps=[(lump, "1000")])
    shipping_services.delete_shipment(first, user)
    shipping_services.delete_shipment(second, user)
    return lump, unit, invoice


@pytest.mark.django_db
def test_reissue_with_lump_without_shipments_points_to_new_invoice(user, lump_gone):
    """作り直すと R09 で止まり、文に「作り直しで写せません」と「請求書の新規登録で作り直してください」がある。何も変わらない。"""
    lump, _unit, invoice = lump_gone

    with pytest.raises(RuleViolation) as caught:
        billing_services.reissue_invoice(invoice, user, issued_on=ISSUED_ON)

    assert caught.value.code == "R09"
    assert caught.value.message == (
        f"{R09_HEAD}受注 {lump.order.order_no} の {lump.line_no} 行目の出荷が無いので、この一式の行は作り直しで写せません。"
        "作り直す前の請求書を取り消してから、請求書の新規登録で作り直してください。"
    )
    invoice.refresh_from_db()
    assert invoice.status == Invoice.STATUS_ISSUED
    assert Invoice.objects.count() == 1


@pytest.mark.django_db
def test_after_cancel_new_invoice_takes_what_is_left(user, customer, lump_gone):
    """請求書を取り消した後に、請求書の新規登録で残った物 (単価の出荷) だけの請求書を作れる (一式の行は載せない)。"""
    _lump, unit, invoice = lump_gone

    billing_services.cancel_invoice(invoice, user)
    created = billing_services.create_invoice(customer, [unit], user, issued_on=ISSUED_ON)

    assert created.status == Invoice.STATUS_ISSUED
    assert [(row.shipment_id, row.qty) for row in created.lines.all()] == [(unit.pk, Decimal("4"))]


@pytest.mark.django_db
def test_new_invoice_lump_without_shipments_keeps_its_text(user, customer, lump_gone):
    """請求書の新規登録で出荷の無い一式の明細を載せる時の文は今のまま (外すよう書く)。"""
    lump, unit, invoice = lump_gone
    billing_services.cancel_invoice(invoice, user)

    with pytest.raises(RuleViolation) as caught:
        billing_services.create_invoice(customer, [unit], user, issued_on=ISSUED_ON, lumps=[(lump, "1000")])

    assert caught.value.code == "R09"
    assert caught.value.message == f"{R09_HEAD}受注 {lump.order.order_no} の {lump.line_no} 行目の出荷が無いので、外してください。"
