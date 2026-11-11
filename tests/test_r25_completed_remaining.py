"""R25: 明細 (受注の明細と発注の明細) が終わっているのは、人が付けた完了の印が真か、出荷 (発注は受入) の合計が数量以上のとき。

完了の明細は、足りない分を残に数えない (多く出した分と多く受けた分はマイナスのまま見せる)。
完了の印は人だけが付け外しし、出荷・受入・数量の直しでは書き換えない。出荷済みと受入の数と金額は変えない。
見比べのまとめの M03 と本人の決め 11-11 08:4x・09:3x。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest

from seizou.connect import services as connect_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrderLine, Receipt
from seizou.shipping import services as shipping_services
from seizou.shipping.models import Shipment
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ORDER_DATE = date(2026, 11, 2)
DAY = date(2026, 11, 5)
ENV_NAME = "SEIZOU_PARTNER_R25SUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def other_item(user):
    return masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "個"}, user)


@pytest.fixture
def line(user, customer, item):
    """数量 100・単価 250 の受注の明細。"""
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("100"), "unit_price": Decimal("250")}], user, order_date=ORDER_DATE
    )
    return order.lines.get()


def _fresh(model, pk):
    return model.objects.get(pk=pk)


def _annotated_order_line(pk):
    return orders_services.annotate_remaining(OrderLine.objects.filter(pk=pk)).get()


def _annotated_po_line(pk):
    return purchasing_services.annotate_received(PurchaseOrderLine.objects.filter(pk=pk)).get()


# ---------------------------------------------------------------- 受注の明細


@pytest.mark.django_db
def test_a_completed_short_line_has_no_remaining(user, line):
    shipping_services.ship(line, Decimal("98"), DAY, user)
    orders_services.mark_completed(line, user)

    row = _fresh(OrderLine, line.pk)
    assert row.completed is True
    assert orders_services.remaining_qty(row) == Decimal("0")
    assert orders_services.shipped_qty(row) == Decimal("98")
    assert orders_services.unfilled_qty(row) == Decimal("2")
    annotated = _annotated_order_line(line.pk)
    assert annotated.remaining_total == Decimal("0")
    assert annotated.shipped_total == Decimal("98")


@pytest.mark.django_db
def test_b_clear_brings_back_remaining(user, line):
    shipping_services.ship(line, Decimal("98"), DAY, user)
    orders_services.mark_completed(line, user)
    orders_services.clear_completed(line, user)

    row = _fresh(OrderLine, line.pk)
    assert row.completed is False
    assert orders_services.remaining_qty(row) == Decimal("2")
    assert orders_services.unfilled_qty(row) == Decimal("0")
    assert _annotated_order_line(line.pk).remaining_total == Decimal("2")


@pytest.mark.django_db
def test_c_over_shipment_on_completed_line_stays_negative(user, line):
    shipping_services.ship(line, Decimal("98"), DAY, user)
    orders_services.mark_completed(line, user)
    # services は断らない (現物優先)。候補の口が残の 0 より大きい明細だけを出すので、画面からは選べない。
    shipping_services.ship(_fresh(OrderLine, line.pk), Decimal("5"), DAY, user)

    row = _fresh(OrderLine, line.pk)
    assert row.completed is True
    assert orders_services.remaining_qty(row) == Decimal("-3")
    assert orders_services.unfilled_qty(row) == Decimal("0")
    assert _annotated_order_line(line.pk).remaining_total == Decimal("-3")


@pytest.mark.django_db
def test_d_line_reaching_qty_is_done_without_mark_and_reopens(user, line):
    shipment = shipping_services.ship(line, Decimal("100"), DAY, user)

    row = _fresh(OrderLine, line.pk)
    assert row.completed is False
    assert orders_services.remaining_qty(row) == Decimal("0")
    assert _annotated_order_line(line.pk).remaining_total == Decimal("0")

    shipping_services.update_shipment(shipment, Decimal("90"), DAY, user)
    row = _fresh(OrderLine, line.pk)
    assert row.completed is False
    assert orders_services.remaining_qty(row) == Decimal("10")
    assert _annotated_order_line(line.pk).remaining_total == Decimal("10")


@pytest.mark.django_db
def test_e_completed_line_stays_completed_after_edits(user, line):
    shipment = shipping_services.ship(line, Decimal("98"), DAY, user)
    orders_services.mark_completed(line, user)

    shipping_services.update_shipment(shipment, Decimal("90"), DAY, user)
    row = _fresh(OrderLine, line.pk)
    assert row.completed is True
    assert orders_services.remaining_qty(row) == Decimal("0")
    assert orders_services.unfilled_qty(row) == Decimal("10")

    orders_services.update_order(line.order, {}, [{"order_line": line, "qty": Decimal("120")}], user)
    row = _fresh(OrderLine, line.pk)
    assert row.completed is True
    assert row.qty == Decimal("120")
    assert orders_services.remaining_qty(row) == Decimal("0")
    assert _annotated_order_line(line.pk).remaining_total == Decimal("0")


@pytest.mark.django_db
def test_g_completion_keeps_shipment_qty_and_amount(user, line):
    shipment = shipping_services.ship(line, Decimal("98"), DAY, user)
    before = (shipment.qty, shipping_services.amount_of_shipment(_fresh(Shipment, shipment.pk)), line.amount)

    orders_services.mark_completed(line, user)
    after_shipment = _fresh(Shipment, shipment.pk)
    after_line = _fresh(OrderLine, line.pk)
    assert (after_shipment.qty, shipping_services.amount_of_shipment(after_shipment), after_line.amount) == before
    assert after_line.qty == Decimal("100")


# ---------------------------------------------------------------- 発注の明細 (f)


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def po_line(user, subcontractor, item):
    """送っていない (下書きの) 発注の、数量 100・単価 30 の明細。"""
    po = purchasing_services.create_purchase_order(
        subcontractor,
        [{"item": item, "qty": Decimal("100"), "unit_price": Decimal("30")}],
        user,
        order_date=ORDER_DATE,
    )
    return po.lines.get()


@pytest.fixture
def sent_lines(user, item, other_item, monkeypatch):
    """送った (相手が受け取った) 発注の明細 2 行 (数量 100 と 10)。発注の「変更」で数量を直すために使う。"""
    monkeypatch.setenv(ENV_NAME, _new_secret())
    monkeypatch.setattr("requests.post", FakePost())
    partner = masters_services.create_partner(
        {"code": "S002", "name": "作り物の協力企業 2", "role": "subcontractor"}, user
    )
    connect_services.register_connection(partner, PARTNER_URL, ENV_NAME, "send", user)
    po = purchasing_services.create_purchase_order(
        partner,
        [
            {"item": item, "qty": Decimal("100"), "unit_price": Decimal("30")},
            {"item": other_item, "qty": Decimal("10"), "unit_price": Decimal("40")},
        ],
        user,
        order_date=ORDER_DATE,
    )
    return list(po.lines.order_by("line_no"))


def _change_qty(user, first, second, qty):
    """発注の「変更」で 1 行目の数量を qty に直して送る (2 行目はそのまま)。"""
    po = first.purchase_order
    purchasing_services.change_purchase_order(
        po,
        {},
        [{"line": first, "qty": Decimal(qty)}, {"line": second}],
        user,
    )


@pytest.mark.django_db
def test_f_a_completed_short_po_line_has_no_remaining(user, po_line):
    purchasing_services.receive(po_line, Decimal("98"), DAY, user)
    purchasing_services.mark_completed(po_line, user)

    row = _fresh(PurchaseOrderLine, po_line.pk)
    assert row.completed is True
    assert purchasing_services.remaining_qty(row) == Decimal("0")
    assert purchasing_services.received_qty(row) == Decimal("98")
    assert purchasing_services.unfilled_qty(row) == Decimal("2")
    annotated = _annotated_po_line(po_line.pk)
    assert annotated.remaining_total == Decimal("0")
    assert annotated.received_total == Decimal("98")


@pytest.mark.django_db
def test_f_b_clear_brings_back_po_remaining(user, po_line):
    purchasing_services.receive(po_line, Decimal("98"), DAY, user)
    purchasing_services.mark_completed(po_line, user)
    purchasing_services.clear_completed(po_line, user)

    row = _fresh(PurchaseOrderLine, po_line.pk)
    assert row.completed is False
    assert purchasing_services.remaining_qty(row) == Decimal("2")
    assert purchasing_services.unfilled_qty(row) == Decimal("0")
    assert _annotated_po_line(po_line.pk).remaining_total == Decimal("2")


@pytest.mark.django_db
def test_f_c_over_receipt_on_completed_po_line_stays_negative(user, po_line):
    purchasing_services.receive(po_line, Decimal("98"), DAY, user)
    purchasing_services.mark_completed(po_line, user)
    purchasing_services.receive(_fresh(PurchaseOrderLine, po_line.pk), Decimal("5"), DAY, user)

    row = _fresh(PurchaseOrderLine, po_line.pk)
    assert row.completed is True
    assert purchasing_services.remaining_qty(row) == Decimal("-3")
    assert _annotated_po_line(po_line.pk).remaining_total == Decimal("-3")


@pytest.mark.django_db
def test_f_d_po_line_reaching_qty_is_done_without_mark_and_reopens(user, sent_lines):
    first, second = sent_lines
    purchasing_services.receive(first, Decimal("100"), DAY, user)

    row = _fresh(PurchaseOrderLine, first.pk)
    assert row.completed is False
    assert purchasing_services.remaining_qty(row) == Decimal("0")

    _change_qty(user, row, second, "110")
    row = _fresh(PurchaseOrderLine, first.pk)
    assert row.completed is False
    assert purchasing_services.remaining_qty(row) == Decimal("10")
    assert _annotated_po_line(first.pk).remaining_total == Decimal("10")


@pytest.mark.django_db
def test_f_e_completed_po_line_stays_completed_after_change(user, sent_lines):
    first, second = sent_lines
    purchasing_services.receive(first, Decimal("98"), DAY, user)
    purchasing_services.mark_completed(first, user)

    _change_qty(user, _fresh(PurchaseOrderLine, first.pk), second, "120")
    row = _fresh(PurchaseOrderLine, first.pk)
    assert row.completed is True
    assert row.qty == Decimal("120")
    assert purchasing_services.remaining_qty(row) == Decimal("0")
    assert purchasing_services.unfilled_qty(row) == Decimal("22")
    assert _annotated_po_line(first.pk).remaining_total == Decimal("0")


@pytest.mark.django_db
def test_f_g_completion_keeps_receipt_qty_and_amount(user, po_line):
    receipt = purchasing_services.receive(po_line, Decimal("98"), DAY, user)
    before = (receipt.qty, receipt.amount, po_line.amount)

    purchasing_services.mark_completed(po_line, user)
    after_receipt = _fresh(Receipt, receipt.pk)
    after_line = _fresh(PurchaseOrderLine, po_line.pk)
    assert (after_receipt.qty, after_receipt.amount, after_line.amount) == before
    assert after_line.qty == Decimal("100")
