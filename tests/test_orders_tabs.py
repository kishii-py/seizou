"""受注の詳細のタブ (指示書・出荷・請求書) と、受注から先の仕事へ移る入口。Django の test client で HTML を見る。

タブの中身は Order の読むだけの property (workorders・shipments・invoices)。表は下流の部品の物で、orders は書かない。
作り物のデータは全部 services で作る (請求書は発行の納品書に入った出荷だけが入る = R09)。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.core.templatetags.seizou_tags import comma
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine
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
    for label in ("指示書", "出荷", "請求書"):
        assert f"{label}が入ると、ここに並びます。" in body


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


# --- 2 段目: 明細の行の入口 (受注の詳細と注文残の一覧) ---


def _entrances(line):
    """明細 1 行の入口 2 つの href (? の後は鍵が 1 つなので &amp; は出ない)。"""
    return (
        f'href="{reverse("shipping:shipment_create")}?order_line={line.pk}"',
        f'href="{reverse("workorders:workorder_create")}?order_line={line.pk}"',
    )


def _tr(body, pk):
    """一覧の行 (<tr id="row-<pk>" ...>...</tr>) の HTML。"""
    match = re.search(rf'<tr id="row-{pk}".*?</tr>', body, re.S)
    assert match, f"row-{pk} が無い"
    return match.group(0)


def _cells(tr_html):
    """行の <td> の字 (タグを外して前後の空白を落とす)。"""
    return [re.sub(r"<[^>]+>", "", cell).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", tr_html, re.S)]


@pytest.mark.django_db
def test_detail_line_has_two_entrances(user, logged_in, customer, item):
    order = _order(user, customer, item)
    line = order.lines.get()
    shipping_services.ship(line, Decimal("4"), SHIPPED_AT, user)

    body = _detail(logged_in, order).content.decode()

    for href in _entrances(line):
        assert href in body
    assert "出荷する" in body and "指示書を発行する" in body
    assert '<th class="whitespace-nowrap font-medium">操作</th>' in body


@pytest.mark.django_db
def test_no_entrance_for_cancelled_order_or_finished_line(user, logged_in, customer, item):
    done = _order(user, customer, item, qty="2")
    done_line = done.lines.get()
    shipping_services.ship(done_line, Decimal("2"), SHIPPED_AT, user)
    cancelled = _order(user, customer, item)
    orders_services.cancel_order(cancelled, user)

    for order, line in ((done, done_line), (cancelled, cancelled.lines.get())):
        body = _detail(logged_in, order).content.decode()
        for href in _entrances(line):
            assert href not in body
        assert f"?order_line={line.pk}" not in body


@pytest.mark.django_db
def test_remaining_stays_last_cell(user, logged_in, customer, item):
    order = orders_services.create_order(
        customer,
        [
            {"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")},
            {"item": item, "qty": Decimal("5"), "unit_price": Decimal("100")},
        ],
        user,
        order_date=ORDER_DATE,
    )
    first = order.lines.get(line_no=1)
    shipping_services.ship(first, Decimal("4"), SHIPPED_AT, user)

    body = _detail(logged_in, order).content.decode()

    # E2E (E-12) と同じ読み方: 明細の見出しの後の表の、各行の最後の td が「残」。
    section = body[body.index('id="order-lines-title"') :]
    tbody = re.search(r"<tbody>(.*?)</tbody>", section, re.S).group(1)
    rows = re.findall(r"<tr[^>]*>.*?</tr>", tbody, re.S)
    lines = list(orders_services.annotate_remaining(OrderLine.objects.filter(order=order)).order_by("line_no"))
    assert len(rows) == len(lines) == 2
    for tr_html, line in zip(rows, lines, strict=True):
        assert _cells(tr_html)[-1] == str(comma(line.remaining_total))
    assert [line.remaining_total for line in lines] == [Decimal("6"), Decimal("5")]


# --- 2 段目: 受注の一覧の状態の色と、注文残の一覧の入口 ---

BADGES = {
    "open": '<span class="badge badge-soft badge-primary">受注</span>',
    "partial": '<span class="badge badge-soft badge-warning">一部出荷</span>',
    "shipped": '<span class="badge badge-soft badge-success">出荷済</span>',
    "cancelled": '<span class="badge badge-ghost text-base-content/50">取消</span>',
}


def _four_orders(user, customer, item):
    """状態の違う受注 4 件 (受注・一部出荷・出荷済・取消)。"""
    open_order = _order(user, customer, item)
    partial = _order(user, customer, item)
    shipping_services.ship(partial.lines.get(), Decimal("4"), SHIPPED_AT, user)
    shipped = _order(user, customer, item, qty="2")
    shipping_services.ship(shipped.lines.get(), Decimal("2"), SHIPPED_AT, user)
    cancelled = _order(user, customer, item)
    orders_services.cancel_order(cancelled, user)
    orders = {"open": open_order, "partial": partial, "shipped": shipped, "cancelled": cancelled}
    for status, order in orders.items():
        order.refresh_from_db()
        assert order.status == status
    return orders


@pytest.mark.django_db
def test_order_list_status_badges(user, logged_in, customer, item):
    orders = _four_orders(user, customer, item)

    body = logged_in.get(reverse("orders:order_list"), {"all": "1"}).content.decode()

    for status, order in orders.items():
        tr_html = _tr(body, order.pk)
        assert BADGES[status] in tr_html
        # E2E (E-08) と同じ読み方: 行の最初の td が受注番号。列の並びは変えない。
        cells = _cells(tr_html)
        assert cells[0] == order.order_no
        assert cells[4] == order.get_status_display()


@pytest.mark.django_db
def test_order_list_partial_has_badges(user, logged_in, customer, item):
    _four_orders(user, customer, item)

    response = logged_in.get(reverse("orders:order_list"), {"all": "1"}, HTTP_HX_REQUEST="true")
    partial = response.content.decode()

    assert "<html" not in partial
    for badge in BADGES.values():
        assert badge in partial
    assert '<div id="list-meta"' in partial and 'hx-swap-oob="true"' in partial


@pytest.mark.django_db
def test_orderline_list_rows_have_entrances(user, logged_in, customer, item):
    orders = _four_orders(user, customer, item)
    open_line = orders["open"].lines.get()
    partial_line = orders["partial"].lines.get()

    default = logged_in.get(reverse("orders:orderline_list")).content.decode()
    assert '<th class="whitespace-nowrap font-medium">操作</th>' in default
    for line in (open_line, partial_line):
        for href in _entrances(line):
            assert href in _tr(default, line.pk)

    everything = logged_in.get(reverse("orders:orderline_list"), {"all": "1"}).content.decode()
    for status in ("shipped", "cancelled"):
        line = orders[status].lines.get()
        tr_html = _tr(everything, line.pk)
        assert "?order_line=" not in tr_html
        assert _cells(tr_html)[-1] == "—"
    for href in _entrances(open_line):
        assert href in everything


@pytest.mark.django_db
def test_list_card_keeps_ids(user, logged_in, customer, item):
    _order(user, customer, item)

    for name in ("orders:order_list", "orders:orderline_list"):
        body = logged_in.get(reverse(name)).content.decode()
        for element_id in ('id="list-q"', 'id="list-per"', 'id="rows"', 'id="list-meta"'):
            assert body.count(element_id) == 1, (name, element_id)
        assert 'id="row-' in body


@pytest.mark.django_db
def test_empty_search_uses_core_empty_state(user, logged_in, customer, item):
    _order(user, customer, item)

    for name in ("orders:order_list", "orders:orderline_list"):
        full = logged_in.get(reverse(name), {"q": "合わない字"}).content.decode()
        assert "「合わない字」に合う物はありません。" in full
        partial = logged_in.get(reverse(name), {"q": "合わない字"}, HTTP_HX_REQUEST="true").content.decode()
        assert "「合わない字」に合う物はありません。" in partial
        assert '<div id="list-meta"' in partial


@pytest.mark.django_db
def test_order_list_csv_unchanged(user, logged_in, customer, item):
    _four_orders(user, customer, item)

    orders_csv = logged_in.get(reverse("orders:order_list"), {"all": "1", "format": "csv"}).content.decode()
    header, *rows = orders_csv.lstrip("﻿").splitlines()
    assert header == "受注番号,得意先,受注日,得意先の注文番号,状態,入口"
    assert len(rows) == 4
    assert "操作" not in orders_csv

    lines_csv = logged_in.get(reverse("orders:orderline_list"), {"format": "csv"}).content.decode()
    assert lines_csv.lstrip("﻿").splitlines()[0] == "受注番号,得意先,部品,納期,納期まで,数量,出荷済み,残,指示書,進捗,計画の終了日,判断,受注日,状態"
    assert "操作" not in lines_csv
