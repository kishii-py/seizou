"""請求した時の数量と今の出荷の数量が違う明細 (billing.services.changed_lines。読むだけ)。

請求済みの出荷を後で直しても止めない (直す前に検査の層の R27 が知らせる。本人の決め 11-11 12:5x〜13:0x) ので、
直した後の食い違いを請求書の側から読む。出荷の行は明細の数量 (写し) と出荷の今の数量、一式の行は明細の数量
(請求した時の出荷の合計の写し) と、その受注の明細の今の出荷の合計を比べる。取消の請求書は空の並び。問い合わせは 2 回まで。
作り物のデータは全部 services で作る。
"""

from datetime import date
from decimal import Decimal

import pytest

from seizou.billing import services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, qty="100", price_basis="unit"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100"), "price_basis": price_basis}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _lump_billed(user, customer, item):
    """一式 300 を 100 出荷し (納品書なし)、完了の印で終えて、一式の行 (金額 1000) で請求した明細と、その出荷と請求書。"""
    line = _line(user, customer, item, qty="300", price_basis="lump")
    shipment = shipping_services.ship(line, Decimal("100"), SHIPPED_AT, user)
    orders_services.mark_completed(line, user)
    line.refresh_from_db()
    invoice = services.create_invoice(customer, [], user, issued_on=ISSUED_ON, lumps=[(line, "1000")])
    return line, shipment, invoice


@pytest.mark.django_db
def test_shipment_line_is_listed_after_the_shipment_is_changed(user, customer, item):
    line = _line(user, customer, item)
    kept = shipping_services.ship(line, Decimal("30"), SHIPPED_AT, user)
    changed = shipping_services.ship(line, Decimal("50"), SHIPPED_AT, user)
    invoice = services.create_invoice(customer, [kept, changed], user, issued_on=ISSUED_ON)
    assert services.changed_lines(invoice) == []

    shipping_services.update_shipment(changed, Decimal("40"), SHIPPED_AT, user)

    found = services.changed_lines(invoice)
    assert [(row.shipment_id, billed, now) for row, billed, now in found] == [(changed.pk, Decimal("50"), Decimal("40"))]
    # 明細の数量 (写し) は変わらない。
    assert invoice.lines.get(shipment=changed).qty == Decimal("50")


@pytest.mark.django_db
def test_lump_line_is_listed_after_a_shipment_of_the_line_is_changed(user, customer, item):
    line, shipment, invoice = _lump_billed(user, customer, item)
    assert services.changed_lines(invoice) == []

    shipping_services.update_shipment(shipment, Decimal("80"), SHIPPED_AT, user)

    found = services.changed_lines(invoice)
    assert [(row.order_line_id, row.shipment_id, billed, now) for row, billed, now in found] == [
        (line.pk, None, Decimal("100"), Decimal("80"))
    ]


@pytest.mark.django_db
def test_lump_line_with_all_shipments_deleted_is_listed_with_zero(user, customer, item):
    line, shipment, invoice = _lump_billed(user, customer, item)

    shipping_services.delete_shipment(shipment, user)

    assert [(billed, now) for _row, billed, now in services.changed_lines(invoice)] == [(Decimal("100"), Decimal("0"))]


@pytest.mark.django_db
def test_cancelled_invoice_is_empty(user, customer, item):
    line = _line(user, customer, item)
    shipment = shipping_services.ship(line, Decimal("50"), SHIPPED_AT, user)
    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    shipping_services.update_shipment(shipment, Decimal("40"), SHIPPED_AT, user)
    assert len(services.changed_lines(invoice)) == 1

    cancelled = services.cancel_invoice(invoice, user)

    assert services.changed_lines(cancelled) == []


@pytest.mark.django_db
def test_lines_keep_invoice_order_and_take_two_queries(user, customer, item, django_assert_max_num_queries):
    unit = _line(user, customer, item)
    first = shipping_services.ship(unit, Decimal("10"), SHIPPED_AT, user)
    second = shipping_services.ship(unit, Decimal("20"), SHIPPED_AT, user)
    lump = _line(user, customer, item, qty="300", price_basis="lump")
    lump_shipment = shipping_services.ship(lump, Decimal("100"), SHIPPED_AT, user)
    orders_services.mark_completed(lump, user)
    lump.refresh_from_db()
    invoice = services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON, lumps=[(lump, "1000")])
    for shipment, qty in ((first, "11"), (second, "19"), (lump_shipment, "90")):
        shipping_services.update_shipment(shipment, Decimal(qty), SHIPPED_AT, user)

    with django_assert_max_num_queries(2):
        found = services.changed_lines(invoice)
        rows = [(row.shipment_id, row.order_line_id, billed, now) for row, billed, now in found]

    assert rows == [
        (first.pk, unit.pk, Decimal("10"), Decimal("11")),
        (second.pk, unit.pk, Decimal("20"), Decimal("19")),
        (None, lump.pk, Decimal("100"), Decimal("90")),
    ]
