"""明細を出し終えた出荷の印 (Shipment.finishes_line) と、出荷 1 つの金額 (shipping.services.amount_of_shipment) と、納品書の字。

- 印は、入れた順で、明細の出荷の合計を数量の手前から数量以上にした最初の出荷に付く。印の付いた出荷が既にあれば付かない。
- 一式 (単価の単位が式) の明細は、印の付いた出荷だけが一式の全額を持ち、ほかの出荷は 0 (決め PRICE-1c)。
- 部品の単位の明細にも印は付くが、金額は今と同じ数量 × 単価を丸めた値。
- 出荷を直した時は、直した出荷の印だけを数え直す。
- 納品書の帳票は同じ金額の式を通し、単価の後ろに「/ 式」の字を添える。
作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.reports import delivery_note_context

ORDER_DATE = date(2026, 10, 1)
SHIPPED_AT = date(2026, 10, 10)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, qty, unit_price, price_basis):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price), "price_basis": price_basis}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _ship(user, line, qty, shipped_at=SHIPPED_AT):
    shipment = services.ship(line, Decimal(qty), shipped_at, user)
    shipment.refresh_from_db()
    return shipment


@pytest.mark.django_db
def test_lump_mark_and_amount_follow_the_completing_shipment(user, customer, item):
    line = _line(user, customer, item, "300", "50000", "lump")
    first = _ship(user, line, "100")
    second = _ship(user, line, "200")
    extra = _ship(user, line, "10")
    assert [row.finishes_line for row in (first, second, extra)] == [False, True, False]
    assert [services.amount_of_shipment(row) for row in (first, second, extra)] == [
        Decimal("0"),
        Decimal("50000"),
        Decimal("0"),
    ]


@pytest.mark.django_db
def test_single_shipment_of_the_whole_and_more_is_marked(user, customer, item):
    whole = _line(user, customer, item, "300", "50000", "lump")
    assert _ship(user, whole, "300").finishes_line is True
    more = _line(user, customer, item, "300", "50000", "lump")
    shipment = _ship(user, more, "310")
    assert shipment.finishes_line is True
    assert services.amount_of_shipment(shipment) == Decimal("50000")


@pytest.mark.django_db
def test_unit_line_is_marked_but_amount_is_qty_times_price(user, customer, item):
    line = _line(user, customer, item, "10", "120", "unit")
    shipment = _ship(user, line, "10")
    assert shipment.finishes_line is True
    assert services.amount_of_shipment(shipment) == Decimal("1200")


@pytest.mark.django_db
def test_ship_lines_marks_the_same_way(user, customer, item):
    lump = _line(user, customer, item, "300", "50000", "lump")
    unit = _line(user, customer, item, "10", "120", "unit")
    first = services.ship_lines([(lump, Decimal("100")), (unit, Decimal("10"))], SHIPPED_AT, user)
    second = services.ship_lines([(lump, Decimal("200"))], SHIPPED_AT, user)
    for row in (*first, *second):
        row.refresh_from_db()
    assert [row.finishes_line for row in first] == [False, True]
    assert second[0].finishes_line is True


@pytest.mark.django_db
def test_update_shipment_recounts_only_the_edited_shipment(user, customer, item):
    line = _line(user, customer, item, "300", "50000", "lump")
    shipment = _ship(user, line, "100")
    assert shipment.finishes_line is False
    services.update_shipment(shipment, Decimal("300"), SHIPPED_AT, user)
    shipment.refresh_from_db()
    assert shipment.finishes_line is True
    services.update_shipment(shipment, Decimal("100"), SHIPPED_AT, user)
    shipment.refresh_from_db()
    assert shipment.finishes_line is False

    other = _line(user, customer, item, "300", "50000", "lump")
    first = _ship(user, other, "100")
    second = _ship(user, other, "200")
    assert (first.finishes_line, second.finishes_line) == (False, True)
    # 印の付いた出荷が既にあるので、1 つ目を直して合計が数量を超えても、1 つ目に印は付かない。
    services.update_shipment(first, Decimal("150"), SHIPPED_AT, user)
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.finishes_line, second.finishes_line) == (False, True)


@pytest.mark.django_db
def test_reducing_qty_to_shipped_does_not_mark_the_earlier_shipment(user, customer, item):
    line = _line(user, customer, item, "300", "50000", "lump")
    shipment = _ship(user, line, "100")
    orders_services.update_order(line.order, {}, [{"id": line.pk, "qty": Decimal("100")}], user)
    shipment.refresh_from_db()
    assert shipment.finishes_line is False
    assert services.amount_of_shipment(shipment) == Decimal("0")


@pytest.mark.django_db
def test_mark_follows_the_order_of_entry_not_the_shipping_date(user, customer, item):
    line = _line(user, customer, item, "300", "50000", "lump")
    later = _ship(user, line, "200", date(2026, 10, 20))
    earlier = _ship(user, line, "100", date(2026, 10, 10))
    assert (later.finishes_line, earlier.finishes_line) == (False, True)


@pytest.mark.django_db
def test_delivery_note_uses_the_same_amount_and_shows_price_unit(client, user, customer, item):
    lump = _line(user, customer, item, "300", "50000", "lump")
    unit = _line(user, customer, item, "10", "120", "unit")
    first = _ship(user, lump, "100")
    second = _ship(user, lump, "200")
    unit_shipment = _ship(user, unit, "10")
    note = services.create_delivery_note(customer, [first, second], user)
    context = delivery_note_context(note)
    assert [row["amount"] for row in context["lines"]] == [Decimal("0"), Decimal("50000")]
    assert context["total"] == Decimal("50000")
    assert {row["price_unit"] for row in context["lines"]} == {"式"}

    unit_note = services.create_delivery_note(customer, [unit_shipment], user)
    assert [row["price_unit"] for row in delivery_note_context(unit_note)["lines"]] == ["個"]

    client.force_login(user)
    body = client.get(reverse("shipping:deliverynote_print", args=[note.pk])).content.decode()
    assert "¥50,000 / 式" in body
