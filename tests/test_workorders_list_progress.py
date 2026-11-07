"""指示書の一覧の「工程の進み」の列 (工程ごとの名前・細い棒・良品の合計/予定の数を縦に並べる。本人の決め 11-07 18:2x〜18:4x)。

値は WorkOrder.step_progress (数は進捗の盤と同じ = 予定の数と良品の合計。不良は数えない)。行は workorders/_row.html が棒にする
(棒の長さは style の width、予定の数に届いた工程は緑)。CSV は str の字。作り物のデータは全部 services で作る。名前と品番は作り物だけ。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.progress import services as progress_services
from seizou.workorders import services
from seizou.workorders.models import StepProgress, StepProgressRow, WorkOrder

LONG_NAME = "とても長い工程の名前の例としての表面処理と外観の検査"


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
def line(user, customer, item):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("100"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 20)}],
        user,
        order_date=date(2026, 11, 2),
    )
    return order.lines.get()


@pytest.fixture
def processes(user):
    """工程 3 つ (切断・曲げ・長い名前の工程)。並びは並び順。"""
    return [
        masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user),
        masters_services.create_process({"code": "BEND", "name": "曲げ", "sort_order": 2}, user),
        masters_services.create_process({"code": "FIN", "name": LONG_NAME, "sort_order": 3}, user),
    ]


def _issue(line, user, processes, qty="10"):
    return services.issue_workorder(line, Decimal(qty), [{"process": p, "planned_qty": Decimal(qty)} for p in processes], user)


def _record(step, qty, user, *, completes=False, defect="0"):
    progress_services.record_progress(step, Decimal(qty), timezone.now(), user, completes_step=completes, defect_qty=Decimal(defect))


def _steps(workorder):
    return list(workorder.steps.order_by("seq"))


def _fresh(workorder):
    return WorkOrder.objects.get(pk=workorder.pk)


def _tr(body, pk):
    start = body.index(f'<tr id="row-{pk}"')
    return body[start : body.index("</tr>", start)]


def _cell(body, pk):
    """行の中の「工程の進み」の欄 (ul の全体)。無ければ空の字。"""
    row = _tr(body, pk)
    match = re.search(r'<ul class="flex w-44[^>]*aria-label="工程の進み">.*?</ul>', row, re.S)
    return match.group(0) if match else ""


# ---------------------------------------------------------------- 値 (WorkOrder.step_progress)


@pytest.mark.django_db
def test_step_progress_counts_good_qty_against_planned(user, line, processes):
    workorder = _issue(line, user, processes)
    cut, bend, _finish = _steps(workorder)
    _record(cut, "3", user, defect="2")  # 不良は数えない
    _record(bend, "4", user)
    _record(bend, "6", user, completes=True)

    rows = _fresh(workorder).step_progress()

    assert isinstance(rows, StepProgress)
    assert rows == [
        StepProgressRow(1, "切断", Decimal("10"), Decimal("3"), 30, False),
        StepProgressRow(2, "曲げ", Decimal("10"), Decimal("10"), 100, True),
        StepProgressRow(3, LONG_NAME, Decimal("10"), Decimal("0"), 0, False),
    ]
    assert str(rows) == f"切断 3/10・曲げ 10/10・{LONG_NAME} 0/10"


@pytest.mark.django_db
def test_step_progress_caps_the_bar_at_full_and_keeps_the_real_number(user, line, processes):
    workorder = _issue(line, user, processes[:1])
    _record(_steps(workorder)[0], "12.5", user)

    (row,) = _fresh(workorder).step_progress()

    assert (row.pct, row.full, row.recorded) == (100, True, Decimal("12.5"))
    assert str(_fresh(workorder).step_progress()) == "切断 12.5/10"


@pytest.mark.django_db
def test_step_progress_rounds_down_and_no_steps_is_empty(user, line, processes):
    workorder = _issue(line, user, processes[:1], qty="3")
    _record(_steps(workorder)[0], "1", user)
    assert _fresh(workorder).step_progress()[0].pct == 33

    empty = _issue(line, user, processes[:1])
    empty.steps.all().delete()  # 工程の無い指示書 (試験だけ。画面からは作れない。実績の無い工程だけ消せる)
    assert _fresh(empty).step_progress() == []
    assert str(_fresh(empty).step_progress()) == ""


# ---------------------------------------------------------------- 一覧の列


@pytest.mark.django_db
def test_list_renders_one_bar_per_step(logged_in, user, line, processes):
    workorder = _issue(line, user, processes)
    cut, bend, _finish = _steps(workorder)
    _record(cut, "3", user)
    _record(bend, "10", user)

    response = logged_in.get(reverse("workorders:workorder_list"))
    assert response.status_code == 200
    labels = [column.label for column in response.context["columns"]]
    assert labels[-1] == "工程の進み"
    cell = _cell(response.content.decode(), workorder.pk)

    assert cell.count("<li ") == 3
    # 一部 (30%) = 主色の棒と灰の数、届いた (100%) = 緑の棒と緑の数、まだ (0%) = 幅 0 の主色の棒
    assert 'data-step-progress="30"' in cell and 'bg-primary" style="width: 30%"' in cell
    assert '<span class="shrink-0 tabular-nums text-base-content/70">3/10</span>' in cell
    assert 'data-step-progress="100"' in cell and 'bg-success" style="width: 100%"' in cell
    assert '<span class="shrink-0 font-medium tabular-nums text-success">10/10</span>' in cell
    assert 'data-step-progress="0"' in cell and 'bg-primary" style="width: 0%"' in cell
    # 工程の名前は 1 行で切り、指すと順と全部の名前
    assert f'<span class="min-w-0 truncate" title="3 {LONG_NAME}">{LONG_NAME}</span>' in cell
    assert 'title="1 切断">切断</span>' in cell
    assert cell.count('role="progressbar"') == 3 and 'aria-label="切断 の進み"' in cell
    assert cell.index("切断") < cell.index("曲げ") < cell.index(LONG_NAME)


@pytest.mark.django_db
def test_list_cancelled_row_still_shows_the_bars(logged_in, user, line, processes):
    cancelled = _issue(line, user, processes[:2])
    services.cancel_workorder(cancelled, user, reason="作り物の取消")

    body = logged_in.get(reverse("workorders:workorder_list")).content.decode()
    cell = _cell(body, cancelled.pk)

    assert cell.count("<li ") == 2 and cell.count('style="width: 0%"') == 2
    assert "取消" in _tr(body, cancelled.pk)


@pytest.mark.django_db
def test_list_csv_writes_the_text_form(logged_in, user, line, processes):
    workorder = _issue(line, user, processes[:2])
    _record(_steps(workorder)[0], "3", user)

    lines = logged_in.get(reverse("workorders:workorder_list"), {"format": "csv"}).content.decode("utf-8-sig").splitlines()
    header = lines[0].split(",")
    position = header.index("工程の進み")

    assert lines[1].split(",")[0] == workorder.wo_no
    assert lines[1].split(",")[position] == "切断 3/10・曲げ 0/10"


@pytest.mark.django_db
def test_list_query_count_does_not_grow_with_rows(logged_in, user, line, processes):
    url = reverse("workorders:workorder_list")

    def count():
        assert logged_in.get(url).status_code == 200
        with CaptureQueriesContext(connection) as ctx:
            assert logged_in.get(url).status_code == 200
        return len(ctx.captured_queries)

    for _ in range(2):
        _record(_steps(_issue(line, user, processes))[0], "1", user)
    few = count()
    for _ in range(3):
        _record(_steps(_issue(line, user, processes))[1], "2", user)
    assert count() == few


@pytest.mark.django_db
def test_detail_does_not_show_the_bars(logged_in, user, line, processes):
    workorder = _issue(line, user, processes)

    body = logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk])).content.decode()

    assert 'aria-label="工程の進み"' not in body and 'role="progressbar"' not in body
