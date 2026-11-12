"""受注の詳細の問い合わせの数が、明細の数で増えない事を見る (依頼書 N432)。

訳: 受注の詳細は、小窓で保存するたびに seizou:refresh で取り直す。段の帯 (stage_band)・流れの枠 (events_for)・明細の表の
行のメニューと「検収」の枡が、明細ごとに指示書・工程・実績・出荷・納品書・請求書の明細・請求書・入金・検収を問い合わせていたので、
明細が多い受注では開くたびに問い合わせが明細の数だけ増えた (作り物の A 社の種で、明細 1 行につき 13〜17 回)。
明細を読む時に、辿る物を関係ごとに 1 回でまとめて読む形にした (orders.timeline.detail_lookups)。出る字と並びは変えない。
作り物の得意先と部品だけを使い、データは services で作る。相手への HTTP は偽物 (呼ばれたら落とす) にする。
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from itertools import zip_longest

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.billing import services as billing_services
from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders import timeline
from seizou.orders.models import Order, OrderLine
from seizou.orders.views import OrderDetailView
from seizou.progress import services as progress_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services
from tests.test_orders_acceptance import _accept


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_superuser(username="tester", password="tests-only")


@pytest.fixture
def processes(user):
    return [
        masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user),
        masters_services.create_process({"code": "BEND", "name": "曲げ", "sort_order": 2}, user),
    ]


def _customer(user, code):
    """得意先は受注ごとに分ける (同じ得意先の同じ締め期間の 2 枚目の請求書は R23 で止まるため)。"""
    return masters_services.create_partner({"code": code, "name": f"作り物の得意先 {code}", "role": "customer"}, user)


def _items(user, count):
    return [
        masters_services.create_item({"part_no": f"Q-{uuid.uuid4().hex[:8]}", "name": "作り物の軸", "unit": "個"}, user)
        for _ in range(count)
    ]


def _flowed_order(user, customer, processes, lines):
    """明細が lines 行の受注。どの明細にも、発行した指示書 1 つ (工程 2 つ・実績 1 件)・取り消した指示書 1 つ・出荷 2 つを付ける。

    出荷の 1 つは明細ごとの納品書に入れ、全部の明細のその出荷を 1 枚の請求書に入れて入金を記録する。もう 1 つは納品書に入れない。
    """
    today = timezone.localdate()
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")} for item in _items(user, lines)],
        user,
        order_date=today - timedelta(days=3),
    )
    billed = []
    for line in order.lines.order_by("line_no"):
        workorder = workorders_services.issue_workorder(
            line, Decimal("10"), [{"process": process, "planned_qty": Decimal("10")} for process in processes], user
        )
        progress_services.record_progress(workorder.steps.order_by("seq").first(), Decimal("4"), timezone.now(), user)
        spare = workorders_services.issue_workorder(
            line, Decimal("5"), [{"process": processes[0], "planned_qty": Decimal("5")}], user
        )
        workorders_services.cancel_workorder(spare, user)
        shipment = shipping_services.ship(line, Decimal("4"), today, user)
        shipping_services.create_delivery_note(customer, [shipment], user)
        shipment.refresh_from_db()
        billed.append(shipment)
        shipping_services.ship(line, Decimal("3"), today, user)
    invoice = billing_services.create_invoice(customer, billed, user, issued_on=today)
    billing_services.record_payment(invoice, today, invoice.total, user)
    return order


def _flow_section(body):
    start = body.index('<section id="order-flow"')
    return body[start : body.index("</section>", start)]


def _detail_queries(client, order) -> int:
    url = reverse("orders:order_detail", args=[order.pk])
    assert client.get(url).status_code == 200  # 最初の 1 回の控え (session など) を外す
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    assert response.status_code == 200
    flow = _flow_section(response.content.decode())
    shipments = [s for line in order.lines.all() for s in line.shipments.all()]
    # 流れの枠に、明細ごとの出荷の出来事が全部 (どの出荷も 1 回ずつ) 出ている
    assert len(shipments) == 2 * order.lines.count()
    assert [flow.count(f"出荷 {s.shipment_no} (") for s in shipments] == [1] * len(shipments)
    return len(queries.captured_queries)


def _detail_order(order):
    """詳細の画面と同じ読み方 (OrderDetailView.get_queryset) で読んだ受注。"""
    return OrderDetailView().get_queryset().get(pk=order.pk)


def _differences(got, expected) -> list:
    """違う出来事の題の組 (まとめて読んだ方, 先に読まずに作った方)。"""
    return [
        (getattr(a, "title", None), getattr(b, "title", None)) for a, b in zip_longest(got, expected) if a != b
    ]


def _assert_same_as_plain(order):
    """まとめて読んだ受注と、何も先に読まずに引き直した受注で、流れの出来事・段の帯・行のメニューの対象が等しい。"""
    read = _detail_order(order)
    events = timeline.events_for(read)
    expected = timeline.events_for(Order.objects.get(pk=order.pk))
    assert events == expected, _differences(events, expected)
    assert timeline.stage_band(read) == timeline.stage_band(Order.objects.get(pk=order.pk))
    # 明細の表の行のメニューの「納品書を作る」の対象 (BoardRow.unnoted_shipment) も同じ
    assert [line.board.unnoted_shipment for line in read.lines.all()] == [
        OrderLine.objects.get(pk=line.pk).board.unnoted_shipment for line in read.lines.all()
    ]
    return events


# ---------------------------------------------------------------- (a) 明細の数で増えない


@pytest.mark.django_db
def test_detail_query_count_does_not_grow_with_lines(client, user, processes):
    client.force_login(user)
    small = _detail_queries(client, _flowed_order(user, _customer(user, "C001"), processes, 1))
    large = _detail_queries(client, _flowed_order(user, _customer(user, "C002"), processes, 5))
    assert small == large, (small, large)


# ---------------------------------------------------------------- (b) 出る物は同じ


@pytest.mark.django_db
def test_events_and_band_are_the_same_as_reading_nothing_first(user, processes):
    order = _flowed_order(user, _customer(user, "C001"), processes, 5)

    events = _assert_same_as_plain(order)

    kinds = [event.kind for event in events]
    # 指示書は発行と取消の 2 つずつ、実績・出荷・納品書は明細ごと、請求書と入金は 1 つずつ
    assert (kinds.count("workorder"), kinds.count("progress"), kinds.count("shipment")) == (10, 5, 10)
    assert (kinds.count("delivery_note"), kinds.count("invoice"), kinds.count("payment")) == (5, 1, 1)
    assert sum(event.title.endswith("(取消)") for event in events) == 5


# ---------------------------------------------------------------- (c) 一式の行だけの請求書


@pytest.mark.django_db
def test_lump_only_invoice_is_the_same_as_reading_nothing_first(user, processes):
    today = timezone.localdate()
    customer = _customer(user, "C003")
    lump_item, plain_item = _items(user, 2)
    order = orders_services.create_order(
        customer,
        [
            {"item": lump_item, "qty": Decimal("300"), "unit_price": Decimal("50000"), "price_basis": "lump"},
            {"item": plain_item, "qty": Decimal("10"), "unit_price": Decimal("100")},
        ],
        user,
        order_date=today - timedelta(days=3),
    )
    lump, plain = order.lines.order_by("line_no")
    shipping_services.ship(lump, Decimal("100"), today, user)
    orders_services.mark_completed(OrderLine.objects.get(pk=lump.pk), user)
    invoice = billing_services.create_invoice(
        customer, [], user, issued_on=today, lumps=[(OrderLine.objects.get(pk=lump.pk), "50000")]
    )
    billing_services.record_payment(invoice, today, invoice.total, user)
    shipping_services.ship(plain, Decimal("10"), today, user)

    events = _assert_same_as_plain(order)

    # 一式の行だけの請求書 (出荷に結び付かない請求書の明細) が流れに出て、一式の明細は「入金」まで届いている
    assert f"請求書 {invoice.invoice_no} を発行" in [event.title for event in events]
    assert timeline.reached_stages(_detail_order(order).lines.all()[0]) == frozenset({1, 5, 7, 8})


# ---------------------------------------------------------------- (d) 検収の要約


@pytest.mark.django_db
def test_acceptance_summary_from_read_rows_is_the_same(user):
    customer = _customer(user, "C004")
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")} for item in _items(user, 2)],
        user,
        order_date=date(2026, 11, 2),
    )
    first, _second = order.lines.order_by("line_no")
    _accept(first, "4", date(2026, 11, 5))  # 第 1 版の注文への検収
    base = Inbox.objects.get(order=order)
    # 第 2 版を受け入れた形 (受注が今合っている注文の版を 2 にする。版の行は受注を持たず、第 1 版の行を指す)
    Inbox.objects.create(
        message_id=uuid.uuid4(), partner=customer, kind="order", payload={}, status="accepted", revises=base, revision_no=2
    )
    connect_services.record_acceptance(
        base,
        first,
        Decimal("6"),
        date(2026, 11, 7),
        None,
        message_id=uuid.uuid4(),
        buyer_order_no="PO-0001",
        buyer_line_no=first.line_no,
        order_revision_no=2,
    )

    read = _detail_order(order)
    summaries = [orders_services.acceptance_summary(line) for line in read.lines.all()]
    plain = [orders_services.acceptance_summary(OrderLine.objects.get(pk=line.pk)) for line in read.lines.all()]

    assert summaries == plain
    assert summaries == [
        {"count": 2, "total": Decimal("10"), "last_on": date(2026, 11, 7), "old_revision": 1, "current_revision": 2},
        {"count": 0, "total": Decimal("0"), "last_on": None},
    ]
    assert [line.acceptance for line in _detail_order(order).lines.all()] == plain
    # 先に読んだ検収から作るので、検収の無い明細の要約は問い合わせをしない
    second = _detail_order(order).lines.all()[1]
    with CaptureQueriesContext(connection) as queries:
        orders_services.acceptance_summary(second)
    assert len(queries.captured_queries) == 0


# ---------------------------------------------------------------- (e) 部品の無い会社


def _without_part(monkeypatch, label, relation):
    """部品が入っていない会社の形を作る (試験の中だけ)。表と model は残したまま、次の 3 つを外す。

    apps から見えなくする (apps.get_model が LookupError、apps.is_installed が偽)・受注の明細の逆の関係 relation を外す
    (getattr で辿る口と、_meta.get_field の両方)。本物の部品を外した会社で開いた確かめではない (記録に未確認と書く)。
    """
    get_model, is_installed = django_apps.get_model, django_apps.is_installed

    def without_get_model(app_label, model_name=None, require_ready=True):
        if (app_label if model_name is not None else app_label.split(".")[0]) == label:
            raise LookupError(f"{label} が入っていない")
        return get_model(app_label, model_name, require_ready)

    monkeypatch.setattr(django_apps, "get_model", without_get_model)
    monkeypatch.setattr(django_apps, "is_installed", lambda name: name != f"seizou.{label}" and is_installed(name))
    get_field = OrderLine._meta.get_field

    def without_field(field_name):
        if field_name == relation:
            raise FieldDoesNotExist(f"OrderLine has no field named '{relation}'")
        return get_field(field_name)

    monkeypatch.setattr(OrderLine._meta, "get_field", without_field)
    monkeypatch.delattr(OrderLine, relation)


@pytest.mark.django_db
@pytest.mark.parametrize(("label", "relation"), [("workorders", "workorders"), ("shipping", "shipments")])
def test_detail_opens_without_the_part(client, user, processes, monkeypatch, label, relation):
    order = _flowed_order(user, _customer(user, "C005"), processes, 2)
    client.force_login(user)
    _without_part(monkeypatch, label, relation)

    response = client.get(reverse("orders:order_detail", args=[order.pk]))

    assert response.status_code == 200
    assert '<section id="order-flow"' in response.content.decode()
