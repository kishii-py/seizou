"""受注と明細の変更の履歴 (決め HIST-1 の伝票の「受注」。C106 の その 4)。

受注の書く関数 (create_order・update_order・cancel_order・refresh_status・mark_no_bill・clear_no_bill) が、受注の詳細の「履歴」に
作った・直した・消した・取り消したを残すこと、何も変えなければ増えないこと、流れの枠の出来事が変わらないこと、詳細の問い合わせの数が
履歴の行の数で増えないことを見る。データは作り物だけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.timeline import events_for
from seizou.shipping import services as shipping_services


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "H-100", "name": "作り物の軸", "unit": "個"}, user)


def _order(user, customer, item, *, lines=3):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")} for _ in range(lines)],
        user,
        order_date=date(2026, 11, 2),
    )


def _rows(order):
    return list(ChangeLog.objects.filter(owner_model="orders.order", owner_pk=order.pk))


def _history_part(client, order):
    body = client.get(reverse("orders:order_detail", args=[order.pk])).content.decode()
    start = body.index('aria-label="履歴"')
    return body[start : body.index("data-last-change", start)]


def _keep(order):
    """update_order に渡す、今の明細を全部残す並び。"""
    return [{"id": line.pk} for line in order.lines.order_by("line_no")]


@pytest.mark.django_db
def test_create_order_records_order_and_lines(user, customer, item):
    order = _order(user, customer, item)

    rows = _rows(order)
    assert len(rows) == 4
    assert {row.action for row in rows} == {"created"}
    assert sorted(row.model for row in rows) == ["orders.order"] + ["orders.orderline"] * 3
    own = next(row for row in rows if row.model == "orders.order")
    assert {"field": "order_no", "label": "受注番号", "type": "text", "before": "", "after": order.order_no} in own.changes


@pytest.mark.django_db
def test_update_order_changed_and_removed_lines(logged_in, user, customer, item):
    order = _order(user, customer, item)
    first, second, third = order.lines.order_by("line_no")
    second_text, third_text = str(second), str(third)
    count = ChangeLog.objects.count()

    orders_services.update_order(order, {}, [{"id": first.pk}, {"id": second.pk, "qty": Decimal("12")}], user)

    new = ChangeLog.objects.order_by("pk")[count:]
    assert [(row.model, row.action, row.object_text) for row in new] == [
        ("orders.orderline", "deleted", third_text),
        ("orders.orderline", "updated", second_text),
    ]
    assert all(row.owner_model == "orders.order" and row.owner_pk == order.pk for row in new)
    changed = new[1].changes
    assert {"field": "qty", "label": "数量", "type": "number", "before": "10", "after": "12"} in changed
    assert {"field": "amount", "label": "金額", "type": "money", "before": "1000", "after": "1200"} in changed

    part = _history_part(logged_in, order)
    assert f"{second_text} を直した" in part
    assert "数量: 10 → 12" in part
    assert f"{third_text} を消した" in part


@pytest.mark.django_db
def test_update_order_without_change_adds_nothing(user, customer, item):
    order = _order(user, customer, item)
    count = ChangeLog.objects.count()

    orders_services.update_order(order, {"note": ""}, _keep(order), user)

    assert ChangeLog.objects.count() == count


@pytest.mark.django_db
def test_update_order_header_and_added_line(user, customer, item):
    order = _order(user, customer, item, lines=1)
    count = ChangeLog.objects.count()

    orders_services.update_order(
        order,
        {"customer_order_no": "PO-9"},
        _keep(order) + [{"item": item, "qty": Decimal("2"), "unit_price": Decimal("50")}],
        user,
    )

    new = list(ChangeLog.objects.order_by("pk")[count:])
    assert [(row.model, row.action) for row in new] == [("orders.order", "updated"), ("orders.orderline", "created")]
    assert new[0].changes == [
        {"field": "customer_order_no", "label": "得意先の注文番号", "type": "text", "before": "", "after": "PO-9"}
    ]


@pytest.mark.django_db
def test_removed_line_with_shipment_leaves_no_history(user, customer, item):
    """出荷のある明細を消そうとして止まったら、同じ atomic が戻るので「消した」も残らない。"""
    order = _order(user, customer, item, lines=2)
    first, second = order.lines.order_by("line_no")
    shipping_services.ship(second, Decimal("1"), timezone.now(), user, send=False)
    count = ChangeLog.objects.count()

    with pytest.raises(ValueError):
        orders_services.update_order(order, {}, [{"id": first.pk}], user)

    assert ChangeLog.objects.count() == count


@pytest.mark.django_db
def test_cancel_order_records_cancelled(logged_in, user, customer, item):
    order = _order(user, customer, item, lines=1)

    orders_services.cancel_order(order, user, reason="作り物の理由")

    row = _rows(order)[0]
    assert row.action == "cancelled" and row.model == "orders.order"
    assert {"field": "status", "label": "状態", "type": "text", "before": "未出荷", "after": "取消"} in row.changes
    part = _history_part(logged_in, order)
    assert "取り消した" in part
    assert "状態: 未出荷 → 取消" in part


@pytest.mark.django_db
def test_refresh_status_records_only_when_status_changes(user, customer, item):
    order = _order(user, customer, item, lines=1)
    line = order.lines.get()

    shipping_services.ship(line, Decimal("10"), timezone.now(), user, send=False)

    rows = [row for row in _rows(order) if row.model == "orders.order" and row.action == "updated"]
    assert len(rows) == 1
    assert rows[0].changes == [
        {"field": "status", "label": "状態", "type": "text", "before": "未出荷", "after": "出荷済"}
    ]
    count = ChangeLog.objects.count()
    orders_services.refresh_status(order, user)  # 状態は変わらない
    assert ChangeLog.objects.count() == count


@pytest.mark.django_db
def test_no_bill_mark_and_clear(user, customer, item):
    order = _order(user, customer, item, lines=1)
    line = order.lines.get()

    orders_services.mark_no_bill(line, user)
    orders_services.mark_no_bill(line, user)  # もう付いている = 増えない
    orders_services.clear_no_bill(line, user)

    rows = [row for row in _rows(order) if row.action == "updated"]
    assert [row.changes for row in rows] == [
        [{"field": "no_bill", "label": "請求しない", "type": "bool", "before": "true", "after": "false"}],
        [{"field": "no_bill", "label": "請求しない", "type": "bool", "before": "false", "after": "true"}],
    ]


@pytest.mark.django_db
def test_flow_events_do_not_change(user, customer, item):
    """流れの枠 (orders.timeline) の出来事は、履歴の行が増えても変わらない (決め HIST-1「流れは残す」)。"""
    order = _order(user, customer, item, lines=2)
    before = [(event.kind, event.title) for event in events_for(order)]
    first, second = order.lines.order_by("line_no")

    orders_services.update_order(order, {}, [{"id": first.pk, "qty": Decimal("11")}, {"id": second.pk}], user)

    assert [(event.kind, event.title) for event in events_for(order)] == before


@pytest.mark.django_db
def test_detail_has_history_tab_and_last_change(logged_in, user, customer, item):
    order = _order(user, customer, item, lines=1)

    body = logged_in.get(reverse("orders:order_detail", args=[order.pk])).content.decode()

    assert 'aria-label="履歴"' in body
    assert "data-last-change" in body
    assert "最後に直した" in body and "tester" in body


def _queries(client, url) -> int:
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200
    return len(ctx.captured_queries)


@pytest.mark.django_db
def test_detail_queries_do_not_grow_with_history_rows(logged_in, user, customer, item):
    few = _order(user, customer, item, lines=1)
    many = _order(user, customer, item, lines=1)
    for qty in range(11, 21):
        orders_services.update_order(many, {}, [{"id": many.lines.get().pk, "qty": Decimal(qty)}], user)
    assert len(_rows(many)) > len(_rows(few))

    few_url = reverse("orders:order_detail", args=[few.pk])
    many_url = reverse("orders:order_detail", args=[many.pk])
    for url in (few_url, many_url):  # 最初の 1 回の控えを外す
        assert logged_in.get(url).status_code == 200

    assert _queries(logged_in, many_url) == _queries(logged_in, few_url)
