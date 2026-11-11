"""請求書の詳細の、請求した後に出荷の数量が変わった明細の黄色の 1 行 (知らせの段 5 = 記録の今の状態。依頼書 N403b)。

請求済みの出荷を直すか消しても止めない (R27 が知らせる。本人の決め 11-11 12:5x〜13:0x) ので、直した後の食い違いを請求書の詳細で見せる。
字は billing.services.changed_lines(invoice) を billing_tags の invoice_changed_note が 1 回だけ引いて作る。出荷の行は「S-… (請求 50・今 48)」、
一式の行は「受注 SO-… の n 行目の一式 (請求 100・今 60)」。金額は書かない (金額の閲覧の無い人にも同じ字)。取消の請求書と直していない請求書には出ない。
作り物のデータは全部 services で作る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from tests.test_billing_payment_views import ALL_ON, account

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
NEW_SHIPPED_AT = date(2026, 11, 6)
ISSUED_ON = date(2026, 11, 30)
HEAD = "請求した後に出荷の数量が変わった明細があります: "
TAIL = "。請求書も直すときは、取り消して作り直してください。"
#: 黄色の 1 行の印 (billing/invoice_detail.html)
MARK = "data-changed-lines"


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, qty, price_basis="unit"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100"), "price_basis": price_basis}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def billed(user, customer, item):
    """単価の明細の出荷 50 と、一式 300 の明細の出荷 60 と 40 (完了の印で終える) を、出荷の行と一式の行 (金額 1000) で 1 枚に請求した物。

    納品書は作らない (出荷を直すときの R08 を避ける)。(単価の出荷, 一式の明細, 一式の 2 つ目の出荷, 請求書) を返す。
    """
    unit = shipping_services.ship(_line(user, customer, item, "100"), Decimal("50"), SHIPPED_AT, user)
    lump = _line(user, customer, item, "300", "lump")
    shipping_services.ship(lump, Decimal("60"), SHIPPED_AT, user)
    second = shipping_services.ship(lump, Decimal("40"), SHIPPED_AT, user)
    orders_services.mark_completed(lump, user)
    lump.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [unit], user, issued_on=ISSUED_ON, lumps=[(lump, "1000")])
    return unit, lump, second, invoice


def _detail(client, invoice):
    return client.get(reverse("billing:invoice_detail", args=[invoice.pk])).content.decode()


def _changed_line(body):
    """黄色の 1 行の中身 (印から閉じの </div> まで)。"""
    part = body[body.index(MARK) :]
    return part[: part.index("</div>")]


@pytest.mark.django_db
def test_changed_shipment_line_shows_yellow_line(user, logged_in, billed):
    """出荷の行の出荷を 50 から 48 に直すと、黄色の 1 行に出荷番号と「(請求 50・今 48)」が出る。金額は書かない。"""
    unit, _lump, _second, invoice = billed
    assert MARK not in _detail(logged_in, invoice)

    shipping_services.update_shipment(unit, Decimal("48"), NEW_SHIPPED_AT, user)
    body = _detail(logged_in, invoice)

    line = _changed_line(body)
    assert "alert-warning" in body[: body.index(MARK)][-200:]
    assert f"{HEAD}{unit.shipment_no} (請求 50・今 48){TAIL}" in line
    assert "¥" not in line


@pytest.mark.django_db
def test_changed_lump_line_names_order_line(user, logged_in, billed):
    """一式の行は「受注 <番号> の n 行目の一式 (請求 100・今 60)」の形。出荷の行と一緒に変われば「・」で並ぶ (明細の並びのまま)。"""
    unit, lump, second, invoice = billed

    shipping_services.delete_shipment(second, user)
    lump_text = f"受注 {lump.order.order_no} の {lump.line_no} 行目の一式 (請求 100・今 60)"
    assert f"{HEAD}{lump_text}{TAIL}" in _changed_line(_detail(logged_in, invoice))

    shipping_services.update_shipment(unit, Decimal("48"), NEW_SHIPPED_AT, user)
    assert f"{HEAD}{unit.shipment_no} (請求 50・今 48)・{lump_text}{TAIL}" in _changed_line(_detail(logged_in, invoice))


@pytest.mark.django_db
def test_no_yellow_line_for_cancelled_invoice(user, logged_in, billed):
    """取消の請求書には出ない (changed_lines が空を返す)。"""
    unit, _lump, _second, invoice = billed
    shipping_services.update_shipment(unit, Decimal("48"), NEW_SHIPPED_AT, user)
    billing_services.cancel_invoice(invoice, user)

    body = _detail(logged_in, invoice)

    assert MARK not in body
    assert "請求した後に出荷の数量が変わった明細" not in body


@pytest.mark.django_db
def test_same_text_without_money(settings, client, user, billed):
    """金額の閲覧の無い人にも同じ字 (金額を書かないため)。"""
    unit, _lump, _second, invoice = billed
    shipping_services.update_shipment(unit, Decimal("48"), NEW_SHIPPED_AT, user)
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    client.force_login(account("jimu", permissions=["billing"]))

    body = _detail(client, invoice)

    assert f"{HEAD}{unit.shipment_no} (請求 50・今 48){TAIL}" in _changed_line(body)
