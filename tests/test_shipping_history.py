"""出荷と納品書の変更の履歴 (決め HIST-1 の伝票の「出荷」「納品書」。C106 の その 6)。

出荷の書く関数 (ship・ship_lines・update_shipment・create_delivery_note・cancel_delivery_note) が、出荷と納品書の詳細の「履歴」に
作った・直した・取り消したを残すこと、何も変えなければ増えないこと、出荷の知らせの列 (番号・送った日時・相手の返事) を残さないこと、
詳細の問い合わせの数が履歴の行の数で増えないことを見る。データは作り物だけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.core.exceptions import RuleViolation
from seizou.core.models import ChangeLog
from seizou.core.services import record_history
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

DAY = date(2026, 11, 3)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def line(user, customer):
    item = masters_services.create_item({"part_no": "H-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}], user, order_date=date(2026, 11, 2)
    )
    return order.lines.get()


def _rows(obj):
    return list(ChangeLog.objects.filter(owner_model=obj._meta.label_lower, owner_pk=obj.pk))


def _history_part(client, url_name, obj):
    body = client.get(reverse(url_name, args=[obj.pk])).content.decode()
    start = body.index('aria-label="履歴"')
    return body[start : body.index("data-last-change", start)]


@pytest.mark.django_db
def test_ship_records_created_on_shipment(user, line):
    shipment = shipping_services.ship(line, Decimal("4"), DAY, user, send=False)

    rows = _rows(shipment)
    assert [(row.model, row.action) for row in rows] == [("shipping.shipment", "created")]
    fields = {change["field"] for change in rows[0].changes}
    assert {"shipment_no", "order_line", "qty", "shipped_at"} <= fields
    assert not fields & {"notice_message_id", "notice_sent_at", "notice_response"}


@pytest.mark.django_db
def test_ship_lines_records_each_shipment(user, line):
    shipments = shipping_services.ship_lines([(line, Decimal("2"))], DAY, user)

    assert [row.action for row in _rows(shipments[0])] == ["created"]


@pytest.mark.django_db
def test_update_shipment_before_after(logged_in, user, line):
    shipment = shipping_services.ship(line, Decimal("4"), DAY, user, send=False)
    count = ChangeLog.objects.count()

    shipping_services.update_shipment(shipment, Decimal("5"), DAY, user, note="")

    row = _rows(shipment)[0]
    assert row.action == "updated"
    assert row.changes == [{"field": "qty", "label": "数量", "type": "number", "before": "4", "after": "5"}]
    assert ChangeLog.objects.count() == count + 1  # 受注の状態は変わらない (一部出荷のまま)
    part = _history_part(logged_in, "shipping:shipment_detail", shipment)
    assert "数量: 4 → 5" in part

    count = ChangeLog.objects.count()
    shipping_services.update_shipment(shipment, Decimal("5"), DAY, user, note="")  # 何も変えない
    assert ChangeLog.objects.count() == count


@pytest.mark.django_db
def test_delivery_note_created_and_cancelled(logged_in, user, customer, line):
    shipment = shipping_services.ship(line, Decimal("4"), DAY, user, send=False)

    note = shipping_services.create_delivery_note(customer, [shipment], user, issued_on=DAY)

    assert [row.action for row in _rows(note)] == ["created"]
    shipment_row = _rows(shipment)[0]
    assert shipment_row.changes == [
        {"field": "delivery_note", "label": "納品書", "type": "text", "before": "", "after": str(note)}
    ]
    assert f"納品書: — → {note}" in _history_part(logged_in, "shipping:shipment_detail", shipment)

    shipping_services.cancel_delivery_note(note, user)

    row = _rows(note)[0]
    assert row.action == "cancelled"
    assert row.changes == [{"field": "status", "label": "状態", "type": "text", "before": "発行", "after": "取消"}]
    part = _history_part(logged_in, "shipping:deliverynote_detail", note)
    assert "取り消した" in part and "状態: 発行 → 取消" in part


@pytest.mark.django_db
def test_refused_update_leaves_no_history(user, customer, line):
    """発行の納品書に入った出荷を直そうとして止まったら、履歴も残らない (同じ atomic)。"""
    shipment = shipping_services.ship(line, Decimal("4"), DAY, user, send=False)
    shipping_services.create_delivery_note(customer, [shipment], user, issued_on=DAY)
    count = ChangeLog.objects.count()

    with pytest.raises(RuleViolation, match="R08|納品書"):
        shipping_services.update_shipment(shipment, Decimal("5"), DAY, user, note="")

    assert ChangeLog.objects.count() == count


def _queries(client, url) -> int:
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200
    return len(ctx.captured_queries)


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["shipping:shipment_detail", "shipping:deliverynote_detail"])
def test_detail_queries_do_not_grow_with_history_rows(logged_in, user, customer, line, url_name):
    few_shipment = shipping_services.ship(line, Decimal("1"), DAY, user, send=False)
    many_shipment = shipping_services.ship(line, Decimal("1"), DAY, user, send=False)
    few_note = shipping_services.create_delivery_note(customer, [few_shipment], user, issued_on=DAY)
    many_note = shipping_services.create_delivery_note(customer, [many_shipment], user, issued_on=DAY)
    few, many = (few_shipment, many_shipment) if url_name == "shipping:shipment_detail" else (few_note, many_note)
    for n in range(10):  # 状態を変えずに、履歴の行だけを増やす
        record_history(many, user, changes=[{"field": "note", "label": "備考", "type": "text", "before": str(n), "after": str(n + 1)}])
    assert len(_rows(many)) > len(_rows(few)) + 5

    few_url = reverse(url_name, args=[few.pk])
    many_url = reverse(url_name, args=[many.pk])
    for url in (few_url, many_url):  # 最初の 1 回の控えを外す
        assert logged_in.get(url).status_code == 200

    assert _queries(logged_in, many_url) == _queries(logged_in, few_url)
