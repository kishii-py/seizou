"""出し切らずに終えた一式の受注の明細 (billing.services.unbilled_lumps) と、その請求の材料 (lump_facts) と、
請求書の新規登録の黄の 1 行と表 (決め PRICE-1d の「請求書を作る画面で知らせて、人が金額を決めて載せる」の知らせる所)。

出す物 = 単価の単位が式・出荷が 1 つ以上ある (納品書を発行したかどうかは見ない。本人の決め 11-11 10:1x)・明細を出し終えた出荷の印が無い・
終わっている (受注が取消か、出荷の合計が数量以上)・発行の請求書に一式の行がまだ無い、の 5 つが全部当たる明細。
一式の行を作る関数はまだ無い (後の依頼) ので、一式の行は試験の中で表に直接作る。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services
from seizou.billing.models import Invoice, InvoiceLine
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from tests.billing_helpers import shipped

OCT_10 = date(2026, 10, 10)
OCT_20 = date(2026, 10, 20)
#: 金額の閲覧だけを効かせる (部署と書く権限の判定はしない。tests/test_core_line_totals.py と同じ)
NO_MONEY = {"department": "off", "write": "off", "money": "on"}
#: 一式の節の頭の tag (JS の字の "[data-lump-notice]" と分ける)
LUMP_SECTION = '<div class="mt-6" data-lump-notice>'


def _customer(user, code="C001", rounding="round"):
    return masters_services.create_partner(
        {"code": code, "name": f"作り物の得意先 {code}", "role": "customer", "rounding": rounding}, user
    )


@pytest.fixture
def customer(user):
    return _customer(user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, qty="300", price_basis="lump"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("50000"), "price_basis": price_basis}],
        user,
        order_date=date(2026, 10, 1),
    )
    return order.lines.get()


def _cancelled_lump(user, customer, item):
    """一式 300 を 100 出荷して納品書を発行し、受注を取り消した明細。"""
    line = _line(user, customer, item)
    shipped(user, customer, line, "100", OCT_10)
    orders_services.cancel_order(line.order, user)
    line.refresh_from_db()
    return line


def _reduced_lump(user, customer, item):
    """一式 300 を 100 出荷して納品書を発行し、受注の数量を 100 に直した明細 (数量を減らして終えた形)。"""
    line = _line(user, customer, item)
    shipped(user, customer, line, "100", OCT_10)
    orders_services.update_order(line.order, {}, [{"id": line.pk, "qty": Decimal("100")}], user)
    line.refresh_from_db()
    return line


def _issued_lump_line(customer, line):
    invoice = Invoice.objects.create(
        invoice_no="IV-T0001",
        customer=customer,
        issued_on=date(2026, 11, 30),
        period_from=date(2026, 11, 1),
        period_to=date(2026, 11, 30),
        subtotal=Decimal("16667"),
        tax=Decimal("1666"),
        total=Decimal("18333"),
    )
    InvoiceLine.objects.create(
        invoice=invoice,
        shipment=None,
        order_line=line,
        qty=Decimal("100"),
        unit_price=Decimal("16667"),
        price_basis="lump",
        amount=Decimal("16667"),
        last_shipped_at=OCT_10,
    )
    return invoice


# ---------------------------------------------------------------- unbilled_lumps: 出る物


@pytest.mark.django_db
def test_cancelled_and_reduced_lumps_are_listed(user, customer, item):
    cancelled = _cancelled_lump(user, customer, item)
    reduced = _reduced_lump(user, customer, item)
    assert services.unbilled_lumps() == [cancelled, reduced]


@pytest.mark.django_db
def test_lump_stays_listed_after_its_zero_shipment_is_billed(user, customer, item):
    line = _line(user, customer, item)
    shipment = shipped(user, customer, line, "100", OCT_10)
    invoice = services.create_invoice(customer, [shipment], user, issued_on=date(2026, 10, 31))
    assert invoice.lines.get().amount == Decimal("0")
    orders_services.cancel_order(line.order, user)
    assert services.unbilled_lumps() == [line]


# ---------------------------------------------------------------- unbilled_lumps: 出ない物


@pytest.mark.django_db
def test_open_completed_unshipped_unit_lumps_are_not_listed(user, customer, item):
    # (d) 受注が開いていて、出荷が数量に届いていない。
    open_line = _line(user, customer, item)
    shipped(user, customer, open_line, "100", OCT_10)
    # (e) 全部を出し終えた出荷 (印) がある。
    done = _line(user, customer, item)
    shipped(user, customer, done, "100", OCT_10)
    shipped(user, customer, done, "200", OCT_20)
    # (g) 出荷が無いまま取消。
    empty = _line(user, customer, item)
    orders_services.cancel_order(empty.order, user)
    # (h) 部品の単位の明細。
    unit = _line(user, customer, item, price_basis="unit")
    shipped(user, customer, unit, "100", OCT_10)
    orders_services.cancel_order(unit.order, user)
    assert services.unbilled_lumps() == []


@pytest.mark.django_db
def test_lump_with_shipment_outside_issued_note_is_listed(user, customer, item):
    # (f) 出荷の 1 つが納品書に入っていない明細も並ぶ (納品書を発行したかどうかは見ない。本人の決め 11-11 10:1x)。
    no_note = _line(user, customer, item)
    shipped(user, customer, no_note, "60", OCT_10)
    shipping_services.ship(no_note, Decimal("40"), OCT_20, user)
    orders_services.cancel_order(no_note.order, user)
    # (f) 出荷が取消の納品書にしか入っていない明細も並ぶ。
    cancelled_note = _line(user, customer, item)
    shipment = shipped(user, customer, cancelled_note, "100", OCT_10)
    shipping_services.cancel_delivery_note(shipment.delivery_note, user)
    orders_services.cancel_order(cancelled_note.order, user)
    assert services.unbilled_lumps() == [no_note, cancelled_note]
    # 取消の納品書の出荷を、新しい納品書に入れ直しても並んだまま。
    shipment.refresh_from_db()
    shipping_services.create_delivery_note(customer, [shipment], user)
    assert services.unbilled_lumps() == [no_note, cancelled_note]


@pytest.mark.django_db
def test_customer_filter(user, customer, item):
    other = _customer(user, code="C002")
    line = _cancelled_lump(user, customer, item)
    assert services.unbilled_lumps(customer) == [line]
    assert services.unbilled_lumps(other) == []


@pytest.mark.django_db
def test_lump_with_issued_lump_line_is_not_listed_until_cancelled(user, customer, item):
    # (j) 発行の請求書に一式の行がある。その請求書を取消にすると、また出る。
    line = _reduced_lump(user, customer, item)
    invoice = _issued_lump_line(customer, line)
    assert services.unbilled_lumps() == []
    services.cancel_invoice(invoice, user)
    assert services.unbilled_lumps() == [line]


# ---------------------------------------------------------------- lump_facts


@pytest.mark.django_db
def test_lump_facts(user, customer, item):
    line = _cancelled_lump(user, customer, item)
    expected = {
        "shipped_qty": Decimal("100"),
        "last_shipped_at": OCT_10,
        "full_amount": Decimal("50000"),
        "shipped_amount": Decimal("16667"),
    }
    assert services.lump_facts(line) == expected
    # unbilled_lumps が足した合計を読んでも同じ。
    assert services.lump_facts(services.unbilled_lumps()[0]) == expected

    floor = _customer(user, code="C002", rounding="floor")
    assert services.lump_facts(_cancelled_lump(user, floor, item))["shipped_amount"] == Decimal("16666")


@pytest.mark.django_db
def test_split_shipments_are_not_counted_twice(user, customer, item):
    line = _line(user, customer, item)
    shipped(user, customer, line, "40", OCT_10)
    shipped(user, customer, line, "60", OCT_20)
    orders_services.cancel_order(line.order, user)
    [listed] = services.unbilled_lumps()
    assert listed.shipped_total == Decimal("100")
    facts = services.lump_facts(listed)
    assert (facts["shipped_qty"], facts["last_shipped_at"]) == (Decimal("100"), OCT_20)


# ---------------------------------------------------------------- 請求書の新規登録の画面


@pytest.mark.django_db
def test_create_form_shows_yellow_lump_section(client, user, customer, item):
    client.force_login(user)
    body = client.get(reverse("billing:invoice_create")).content.decode()
    assert LUMP_SECTION not in body

    line = _cancelled_lump(user, customer, item)
    body = client.get(reverse("billing:invoice_create")).content.decode()
    assert LUMP_SECTION in body
    assert "全部を出し終える出荷が無いまま終わった一式の受注の明細があります。" in body
    assert f"{line.order.order_no}-1" in body
    assert reverse("orders:order_detail", args=[line.order_id]) in body
    assert "受注を取消" in body
    assert "¥50,000" in body and "¥16,667" in body
    assert f'data-lump-row data-partner="{customer.pk}"' in body


@pytest.mark.django_db
def test_create_form_shows_reduced_lump(client, user, customer, item):
    _reduced_lump(user, customer, item)
    client.force_login(user)
    body = client.get(reverse("billing:invoice_create")).content.decode()
    assert "数量まで出荷 (全部を出し終えた出荷なし)" in body


@pytest.mark.django_db
def test_create_form_hides_lump_amounts_without_money(settings, client, user, customer, item):
    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    line = _cancelled_lump(user, customer, item)
    client.force_login(user)
    response = client.get(reverse("billing:invoice_create"))
    body = response.content.decode()
    # 請求書を作るのは金額に関わる操作なので、金額の閲覧の無い人には開いた時点で断る (本人の決め 11-12 07:2x。
    # 直す前は一式の金額を隠した節が開いた)。開けない画面に、一式の節と金額は出ない
    assert response.status_code == 403
    assert LUMP_SECTION not in body
    assert f"{line.order.order_no}-1" not in body
    assert "¥50,000" not in body and "¥16,667" not in body
    assert "<th class=\"text-right\">一式の金額</th>" not in body
