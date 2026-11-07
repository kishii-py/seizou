"""一式の行を載せた後に出荷が続いた明細を、請求書の新規登録の黄の表に出す (作者の決め C107-7。billing.services.lumps_shipped_after)。

人が金額を決めて一式の行を載せた後に受注の数量を増やして出荷を続けると、後の出荷は 0 円 (明細を出し終えた出荷の印が付かない) なので、
黙って請求から落ちないよう「一式の行 IV-… の後に出荷あり」の行で知らせる (読むだけ・止めない)。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

OCT_10 = date(2026, 10, 10)
OCT_31 = date(2026, 10, 31)
NOV_05 = date(2026, 11, 5)


def _customer(user, code="C001"):
    return masters_services.create_partner({"code": code, "name": f"作り物の得意先 {code}", "role": "customer"}, user)


@pytest.fixture
def customer(user):
    return _customer(user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _billed_then_shipped_after(user, customer, item, *, ship_after=True):
    """一式 (300・50000) を 100 出荷・納品書を発行・数量を 100 に直す → 一式の行 16667 の請求書 → 数量を 300 に戻して 200 を出荷。"""
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("300"), "unit_price": Decimal("50000"), "price_basis": "lump"}],
        user,
        order_date=date(2026, 10, 1),
    )
    line = order.lines.get()
    shipped(user, customer, line, "100", OCT_10)
    orders_services.update_order(order, {}, [{"id": line.pk, "qty": Decimal("100")}], user)
    line.refresh_from_db()
    invoice = services.create_invoice(customer, [], user, issued_on=OCT_31, lumps=[(line, Decimal("16667"))])
    orders_services.update_order(order, {}, [{"id": line.pk, "qty": Decimal("300")}], user)
    if ship_after:
        shipped(user, customer, line, "200", NOV_05)
    line.refresh_from_db()
    return line, invoice


@pytest.mark.django_db
def test_shipped_after_lump_line_is_listed(client, user, customer, item):
    line, invoice = _billed_then_shipped_after(user, customer, item)

    [(found_line, billed)] = services.lumps_shipped_after()
    assert found_line == line
    assert billed.invoice_id == invoice.pk and billed.shipment_id is None
    assert services.unbilled_lumps() == []

    client.force_login(user)
    body = client.get(reverse("billing:invoice_create")).content.decode()
    assert "一式の受注の明細で、金額がまだ請求に載っていない物があります。" in body
    assert "一式の行" in body and "の後に出荷あり" in body
    assert f"{invoice.invoice_no} を取り消すと、金額を決め直して載せられます。" in body
    assert f"lump-{line.pk}-amount" not in body
    assert reverse("orders:orderline_no_bill", args=[line.pk]) not in body


@pytest.mark.django_db
def test_no_later_shipment_is_not_listed(user, customer, item):
    _billed_then_shipped_after(user, customer, item, ship_after=False)

    assert services.lumps_shipped_after() == []


@pytest.mark.django_db
def test_marked_line_is_not_listed(user, customer, item):
    line, _invoice = _billed_then_shipped_after(user, customer, item)

    orders_services.mark_no_bill(line, user)

    assert services.lumps_shipped_after() == []


@pytest.mark.django_db
def test_cancelled_lump_invoice_returns_to_unbilled(user, customer, item):
    line, invoice = _billed_then_shipped_after(user, customer, item)

    services.cancel_invoice(invoice, user)

    assert services.lumps_shipped_after() == []
    assert services.unbilled_lumps() == [line]


@pytest.mark.django_db
def test_customer_filter(user, customer, item):
    line, _invoice = _billed_then_shipped_after(user, customer, item)
    other = _customer(user, code="C002")

    assert [found for found, _billed in services.lumps_shipped_after(customer)] == [line]
    assert services.lumps_shipped_after(other) == []
