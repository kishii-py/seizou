"""請求書の明細の 2 つの形 (出荷の行と一式の行) と、出荷の行の金額 (決め PRICE-1c・PRICE-1d)。

- 一式 (単価の単位が式) の明細の出荷の行は、明細を出し終えた出荷だけが一式の全額、ほかの出荷は 0 円で請求に載る。
- 明細は受注の明細 (order_line) と単価の単位 (price_basis) の写しを持つ。部品の単位の明細の金額は今と同じ。
- 請求書の新規登録・詳細・印刷の字に、一式の印「(一式)」と単価の単位「/ 式」を添える。
- 一式の行 (出荷が空) があっても、「請求書に入っていない出荷」の 2 つの関数が全部の出荷を落とさない (NOT IN の見張り)。
- 制約: 出荷の行か一式の行のどちらかの形だけ。1 枚の請求書に同じ受注の明細の一式の行は 1 行だけ。
- 一式の行のある受注の明細の後の出荷には、明細を出し終えた出荷の印を付けない。
一式の行を作る関数はまだ無い (後の依頼) ので、一式の行は試験の中で表に直接作る。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from seizou.billing import services
from seizou.billing.models import Invoice, InvoiceLine
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from tests.billing_helpers import shipped

OCT_10 = date(2026, 10, 10)
NOV_05 = date(2026, 11, 5)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, qty="300", unit_price="50000", price_basis="lump"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price), "price_basis": price_basis}],
        user,
        order_date=date(2026, 10, 1),
    )
    return order.lines.get()


def _issued_invoice(customer, invoice_no="IV-T0001"):
    """一式の行を試験の中で載せる、発行の請求書 (金額は試験で見ない)。"""
    return Invoice.objects.create(
        invoice_no=invoice_no,
        customer=customer,
        issued_on=date(2026, 12, 31),
        period_from=date(2026, 12, 1),
        period_to=date(2026, 12, 31),
        subtotal=Decimal("16667"),
        tax=Decimal("1666"),
        total=Decimal("18333"),
    )


def _lump_line(invoice, line, **overrides):
    values = {
        "invoice": invoice,
        "shipment": None,
        "order_line": line,
        "qty": Decimal("100"),
        "unit_price": Decimal("16667"),
        "price_basis": "lump",
        "amount": Decimal("16667"),
        "last_shipped_at": OCT_10,
    }
    values.update(overrides)
    return InvoiceLine.objects.create(**values)


@pytest.mark.django_db
def test_lump_shipment_lines_carry_zero_until_the_completing_shipment(user, customer, item):
    line = _line(user, customer, item)
    first = shipped(user, customer, line, "100", OCT_10)
    october = services.create_invoice(customer, [first], user, issued_on=date(2026, 10, 31))
    row = october.lines.get()
    assert (row.amount, row.unit_price, row.price_basis) == (Decimal("0"), Decimal("50000"), "lump")
    assert row.order_line == line and row.last_shipped_at is None
    assert (october.subtotal, october.tax) == (Decimal("0"), Decimal("0"))

    second = shipped(user, customer, line, "200", NOV_05)
    november = services.create_invoice(customer, [second], user, issued_on=date(2026, 11, 30))
    assert november.lines.get().amount == Decimal("50000")
    assert november.subtotal == Decimal("50000")


@pytest.mark.django_db
def test_unit_lines_keep_the_same_amount_and_copy_order_line(user, customer, item):
    line = _line(user, customer, item, qty="10", unit_price="137.5", price_basis="unit")
    shipment = shipped(user, customer, line, "10")
    invoice = services.create_invoice(customer, [shipment], user, issued_on=date(2026, 11, 30))
    row = invoice.lines.get()
    assert row.amount == Decimal("1375")
    assert row.price_basis == "unit"
    # 作る前からある明細も、移す migration で出荷の受注の明細が入る (作った明細で同じ値を見る)。
    assert row.order_line == shipment.order_line == line
    assert row.last_shipped_at is None
    assert (row.shipped_on, row.ref_no, row.price_unit_text) == (shipment.shipped_at, shipment.shipment_no, "個")


@pytest.mark.django_db
def test_create_form_marks_lump_rows_with_server_amount(client, user, customer, item):
    line = _line(user, customer, item)
    shipped(user, customer, line, "100", OCT_10)
    shipped(user, customer, line, "200", NOV_05)
    client.force_login(user)
    body = client.get(reverse("billing:invoice_create")).content.decode()
    assert 'data-label-pick-amount="0"' in body
    assert 'data-label-pick-amount="50000"' in body
    assert body.count("(一式)") == 2


@pytest.mark.django_db
def test_detail_and_print_show_price_unit(client, user, customer, item):
    lump = _line(user, customer, item)
    unit = _line(user, customer, item, qty="10", unit_price="1375", price_basis="unit")
    lump_shipment = shipped(user, customer, lump, "300", NOV_05)
    unit_shipment = shipped(user, customer, unit, "10", NOV_05)
    invoice = services.create_invoice(customer, [lump_shipment, unit_shipment], user, issued_on=date(2026, 11, 30))
    client.force_login(user)
    detail = client.get(reverse("billing:invoice_detail", args=[invoice.pk])).content.decode()
    assert '50,000<span class="text-base-content/70"> / 式</span>' in detail
    assert '1,375<span class="text-base-content/70"> / 個</span>' in detail
    printed = client.get(reverse("billing:invoice_print", args=[invoice.pk])).content.decode()
    assert "¥50,000 / 式" in printed
    assert "¥1,375 / 個" in printed


@pytest.mark.django_db
def test_lump_line_does_not_hide_unbilled_shipments(user, customer, item):
    line = _line(user, customer, item)
    shipment = shipped(user, customer, line, "100", OCT_10)
    _lump_line(_issued_invoice(customer), line)
    assert list(services.billable_shipments()) == [shipment]
    assert list(services.unbilled_in_period(customer, date(2026, 10, 1), date(2026, 10, 31))) == [shipment]


@pytest.mark.django_db
def test_constraints_keep_the_two_shapes(user, customer, item):
    line = _line(user, customer, item)
    shipment = shipped(user, customer, line, "100", OCT_10)
    invoice = _issued_invoice(customer)
    bad = [
        {"last_shipped_at": None},  # 出荷も最後の出荷日も無い
        {"shipment": shipment},  # 出荷と最後の出荷日の両方がある
        {"price_basis": "unit"},  # 出荷が空で単価の単位が部品の単位
    ]
    for overrides in bad:
        with pytest.raises(IntegrityError), transaction.atomic():
            _lump_line(invoice, line, **overrides)
    _lump_line(invoice, line)
    with pytest.raises(IntegrityError), transaction.atomic():
        _lump_line(invoice, line)
    assert str(invoice.lines.get()) == f"IV-T0001 受注 {line.order.order_no}-1 の一式"


@pytest.mark.django_db
def test_no_mark_after_a_lump_line_is_issued(user, customer, item):
    line = _line(user, customer, item)
    first = shipped(user, customer, line, "100", OCT_10)
    orders_services.update_order(line.order, {}, [{"id": line.pk, "qty": Decimal("100")}], user)
    assert shipping_services._has_issued_lump_line(line) is False
    invoice = _issued_invoice(customer)
    _lump_line(invoice, line)
    assert shipping_services._has_issued_lump_line(line) is True

    # 受注の数量を戻して続きを出荷しても、一式の全額をもう一度自動で載せない (印を付けない)。
    orders_services.update_order(line.order, {}, [{"id": line.pk, "qty": Decimal("300")}], user)
    later = shipping_services.ship(line, Decimal("200"), NOV_05, user)
    later.refresh_from_db()
    assert later.finishes_line is False
    assert shipping_services.amount_of_shipment(later) == Decimal("0")

    # 納品書を取り消してから最初の出荷を直しても、印は付かない。
    # 一式の行で請求した明細の納品書は、前は services では取り消せなかった (作者の決め C107-6。N403b で取り消せる形にした) ので、
    # 断りの前に取り消した納品書の形を表に直接作り、update_shipment の印の見方だけを見る。
    type(first.delivery_note).objects.filter(pk=first.delivery_note_id).update(status="cancelled")
    first.refresh_from_db()
    shipping_services.update_shipment(first, Decimal("300"), OCT_10, user)
    first.refresh_from_db()
    assert first.finishes_line is False

    services.cancel_invoice(invoice, user)
    assert shipping_services._has_issued_lump_line(line) is False
