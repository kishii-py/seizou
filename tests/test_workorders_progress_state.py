"""指示書の進みの状態 (未着手・着手済・完了・取消) を実績から出す (初見テスト R5 の候補 C27 の 3〜4。C27-1 = a・WO-1 = b)。

決め方は WorkOrder.progress_state_of の 1 か所: 取消 → 完了 (最後の工程の実績の合計が予定の数以上) →
着手済 (どれかの工程に実績がある) → 未着手。状態は列に持たない。名前と品番は作り物だけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services as progress_services
from seizou.workorders import services
from seizou.workorders.models import WorkOrder, WorkOrderStep

ORDER_DATE = date(2026, 11, 2)
DUE_DATE = date(2026, 11, 20)


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
def order(user, customer, item):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": DUE_DATE}],
        user,
        order_date=ORDER_DATE,
    )


@pytest.fixture
def line(order):
    return order.lines.get()


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1)


@pytest.fixture
def bend(db):
    return Process.objects.create(code="BEND", name="曲げ", sort_order=2)


def _issue(line, user, cut, bend, qty="4"):
    """工程 2 つ (1 = 切断、2 = 曲げ。予定の数は qty ずつ) の指示書。"""
    return services.issue_workorder(
        line,
        Decimal(qty),
        [{"process": cut, "planned_qty": Decimal(qty)}, {"process": bend, "planned_qty": Decimal(qty)}],
        user,
    )


@pytest.fixture
def workorder(user, line, cut, bend):
    return _issue(line, user, cut, bend)


def _record(step, qty, user):
    progress_services.record_progress(step, Decimal(qty), timezone.now(), user)


def _state(workorder):
    return WorkOrder.objects.get(pk=workorder.pk).progress_state()


# ---------------------------------------------------------------- 3 決め方


@pytest.mark.django_db
def test_no_progress_is_not_started(workorder):
    assert _state(workorder) == WorkOrder.PROGRESS_NOT_STARTED
    assert WorkOrder.objects.get(pk=workorder.pk).progress_state_label() == "未着手"


@pytest.mark.django_db
def test_one_record_is_started(user, workorder):
    first, _second = workorder.steps.order_by("seq")
    _record(first, "1", user)

    assert _state(workorder) == WorkOrder.PROGRESS_STARTED
    assert WorkOrder.objects.get(pk=workorder.pk).progress_state_label() == "着手済"


@pytest.mark.django_db
def test_only_first_step_reached_is_started(user, workorder):
    first, _second = workorder.steps.order_by("seq")
    _record(first, "4", user)

    assert _state(workorder) == WorkOrder.PROGRESS_STARTED


@pytest.mark.django_db
def test_last_step_reached_is_done_even_if_earlier_step_short(user, workorder):
    """完了は最後の工程だけで決める (WO-1 の決め方)。前の工程が予定に届いていなくても完了。"""
    first, second = workorder.steps.order_by("seq")
    _record(first, "1", user)
    _record(second, "4", user)

    assert _state(workorder) == WorkOrder.PROGRESS_DONE
    assert WorkOrder.objects.get(pk=workorder.pk).progress_state_label() == "完了"


@pytest.mark.django_db
def test_cancelled_is_cancelled(user, workorder):
    services.cancel_workorder(workorder, user, reason="作り物の取消")

    assert _state(workorder) == WorkOrder.PROGRESS_CANCELLED
    assert WorkOrder.objects.get(pk=workorder.pk).progress_state_label() == "取消"


@pytest.mark.django_db
def test_workorder_without_steps_is_not_started(user, workorder):
    WorkOrderStep.objects.filter(workorder=workorder).delete()

    assert _state(workorder) == WorkOrder.PROGRESS_NOT_STARTED


@pytest.mark.django_db
def test_without_progress_part_is_not_started(monkeypatch, user, workorder):
    first, second = workorder.steps.order_by("seq")
    _record(first, "4", user)
    _record(second, "4", user)
    original = WorkOrderStep._meta.get_field

    def get_field(name, *args, **kwargs):
        if name == "progress_records":
            raise FieldDoesNotExist(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(WorkOrderStep._meta, "get_field", get_field)

    assert _state(workorder) == WorkOrder.PROGRESS_NOT_STARTED


def test_progress_state_of_reads_only_given_totals():
    """決め方の関数は、渡した工程と合計だけを読む (表を読まない)。"""

    class Step:
        def __init__(self, pk, seq, planned_qty):
            self.pk, self.seq, self.planned_qty = pk, seq, Decimal(planned_qty)

    steps = [Step(2, 2, "4"), Step(1, 1, "4")]
    decide = WorkOrder.progress_state_of
    assert decide(WorkOrder.STATUS_ISSUED, steps, {}) == WorkOrder.PROGRESS_NOT_STARTED
    assert decide(WorkOrder.STATUS_ISSUED, steps, {1: Decimal("4")}) == WorkOrder.PROGRESS_STARTED
    assert decide(WorkOrder.STATUS_ISSUED, steps, {2: Decimal("4")}) == WorkOrder.PROGRESS_DONE
    assert decide(WorkOrder.STATUS_CANCELLED, steps, {2: Decimal("4")}) == WorkOrder.PROGRESS_CANCELLED
    assert decide(WorkOrder.STATUS_ISSUED, [], {}) == WorkOrder.PROGRESS_NOT_STARTED


@pytest.mark.django_db
def test_accept_mark_is_not_read(user, workorder):
    """着手の印を読まない。印を付けただけで実績の無い指示書は未着手のまま。"""
    services.accept_workorder(workorder, user)

    assert _state(workorder) == WorkOrder.PROGRESS_NOT_STARTED


# ---------------------------------------------------------------- 4 字と印 (題の横・基本情報・小窓・一覧・絞り・QR を読んだ先)

#: 印の形 (core/_status_badge.html。題の横は点つき)
DOT = {
    "未着手": '<span class="badge badge-ghost gap-2"><span class="status" aria-hidden="true"></span>未着手</span>',
    "着手済": '<span class="badge badge-soft badge-info gap-2"><span class="status status-info" aria-hidden="true"></span>着手済</span>',
    "完了": '<span class="badge badge-soft badge-success gap-2"><span class="status status-success" aria-hidden="true"></span>完了</span>',
    "取消": '<span class="badge badge-outline badge-error gap-2"><span class="status status-error" aria-hidden="true"></span>取消</span>',
}
PLAIN = {
    "未着手": '<span class="badge badge-ghost">未着手</span>',
    "着手済": '<span class="badge badge-soft badge-info">着手済</span>',
    "完了": '<span class="badge badge-soft badge-success">完了</span>',
    "取消": '<span class="badge badge-outline badge-error">取消</span>',
}


def _heading(client, workorder):
    """詳細の見出しの帯の題と印 (<h1 から、題と印を包む div の閉じまで)。"""
    body = client.get(reverse("workorders:workorder_detail", args=[workorder.pk])).content.decode()
    start = body.index("<h1")
    return body[start : body.index("</div>", start)]


def _tr(body, pk):
    start = body.index(f'<tr id="row-{pk}"')
    return body[start : body.index("</tr>", start)]


@pytest.mark.django_db
def test_heading_badge_follows_progress(logged_in, user, line, cut, bend):
    not_started = _issue(line, user, cut, bend)
    started = _issue(line, user, cut, bend)
    done = _issue(line, user, cut, bend)
    cancelled = _issue(line, user, cut, bend)
    _record(started.steps.order_by("seq").first(), "1", user)
    for step in done.steps.order_by("seq"):
        _record(step, "4", user)
    services.cancel_workorder(cancelled, user, reason="作り物の取消")

    for workorder, label in ((not_started, "未着手"), (started, "着手済"), (done, "完了"), (cancelled, "取消")):
        heading = _heading(logged_in, workorder)
        assert DOT[label] in heading, label
        assert "発行" not in heading and "badge-primary" not in heading, label


@pytest.mark.django_db
def test_basic_info_and_modal_have_no_status_field(logged_in, workorder):
    detail = logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk]))
    assert "状態" not in [field["label"] for field in detail.context["fields"]]

    modal = logged_in.get(reverse("workorders:workorder_modal", args=[workorder.pk]), HTTP_HX_REQUEST="true")
    assert "状態" not in [field["label"] for field in modal.context["fields"]]
    body = modal.content.decode()
    assert PLAIN["未着手"] in body
    assert ">発行<" not in body


@pytest.mark.django_db
def test_list_status_column(logged_in, user, line, cut, bend):
    not_started = _issue(line, user, cut, bend)
    started = _issue(line, user, cut, bend)
    _record(started.steps.order_by("seq").first(), "1", user)
    url = reverse("workorders:workorder_list")

    response = logged_in.get(url)
    labels = [column.label for column in response.context["columns"]]
    assert labels[5] == "状態"
    body = response.content.decode()
    assert PLAIN["未着手"] in _tr(body, not_started.pk)
    assert PLAIN["着手済"] in _tr(body, started.pk)
    assert ">発行<" not in body

    csv_lines = logged_in.get(url, {"format": "csv"}).content.decode("utf-8-sig").splitlines()
    header = csv_lines[0].split(",")
    position = header.index("状態")
    states = {row.split(",")[0]: row.split(",")[position] for row in csv_lines[1:]}
    assert states == {not_started.wo_no: "未着手", started.wo_no: "着手済"}


@pytest.mark.django_db
def test_list_query_count_does_not_grow_with_rows(logged_in, user, line, cut, bend):
    url = reverse("workorders:workorder_list")

    def count():
        assert logged_in.get(url).status_code == 200
        with CaptureQueriesContext(connection) as ctx:
            assert logged_in.get(url).status_code == 200
        return len(ctx.captured_queries)

    for _ in range(2):
        _record(_issue(line, user, cut, bend).steps.order_by("seq").first(), "1", user)
    few = count()
    for _ in range(2):
        _record(_issue(line, user, cut, bend).steps.order_by("seq").first(), "1", user)
    assert count() == few


@pytest.mark.django_db
def test_status_filter_choices(logged_in, user, line, cut, bend):
    issued = _issue(line, user, cut, bend)
    cancelled = _issue(line, user, cut, bend)
    services.cancel_workorder(cancelled, user, reason="作り物の取消")
    url = reverse("workorders:workorder_list")

    response = logged_in.get(url)
    status = next(spec for spec in response.context["filters"] if spec["param"] == "f_status")
    assert [label for _, label in status["options"]] == ["未着手・着手済・完了", "取消"]
    body = response.content.decode()
    select = body[body.index('name="f_status"') : body.index("</select>", body.index('name="f_status"'))]
    assert "すべて" in select and "発行" not in select

    listed = logged_in.get(url, {"f_status": WorkOrder.STATUS_ISSUED}).content.decode()
    assert f'id="row-{issued.pk}"' in listed
    assert f'id="row-{cancelled.pk}"' not in listed


@pytest.mark.django_db
def test_scan_badge(logged_in, user, workorder):
    url = reverse("workorders:workorder_scan", args=[workorder.pk])
    assert PLAIN["未着手"] in logged_in.get(url).content.decode()

    _record(workorder.steps.order_by("seq").first(), "1", user)
    body = logged_in.get(url).content.decode()
    assert PLAIN["着手済"] in body
    assert ">発行<" not in body


# ---------------------------------------------------------------- 7 着手の印を読んでいた所を実績に替える


def _board_row(line):
    from seizou.orders.board import BoardRow
    from seizou.orders.models import OrderLine

    return BoardRow(OrderLine.objects.get(pk=line.pk))


@pytest.mark.django_db
def test_board_workorder_label_follows_progress(user, line, cut, bend):
    first = _issue(line, user, cut, bend)
    assert _board_row(line).workorder_label == "4 未着手"

    for step in first.steps.order_by("seq"):
        _record(step, "4", user)
    assert _board_row(line).workorder_label == "4 完了"

    _issue(line, user, cut, bend, qty="3")
    row = _board_row(line)
    assert row.workorder_label == "7 着手済"
    assert row.started_count == 1


@pytest.mark.django_db
def test_board_two_workorders_one_started(user, line, cut, bend):
    _issue(line, user, cut, bend)
    started = _issue(line, user, cut, bend, qty="3")
    _record(started.steps.order_by("seq").first(), "1", user)

    assert _board_row(line).workorder_label == "7 着手済"


@pytest.mark.django_db
def test_progress_without_accept_moves_every_reader(logged_in, user, order, line, workorder):
    """着手を押さずに切断に実績を入れると: 未着手だけから外れ、段の帯が着手、盤の棒が着手、流れに着手の行が無い。"""
    from seizou.orders.timeline import events_for, stage_of
    from seizou.workorders import plans

    url = reverse("workorders:workorder_list")
    assert f'id="row-{workorder.pk}"' in logged_in.get(url, {"f_unaccepted": "1"}).content.decode()
    first = workorder.steps.order_by("seq").first()
    _record(first, "1", user)

    assert f'id="row-{workorder.pk}"' not in logged_in.get(url, {"f_unaccepted": "1"}).content.decode()
    assert stage_of(line) == 3
    step = WorkOrderStep.objects.select_related("workorder__order_line").get(pk=first.pk)
    totals = plans._recorded_totals([other.pk for other in workorder.steps.all()])
    assert plans.bar_state(step, totals) == "accepted"
    titles = [event.title for event in events_for(order)]
    assert not any(title.endswith("に着手") for title in titles)
    assert any(title.startswith("実績 切断 ") for title in titles)


@pytest.mark.django_db
def test_unaccepted_filter_without_progress_part(monkeypatch, user, line, cut, bend):
    from seizou.workorders import worklist

    issued = _issue(line, user, cut, bend)
    cancelled = _issue(line, user, cut, bend)
    services.cancel_workorder(cancelled, user, reason="作り物の取消")
    original = WorkOrderStep._meta.get_field

    def get_field(name, *args, **kwargs):
        if name == "progress_records":
            raise FieldDoesNotExist(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(WorkOrderStep._meta, "get_field", get_field)

    assert list(worklist.unaccepted_only(WorkOrder.objects.all())) == [issued]
