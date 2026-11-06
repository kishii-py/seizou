"""受注の詳細の「流れ」の枠 (決め 11-04 22:0x。N131)。出来事の集め方 (orders.timeline.events_for) と、詳細の画面の枠。

作り物のデータは全部 services で作る。指示書と納品書の発行日は services が今日を書くので、日付は今日から数える。
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.billing import services as billing_services
from seizou.connect import services as connect_services
from seizou.core.templatetags.seizou_tags import comma
from seizou.masters import services as masters_services
from seizou.notes import services as notes_services
from seizou.orders import services as orders_services
from seizou.orders.timeline import events_for
from seizou.progress import services as progress_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services
from tests.test_r10_inbox_accept import _line, _payload

FLOW_KINDS = ["order", "workorder", "accepted", "progress", "shipment", "delivery_note", "invoice", "payment"]


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


def _order(user, customer, item, order_date):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=order_date,
    )


def _issue(user, line, process, qty="10"):
    return workorders_services.issue_workorder(
        line, Decimal(qty), [{"process": process, "planned_qty": Decimal(qty)}], user
    )


def _flowed(user, customer, item, process):
    """受注 → 指示書の発行と着手 → 実績 → 出荷 → 納品書 → 請求書 → 入金 の受注 (受注日は 3 日前、ほかは今日)。"""
    today = timezone.localdate()
    order = _order(user, customer, item, today - timedelta(days=3))
    line = order.lines.get()
    workorder = workorders_services.accept_workorder(_issue(user, line, process), user)
    progress_services.record_progress(workorder.steps.get(), Decimal("10"), timezone.now(), user)
    shipment = shipping_services.ship(line, Decimal("10"), today, user)
    note = shipping_services.create_delivery_note(customer, [shipment], user)
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=today)
    payment = billing_services.record_payment(invoice, today, invoice.total, user)
    return order, workorder, shipment, note, invoice, payment


def _detail(client, order):
    response = client.get(reverse("orders:order_detail", args=[order.pk]))
    assert response.status_code == 200
    return response, response.content.decode()


def _flow_section(body):
    start = body.index('<section id="order-flow"')
    return body[start : body.index("</section>", start)]


@pytest.mark.django_db
def test_order_alone_has_one_registered_event(user, customer, item):
    today = timezone.localdate()
    order = _order(user, customer, item, today)

    events = events_for(order)

    assert [(e.kind, e.title, e.when, e.url, e.line_no, e.amount) for e in events] == [
        ("order", "受注を登録", today, None, None, None)
    ]


@pytest.mark.django_db
def test_events_from_order_to_payment_line_up_in_order(user, customer, item, cut):
    order, workorder, shipment, note, invoice, payment = _flowed(user, customer, item, cut)

    events = events_for(order)

    assert [e.kind for e in events] == FLOW_KINDS
    assert [e.title for e in events] == [
        "受注を登録",
        f"指示書 {workorder.wo_no} を発行 (10 個)",
        f"指示書 {workorder.wo_no} に着手",
        f"実績 切断 10 個 ({workorder.wo_no})",
        f"出荷 {shipment.shipment_no} (10 個)",
        f"納品書 {note.note_no} を発行",
        f"請求書 {invoice.invoice_no} を発行",
        f"請求書 {invoice.invoice_no} に入金",
    ]
    assert [e.line_no for e in events] == [None, 1, 1, 1, 1, None, None, None]
    assert [e.has_time for e in events] == [False, False, True, True, False, False, False, False]
    workorder_url = reverse("workorders:workorder_detail", args=[workorder.pk])
    assert [e.url for e in events] == [
        None,
        workorder_url,
        workorder_url,
        workorder_url,
        reverse("shipping:shipment_detail", args=[shipment.pk]),
        reverse("shipping:deliverynote_detail", args=[note.pk]),
        reverse("billing:invoice_detail", args=[invoice.pk]),
        reverse("billing:payment_detail", args=[payment.pk]),
    ]
    assert [e.amount for e in events] == [None] * 6 + [invoice.total, payment.amount]


@pytest.mark.django_db
def test_earlier_day_comes_before_later_kind(user, customer, item, cut):
    today = timezone.localdate()
    order = _order(user, customer, item, today - timedelta(days=3))
    line = order.lines.get()
    shipping_services.ship(line, Decimal("4"), today - timedelta(days=2), user)
    _issue(user, line, cut, qty="6")

    assert [e.kind for e in events_for(order)] == ["order", "shipment", "workorder"]


@pytest.mark.django_db
def test_money_is_hidden_without_money_permission(user, logged_in, customer, item, cut, settings):
    order, workorder, shipment, note, invoice, payment = _flowed(user, customer, item, cut)

    shown = _flow_section(_detail(logged_in, order)[1])
    assert f"請求書 {invoice.invoice_no} を発行</a> <span class=\"tabular-nums\">(¥{comma(invoice.total)})</span>" in shown
    assert f"請求書 {invoice.invoice_no} に入金</a> <span class=\"tabular-nums\">(¥{comma(payment.amount)})</span>" in shown

    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}
    hidden = _flow_section(_detail(logged_in, order)[1])
    assert f"請求書 {invoice.invoice_no} を発行</a>" in hidden
    assert f"請求書 {invoice.invoice_no} に入金</a>" in hidden
    assert "(¥" not in hidden
    assert comma(invoice.total) not in hidden


@pytest.mark.django_db
def test_cancelled_order_ends_with_cancel_event(user, logged_in, customer, item):
    today = timezone.localdate()
    order = orders_services.cancel_order(_order(user, customer, item, today), user, reason="作り物の取消")

    events = events_for(order)

    assert [(e.kind, e.title, e.when) for e in events] == [
        ("order", "受注を登録", today),
        ("cancel", "受注を取消", None),
    ]
    section = _flow_section(_detail(logged_in, order)[1])
    assert section.index("受注を登録") < section.index("受注を取消")
    assert '<span class="font-mono tabular-nums">—</span>' in section


@pytest.mark.django_db
def test_inbox_and_note_events(user, customer, item):
    payload = _payload(str(uuid.uuid4()), [_line(1, "A-100")])
    payload["order_date"] = timezone.localdate().isoformat()
    inbox = connect_services.record_inbox(customer, payload, user, received_at=timezone.now() - timedelta(minutes=1))
    order = connect_services.accept(inbox, {"A-100": item}, user, send=False)
    notes_services.save_note(order, "<p>作り物の段取りの注意</p>", user)

    events = events_for(order)

    assert [(e.kind, e.title) for e in events] == [
        ("inbox", "注文が届いた"),
        ("inbox", "受信した注文を受け入れ"),
        ("order", "受注日 (相手の発注日)"),
        ("note", "メモを更新"),
    ]
    assert events[0].url == reverse("connect:inbox_detail", args=[inbox.pk])
    assert events[3].url is None


@pytest.mark.django_db
def test_registered_title_follows_buyer_order_date(user, customer, item):
    """受注日の点の字は、受注日が相手の発注日と同じときだけ「受注日 (相手の発注日)」(C08-2 = a)。

    受注日を編集で別の日に直すと「受注を登録」に戻り、日付は直した日。入口の添え字は変わらない。
    """
    payload = _payload(str(uuid.uuid4()), [_line(1, "A-100")])
    payload["order_date"] = "2026-11-03"
    inbox = connect_services.record_inbox(customer, payload, user)
    order = connect_services.accept(inbox, {"A-100": item}, user, send=False)

    before = [e for e in events_for(order) if e.kind == "order"]
    assert [(e.title, e.when) for e in before] == [("受注日 (相手の発注日)", date(2026, 11, 3))]

    orders_services.update_order(order, {"order_date": date(2026, 11, 5)}, [{"id": line.pk} for line in order.lines.all()], user)
    order.refresh_from_db()

    after = [e for e in events_for(order) if e.kind == "order"]
    assert [(e.title, e.when, e.via) for e in after] == [("受注を登録", date(2026, 11, 5), before[0].via)]


@pytest.mark.django_db
def test_registered_title_without_buyer_order_date(user, customer, item):
    """発注日の無い読み取りの行から作った受注は、受注日が受信の日でも「受注を登録」のまま。"""
    payload = _payload(str(uuid.uuid4()), [_line(1, "A-100")])
    payload["order_date"] = None
    inbox = connect_services.record_inbox(customer, payload, user, source="intake", sender_name="作り物の差出人")
    order = connect_services.accept(inbox, {"A-100": item}, user, send=False)

    registered = [e for e in events_for(order) if e.kind == "order"]
    assert [(e.title, e.when) for e in registered] == [("受注を登録", order.order_date)]


@pytest.mark.django_db
def test_detail_shows_flow_section_after_lines(user, logged_in, customer, item, cut):
    order, workorder, shipment, note, invoice, payment = _flowed(user, customer, item, cut)

    response, body = _detail(logged_in, order)

    assert "orders/_timeline.html" in [template.name for template in response.templates]
    assert '<h2 id="order-flow-title" class="text-lg font-bold">流れ</h2>' in body
    assert body.index('id="order-lines-title"') < body.index('id="order-flow-title"') < body.index('id="notes-heading"')
    section = _flow_section(body)
    assert section.count("data-flow-kind=") == len(FLOW_KINDS)
    workorder_url = reverse("workorders:workorder_detail", args=[workorder.pk])
    assert f'<a class="link link-primary" href="{workorder_url}">指示書 {workorder.wo_no} を発行 (10 個)</a>' in section
    assert "<span>明細 1</span>" in section
    assert "data-modal-form" not in section
    # 流れは枠で、関係する物のタブは今のまま
    assert [tab["label"] for tab in response.context["tabs"]] == ["指示書", "出荷", "請求書"]
