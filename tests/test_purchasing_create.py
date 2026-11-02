"""発注を作る関数 (01 の 10.4 の create_purchase_order)。取引先と品目は作り物。"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder, PurchaseOrderLine

ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の外注先", "role": "subcontractor", "rounding": "round"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def no_http(monkeypatch):
    """HTTP を呼んだら落とす。つながりが無い発注は送らないことを見る。"""

    def fail(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


def _line(item, qty="2", unit_price="100", **extra):
    return {"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price), **extra}


@pytest.mark.django_db
def test_create_with_two_lines(user, subcontractor, item, no_http):
    other = masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)
    po = services.create_purchase_order(
        subcontractor,
        [_line(item, due_date=date(2026, 11, 20), note="急ぎ"), _line(other, "5", "30", unit="kg")],
        user,
        order_date=ORDER_DATE,
        note="作り物の発注",
    )
    assert po.po_no.startswith("PO-")
    assert po.status == PurchaseOrder.STATUS_DRAFT
    assert po.order_date == ORDER_DATE
    assert po.note == "作り物の発注"
    lines = list(po.lines.order_by("line_no"))
    assert [line.line_no for line in lines] == [1, 2]
    assert lines[0].unit == "個"  # 単位が無ければ品目の単位を写す
    assert lines[0].due_date == date(2026, 11, 20) and lines[0].note == "急ぎ"
    assert lines[1].unit == "kg"
    assert lines[1].amount == Decimal("150")


@pytest.mark.django_db
def test_partner_must_be_subcontractor(user, item, no_http):
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    with pytest.raises(ValueError, match="外注先ではありません"):
        services.create_purchase_order(customer, [_line(item)], user, order_date=ORDER_DATE)
    assert PurchaseOrder.objects.count() == 0

    both = masters_services.create_partner(
        {"code": "B001", "name": "作り物の両方の取引先", "role": "both"}, user
    )
    po = services.create_purchase_order(both, [_line(item)], user, order_date=ORDER_DATE)
    assert po.partner == both


@pytest.mark.django_db
def test_lines_must_not_be_empty(user, subcontractor, no_http):
    with pytest.raises(ValueError, match="明細がありません"):
        services.create_purchase_order(subcontractor, [], user, order_date=ORDER_DATE)
    assert PurchaseOrder.objects.count() == 0


@pytest.mark.django_db
def test_unknown_line_key_is_value_error(user, subcontractor, item, no_http):
    with pytest.raises(ValueError, match="知らない項目"):
        services.create_purchase_order(
            subcontractor, [_line(item, amount=Decimal("1"))], user, order_date=ORDER_DATE
        )
    assert PurchaseOrder.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(("qty", "unit_price"), [("0", "100"), ("2", "0"), ("-1", "100"), ("2", "-5")])
def test_qty_and_price_must_be_positive(user, subcontractor, item, no_http, qty, unit_price):
    with pytest.raises(ValueError, match="0 より大きい"):
        services.create_purchase_order(
            subcontractor, [_line(item), _line(item, qty, unit_price)], user, order_date=ORDER_DATE
        )
    # 2 行目で止まっても 1 行目も発注も残らない
    assert PurchaseOrder.objects.count() == 0
    assert PurchaseOrderLine.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(("rounding", "expected"), [("round", "100"), ("ceil", "101"), ("floor", "100")])
def test_amount_is_rounded_by_partner(user, item, no_http, rounding, expected):
    partner = masters_services.create_partner(
        {"code": "S002", "name": "作り物の外注先", "role": "subcontractor", "rounding": rounding}, user
    )
    # 3 × 33.35 = 100.05
    po = services.create_purchase_order(
        partner, [_line(item, "3", "33.35")], user, order_date=ORDER_DATE
    )
    assert po.lines.get().amount == Decimal(expected)


@pytest.mark.django_db
def test_db_constraint_backs_positive_qty(user, subcontractor, item, no_http):
    po = services.create_purchase_order(subcontractor, [_line(item)], user, order_date=ORDER_DATE)
    with pytest.raises(IntegrityError), transaction.atomic():
        PurchaseOrderLine.objects.create(
            purchase_order=po, line_no=2, item=item, qty=Decimal("0"),
            unit_price=Decimal("1"), amount=Decimal("0"),
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        PurchaseOrderLine.objects.create(
            purchase_order=po, line_no=3, item=item, qty=Decimal("1"),
            unit_price=Decimal("0"), amount=Decimal("0"),
        )


@pytest.mark.django_db
def test_po_no_comes_from_next_number(user, subcontractor, item, no_http):
    first = services.create_purchase_order(subcontractor, [_line(item)], user, order_date=ORDER_DATE)
    second = services.create_purchase_order(subcontractor, [_line(item)], user, order_date=ORDER_DATE)
    assert (first.po_no, second.po_no) == ("PO-000001", "PO-000002")
    assert str(first) == "PO-000001"


@pytest.mark.django_db
def test_no_connection_means_not_sent(user, subcontractor, item, no_http):
    # HTTP を呼べば no_http が落とす。送った注文の表の行が無いことは tests/test_connect_send.py が見る。
    po = services.create_purchase_order(subcontractor, [_line(item)], user, order_date=ORDER_DATE)
    assert po.status == PurchaseOrder.STATUS_DRAFT
    assert po.partner.connections.count() == 0
