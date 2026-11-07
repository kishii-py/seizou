"""受注の明細の「請求しない」の印 (作者の決め C107-3) の付いた明細の出荷を、請求の候補から外す。

外す所: billable_shipments (請求書の新規登録の出荷の表と得意先の候補・ダッシュボードの「請求書に入っていない出荷」・左のメニューの数)・
unbilled_in_period (請求書を作った後の黄の知らせ)・unbilled_lumps (一式の表)・納品書の詳細の緑の「請求書作成」(has_unbilled_shipments)。
出荷の数と出荷の金額 (ダッシュボードの月次と出荷の推移) と、発行した請求書は変えない。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services
from seizou.core import dashboard
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from tests.billing_helpers import shipped

OCT_10 = date(2026, 10, 10)
OCT_31 = date(2026, 10, 31)
TODAY = date(2026, 11, 7)
#: 金額の閲覧だけを効かせる (tests/test_core_line_totals.py と同じ)
NO_MONEY = {"department": "off", "write": "off", "money": "on"}


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


def _cancelled_lump(user, customer, item):
    """一式 (300・50000) を 100 出荷して納品書を発行し、受注を取り消した明細と、その出荷。"""
    line = _line(user, customer, item)
    shipment = shipped(user, customer, line, "100", OCT_10)
    orders_services.cancel_order(line.order, user)
    line.refresh_from_db()
    return line, shipment


@pytest.mark.django_db
def test_marked_line_shipments_leave_billable(client, user, customer, item):
    lump, lump_shipment = _cancelled_lump(user, customer, item)
    unit = _line(user, customer, item, price_basis="unit", qty="10", price="100")
    unit_shipment = shipped(user, customer, unit, "4", OCT_10)
    before = dashboard._shipments_to_invoice().count()

    orders_services.mark_no_bill(lump, user)

    assert list(services.billable_shipments()) == [unit_shipment]
    assert dashboard._shipments_to_invoice().count() == before - 1
    client.force_login(user)
    body = client.get(reverse("billing:invoice_create")).content.decode()
    assert lump_shipment.shipment_no not in body
    assert unit_shipment.shipment_no in body


@pytest.mark.django_db
def test_marked_line_shipments_leave_period_note(user, customer, item):
    lump, lump_shipment = _cancelled_lump(user, customer, item)
    unit = _line(user, customer, item, price_basis="unit", qty="10", price="100")
    unit_shipment = shipped(user, customer, unit, "4", OCT_10)
    orders_services.mark_no_bill(lump, user)

    assert lump_shipment not in services.unbilled_in_period(customer, date(2026, 10, 1), OCT_31)
    invoice = services.create_invoice(customer, [unit_shipment], user, issued_on=OCT_31)
    assert "全部請求書に入っています" in invoice.saved_note


@pytest.mark.django_db
def test_note_with_only_marked_shipments_has_no_green_button(user, customer, item):
    lump, lump_shipment = _cancelled_lump(user, customer, item)
    note = lump_shipment.delivery_note
    assert note.has_unbilled_shipments is True

    orders_services.mark_no_bill(lump, user)

    note.refresh_from_db()
    note = type(note).objects.get(pk=note.pk)
    assert note.has_unbilled_shipments is False

    # 印の無い出荷が 1 つでもあれば出る
    unit = _line(user, customer, item, price_basis="unit", qty="10", price="100")
    other = shipping_services.ship(unit, Decimal("4"), OCT_10, user)
    mixed = shipping_services.create_delivery_note(customer, [other], user)
    assert mixed.has_unbilled_shipments is True


@pytest.mark.django_db
def test_mark_keeps_shipment_amounts(user, customer, item):
    lump = _line(user, customer, item)
    shipped(user, customer, lump, "300", date(2026, 11, 2))
    before_monthly = {row["key"]: row["shipment_amount"] for row in dashboard.monthly_trend(today=TODAY)["months"]}
    before_trend = {row["key"]: row["shipment_amount"] for row in dashboard.shipping_trend(today=TODAY)["months"]}

    orders_services.mark_no_bill(lump, user)

    after_monthly = {row["key"]: row["shipment_amount"] for row in dashboard.monthly_trend(today=TODAY)["months"]}
    after_trend = {row["key"]: row["shipment_amount"] for row in dashboard.shipping_trend(today=TODAY)["months"]}
    assert after_monthly == before_monthly and after_trend == before_trend
    assert after_monthly["2026-11"] == Decimal("50000")


@pytest.mark.django_db
def test_marked_lump_leaves_lump_table_and_link_is_shown(settings, client, user, customer, item):
    lump, _shipment = _cancelled_lump(user, customer, item)
    client.force_login(user)
    url = reverse("orders:orderline_no_bill", args=[lump.pk])

    body = client.get(reverse("billing:invoice_create")).content.decode()
    assert f'href="{url}?next=' in body
    assert f'data-modal-form="{url}" data-row-drop' in body

    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    body = client.get(reverse("billing:invoice_create")).content.decode()
    assert url not in body
    settings.SEIZOU = {}

    orders_services.mark_no_bill(lump, user)
    assert services.unbilled_lumps() == []


@pytest.mark.django_db
def test_marked_lump_cannot_be_put_on_invoice(user, customer, item):
    lump, _shipment = _cancelled_lump(user, customer, item)
    orders_services.mark_no_bill(lump, user)
    lump.refresh_from_db()

    with pytest.raises(ValueError) as caught:
        services.create_invoice(customer, [], user, issued_on=OCT_31, lumps=[(lump, Decimal("16667"))])

    assert str(caught.value) == (
        f"発行できません。受注 {lump.order.order_no} の 1 行目には「請求しない」の印が付いているので、外してください。"
    )
    assert not services.billable_shipments().exists()


@pytest.mark.django_db
def test_issued_invoice_keeps_lines_after_mark(user, customer, item):
    lump = _line(user, customer, item)
    shipment = shipped(user, customer, lump, "100", OCT_10)
    invoice = services.create_invoice(customer, [shipment], user, issued_on=OCT_31)
    before = [(row.shipment_id, row.amount) for row in invoice.lines.all()]

    orders_services.mark_no_bill(lump, user)

    invoice.refresh_from_db()
    assert [(row.shipment_id, row.amount) for row in invoice.lines.all()] == before
