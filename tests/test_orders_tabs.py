"""受注の詳細のタブ (指示書・出荷・請求書) と、受注から先の仕事へ移る入口。Django の test client で HTML を見る。

タブの中身は Order の読むだけの property (workorders・shipments・invoices)。表は下流の部品の物で、orders は書かない。
作り物のデータは全部 services で作る (請求書は発行の納品書に入った出荷だけが入る = R09)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


def _order(user, customer, item, qty="10"):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )


def _workorder(user, line, process, qty="10"):
    return workorders_services.issue_workorder(
        line, Decimal(qty), [{"process": process, "planned_qty": Decimal(qty)}], user
    )


def _invoiced(user, customer, shipments):
    """出荷を発行の納品書に入れてから、1 枚の請求書にする。"""
    shipping_services.create_delivery_note(customer, shipments, user)
    return billing_services.create_invoice(customer, shipments, user, issued_on=ISSUED_ON)


def _full_order(user, customer, item, process):
    """指示書 1・出荷 2・請求書 1 の受注。"""
    order = _order(user, customer, item)
    line = order.lines.get()
    workorder = _workorder(user, line, process)
    first = shipping_services.ship(line, Decimal("3"), SHIPPED_AT, user)
    second = shipping_services.ship(line, Decimal("2"), SHIPPED_AT, user)
    invoice = _invoiced(user, customer, [first, second])
    return order, workorder, [first, second], invoice


def _detail(client, order):
    response = client.get(reverse("orders:order_detail", args=[order.pk]))
    assert response.status_code == 200
    return response


def _tabs(response):
    return {tab["label"]: tab for tab in response.context["tabs"]}


def _badge(label, count):
    """土台の詳細のタブの見出しと件数の badge の HTML。"""
    return f'aria-label="{label}">{label}<span class="badge badge-sm tabular-nums">{count}</span>'


# --- 1 段目: 受注の詳細のタブ ---


@pytest.mark.django_db
def test_detail_shows_three_tabs_with_counts(user, logged_in, customer, item, cut):
    order, workorder, shipments, invoice = _full_order(user, customer, item, cut)

    response = _detail(logged_in, order)
    body = response.content.decode()

    tabs = _tabs(response)
    assert tabs["指示書"]["count"] == 1
    assert tabs["出荷"]["count"] == 2
    assert tabs["請求書"]["count"] == 1
    assert _badge("指示書", 1) in body
    assert _badge("出荷", 2) in body
    assert _badge("請求書", 1) in body
    assert reverse("workorders:workorder_detail", args=[workorder.pk]) in body
    for shipment in shipments:
        assert reverse("shipping:shipment_detail", args=[shipment.pk]) in body
    assert reverse("billing:invoice_detail", args=[invoice.pk]) in body
    assert workorder.wo_no in body and invoice.invoice_no in body


@pytest.mark.django_db
def test_new_order_tabs_are_empty(user, logged_in, customer, item):
    order = _order(user, customer, item)

    response = _detail(logged_in, order)
    body = response.content.decode()

    tabs = _tabs(response)
    assert [(label, tabs[label]["count"]) for label in ("指示書", "出荷", "請求書")] == [
        ("指示書", 0),
        ("出荷", 0),
        ("請求書", 0),
    ]
    for label in ("指示書", "出荷", "請求書"):
        assert _badge(label, 0) in body
    assert body.count("まだありません。") == 3


@pytest.mark.django_db
def test_invoice_tab_counts_each_invoice_once(user, logged_in, customer, item, cut):
    order, _workorder_, shipments, invoice = _full_order(user, customer, item, cut)
    assert invoice.lines.count() == 2  # 同じ受注の出荷 2 つが 1 枚に入っている

    assert order.invoices.count() == 1
    assert list(order.invoices) == [invoice]
    assert _tabs(_detail(logged_in, order))["請求書"]["count"] == 1


@pytest.mark.django_db
def test_other_orders_rows_are_not_mixed(user, logged_in, customer, item, cut):
    order, workorder, shipments, invoice = _full_order(user, customer, item, cut)
    other, other_workorder, other_shipments, other_invoice = _full_order(user, customer, item, cut)

    tabs = _tabs(_detail(logged_in, order))
    assert [row["obj"] for row in tabs["指示書"]["rows"]] == [workorder]
    assert {row["obj"] for row in tabs["出荷"]["rows"]} == set(shipments)
    assert [row["obj"] for row in tabs["請求書"]["rows"]] == [invoice]
    assert set(order.shipments) == set(shipments)
    assert other_workorder not in order.workorders
    assert not set(other_shipments) & set(order.shipments)
    assert other_invoice not in order.invoices


@pytest.mark.django_db
def test_tabs_follow_the_order(user, logged_in, customer, item):
    order = _order(user, customer, item)

    response = _detail(logged_in, order)
    body = response.content.decode()

    assert [tab["label"] for tab in response.context["tabs"]] == ["指示書", "出荷", "請求書"]
    positions = [body.index(f'name="detail-tabs" aria-label="{label}"') for label in ("基本情報", "指示書", "出荷", "請求書")]
    assert positions == sorted(positions)
