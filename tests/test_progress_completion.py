"""工程と指示書の完了を、実績の「この工程を完了にする」の印で決める (初見テスト R5 の候補 C103 の その 3)。

決め方 (PROG-1・PROG-1a = a):
- 工程の完了 = その工程に、完了の印の付いた実績が 1 件でもある (数は見ない。予定に届かなくても、超えても同じ)。
- 指示書の完了 = 最後の工程 (順が最大) が完了 (途中の工程の印は見ない)。
- 指示書の着手済 = どれかの工程に実績が 1 件でもある (良品 0・不良だけの実績も着手。C103-Q2 = a)。
- 残 = 予定の数 − 良品の合計のまま (不良は引かない)。完成の数も良品の合計のまま。

同じ決め方を、指示書の状態・進捗の盤・工程のタブ (step_rows)・生産管理ボード (BoardRow)・受注の段の帯 (reached_stages)・
入庫のボタン (finished_due)・生産計画の盤の棒 (bar_state) で見る。名前と品番は作り物だけ。
"""

import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.orders.board import BoardRow
from seizou.orders.models import OrderLine
from seizou.orders.timeline import reached_stages
from seizou.progress import boards, services
from seizou.stock import services as stock_services
from seizou.workorders import plans, worklist
from seizou.workorders import services as workorders_services
from seizou.workorders.models import StepTotals, WorkOrder, WorkOrderStep

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
DONE_BADGE = '<span class="badge badge-success ml-2">完了</span>'


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1)


@pytest.fixture
def bend(db):
    return Process.objects.create(code="BND", name="曲げ", sort_order=2)


@pytest.fixture
def issue(user):
    """作り物の受注を 1 つ作り、その明細に工程の並びの指示書を発行する関数 (返す物は指示書)。"""
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)

    def _issue(*processes, planned=Decimal("10")):
        order = orders_services.create_order(
            customer,
            [{"item": item, "qty": planned, "unit_price": Decimal("100")}],
            user,
            order_date=ORDER_DATE,
        )
        return workorders_services.issue_workorder(
            order.lines.get(),
            planned,
            [{"process": process, "planned_qty": planned} for process in processes],
            user,
        )

    return _issue


def _record(step, qty, user, *, defect="0", completes=False):
    return services.record_progress(
        step, Decimal(str(qty)), RECORDED_AT, user, defect_qty=Decimal(str(defect)), completes_step=completes
    )


def _state(workorder):
    return WorkOrder.objects.get(pk=workorder.pk).progress_state()


def _board_rows():
    return {row["step"]: row for row in boards.build_board(None)["rows"]}


def _board_html_row(client, step):
    body = client.get(reverse("progress:progress_list")).content.decode()
    match = re.search(rf'<tr id="progress-step-{step.pk}".*?</tr>', body, re.S)
    assert match is not None
    return match.group(0)


def _line_row(workorder):
    """生産管理ボードの 1 行 (prefetch の無い明細から作る = 受注の詳細の段の帯と同じ道)。"""
    return BoardRow(OrderLine.objects.get(pk=workorder.order_line_id))


def _prefetched_row(workorder):
    """生産管理ボードの 1 行 (一覧と同じ prefetch_for を通した明細から作る)。"""
    from seizou.orders.board import prefetch_for

    line = prefetch_for(OrderLine.objects.filter(pk=workorder.order_line_id)).get()
    return BoardRow(line)


def _bar_state(step):
    step = WorkOrderStep.objects.select_related("workorder__order_line").get(pk=step.pk)
    totals = plans._step_totals([other.pk for other in step.workorder.steps.all()])
    return plans.bar_state(step, totals)


def _due(workorder):
    return plans.finished_due(WorkOrder.objects.get(pk=workorder.pk))


# --- (a) 数が予定に届いても、印が無ければ完了でない ---


@pytest.mark.django_db
def test_reached_without_mark_is_not_done(client_logged_in, issue, cut, user):
    workorder = issue(cut)
    step = workorder.steps.get()
    _record(step, 10, user)

    assert DONE_BADGE not in _board_html_row(client_logged_in, step)
    assert _board_rows()[step.pk]["remaining"] == Decimal("0")
    (row,) = plans.step_rows(workorder)
    assert row["done"] is False and row["current"] is True
    assert _state(workorder) == WorkOrder.PROGRESS_STARTED
    for line_row in (_line_row(workorder), _prefetched_row(workorder)):
        assert line_row.current_step.pk == step.pk
        assert line_row.progress_label == "切断 10/10"
        assert line_row.all_done is False
    assert 4 not in reached_stages(OrderLine.objects.get(pk=workorder.order_line_id))
    assert _due(workorder) is False
    assert _bar_state(step) != "done"


# --- (b) 予定に届かなくても、印があれば完了 ---


@pytest.mark.django_db
def test_mark_without_reaching_plan_is_done(client_logged_in, issue, cut, user):
    workorder = issue(cut)
    step = workorder.steps.get()
    _record(step, 3, user, completes=True)

    assert DONE_BADGE in _board_html_row(client_logged_in, step)
    assert _board_rows()[step.pk]["remaining"] == Decimal("7")
    (row,) = plans.step_rows(workorder)
    assert row["done"] is True and row["current"] is False
    assert _state(workorder) == WorkOrder.PROGRESS_DONE
    for line_row in (_line_row(workorder), _prefetched_row(workorder)):
        assert line_row.current_step is None
        assert line_row.progress_label == "完了"
        assert line_row.all_done is True
        assert line_row.completed_total == Decimal("3")  # 完成の数は良品のまま
    assert 4 in reached_stages(OrderLine.objects.get(pk=workorder.order_line_id))
    assert _due(workorder) is True  # 入庫の残り 3 がある
    assert _bar_state(step) == "done"
    assert stock_services.finished_qty(WorkOrder.objects.get(pk=workorder.pk)) == Decimal("3")


# --- (c) 指示書の完了は最後の工程の印だけで決める ---


@pytest.mark.django_db
def test_last_step_mark_decides_workorder(issue, cut, bend, user):
    workorder = issue(cut, bend)
    first, second = workorder.steps.order_by("seq")
    _record(second, 10, user, completes=True)

    assert _state(workorder) == WorkOrder.PROGRESS_DONE
    rows = plans.step_rows(workorder)
    assert [row["done"] for row in rows] == [False, True]  # 工程 1 の印の抜けが見える
    assert not any(row["current"] for row in rows)
    assert _line_row(workorder).progress_label == "完了"


@pytest.mark.django_db
def test_first_step_mark_moves_current_to_next(issue, cut, bend, user):
    workorder = issue(cut, bend)
    first, second = workorder.steps.order_by("seq")
    _record(first, 4, user, completes=True)

    assert _state(workorder) == WorkOrder.PROGRESS_STARTED
    for line_row in (_line_row(workorder), _prefetched_row(workorder)):
        assert line_row.current_step.pk == second.pk
        assert line_row.progress_label == "曲げ 0/10"
    rows = plans.step_rows(workorder)
    assert [row["current"] for row in rows] == [False, True]


# --- (d) 良品 0・不良だけの実績 ---


@pytest.mark.django_db
def test_defect_only_record_is_started(issue, cut, user):
    workorder = issue(cut)
    step = workorder.steps.get()
    _record(step, 0, user, defect=3)

    assert _state(workorder) == WorkOrder.PROGRESS_STARTED
    assert workorder.pk not in set(worklist.unaccepted_only(WorkOrder.objects.all()).values_list("pk", flat=True))
    row = _board_rows()[step.pk]
    assert row["remaining"] == Decimal("10")  # 不良は残から引かない
    assert row["defect"] == Decimal("3")
    assert _line_row(workorder).workorder_label == "10 着手済"


# --- (e) 進捗の盤の行の値 ---


@pytest.mark.django_db
def test_board_row_values_with_defect_and_mark(client_logged_in, issue, cut, user):
    """良品 490・不良 10・印あり (予定 500)。盤の行の値は 残 10・予定 500・実績の合計 490・不良の合計 10 で「完了」の札。

    「不良の合計」は盤の行の値 (defect) までで、画面の列は足していない (行の template が依頼の触ってよい物の外。記録に書いた)。
    """
    workorder = issue(cut, planned=Decimal("500"))
    step = workorder.steps.get()
    _record(step, 490, user, defect=10, completes=True)

    row = _board_rows()[step.pk]
    assert (row["remaining"], row["planned"], row["recorded"], row["defect"]) == (
        Decimal("10"),
        Decimal("500"),
        Decimal("490"),
        Decimal("10"),
    )
    assert row["done"] is True
    html_row = _board_html_row(client_logged_in, step)
    cells = re.findall(r'<td class="whitespace-nowrap text-right tabular-nums[^"]*"[^>]*>([^<]*)</td>', html_row)
    assert cells == ["10", "500", "490"]
    assert DONE_BADGE in html_row


# --- (f) 印を外す・消す ---


@pytest.mark.django_db
def test_unmark_or_delete_reverts_completion(issue, cut, user):
    workorder = issue(cut)
    step = workorder.steps.get()
    record = _record(step, 3, user, completes=True)
    assert _state(workorder) == WorkOrder.PROGRESS_DONE

    services.update_progress(record, Decimal("3"), RECORDED_AT, user, completes_step=False)
    assert _state(workorder) == WorkOrder.PROGRESS_STARTED
    assert _line_row(workorder).current_step.pk == step.pk

    services.update_progress(record, Decimal("3"), RECORDED_AT, user, completes_step=True)
    assert _state(workorder) == WorkOrder.PROGRESS_DONE
    services.delete_progress(record, user)
    assert _state(workorder) == WorkOrder.PROGRESS_NOT_STARTED


@pytest.mark.django_db
def test_two_marks_survive_deleting_one(issue, cut, user):
    workorder = issue(cut)
    step = workorder.steps.get()
    first = _record(step, 3, user, completes=True)
    _record(step, 2, user, completes=True)

    services.delete_progress(first, user)

    assert _state(workorder) == WorkOrder.PROGRESS_DONE
    assert plans.step_rows(workorder)[0]["done"] is True


# --- (g) 問い合わせの数 ---


@pytest.mark.django_db
def test_progress_board_queries_do_not_grow_with_steps(client_logged_in, issue, cut, bend, user):
    url = reverse("progress:progress_list")

    def count():
        assert client_logged_in.get(url).status_code == 200
        with CaptureQueriesContext(connection) as ctx:
            assert client_logged_in.get(url).status_code == 200
        return len(ctx.captured_queries)

    for step in issue(cut, bend).steps.all():
        _record(step, 1, user, defect=1, completes=True)
    few = count()
    for _ in range(2):
        for step in issue(cut, bend).steps.all():
            _record(step, 1, user, defect=1, completes=True)
    assert count() == few


@pytest.mark.django_db
def test_board_row_without_prefetch_reads_once_per_step(issue, cut, bend, user):
    """prefetch の無い明細 (受注の詳細の段の帯の道) は、工程 1 つにつき 1 回 (良品・不良・印を 1 回で読む)。"""
    paint = Process.objects.create(code="PNT", name="塗装", sort_order=3)
    one = issue(cut)
    three = issue(cut, bend, paint)

    def count(workorder):
        line = OrderLine.objects.get(pk=workorder.order_line_id)
        with CaptureQueriesContext(connection) as ctx:
            row = BoardRow(line)
            row.progress_label
            row.workorder_label
        return len(ctx.captured_queries)

    assert count(three) - count(one) == 2


# --- (h) 決め方の関数 ---


def test_progress_state_of_reads_marks_and_counts():
    """渡した StepTotals だけを読む (表を読まない)。完了は印だけ、着手済は良品と不良の合計 > 0。"""

    class Step:
        def __init__(self, pk, seq, planned_qty):
            self.pk, self.seq, self.planned_qty = pk, seq, Decimal(planned_qty)

    steps = [Step(1, 1, "10"), Step(2, 2, "10")]
    decide = WorkOrder.progress_state_of
    issued = WorkOrder.STATUS_ISSUED
    assert decide(issued, steps, {2: StepTotals(Decimal("20"), Decimal("0"), False)}) == WorkOrder.PROGRESS_STARTED
    assert decide(issued, steps, {2: StepTotals(Decimal("1"), Decimal("0"), True)}) == WorkOrder.PROGRESS_DONE
    assert decide(issued, steps, {1: StepTotals(Decimal("0"), Decimal("3"), False)}) == WorkOrder.PROGRESS_STARTED
    assert decide(issued, steps, {1: StepTotals(Decimal("10"), Decimal("0"), True)}) == WorkOrder.PROGRESS_STARTED
    assert decide(issued, steps, {1: StepTotals()}) == WorkOrder.PROGRESS_NOT_STARTED
