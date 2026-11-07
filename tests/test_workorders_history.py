"""指示書と工程と実績の変更の履歴 (決め HIST-1 の伝票の「指示書」「実績」・PROG-1d・PROG-1e。C106 の その 8)。

指示書の書く関数 (issue_workorder・plan_step・cancel_workorder) と実績の書く関数 (record_progress・update_progress・delete_progress) が、
指示書の詳細の「履歴」に作った・直した・取り消した・消したを残すこと、直した人と消した人が変更の履歴の人に残ること、
何も変えなければ増えないこと、詳細の問い合わせの数が履歴の行の数で増えないことを見る。データは作り物だけ。
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.core.models import ChangeLog
from seizou.core.services import record_history
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.progress import services as progress_services
from seizou.workorders import services as workorders_services

AT = timezone.make_aware(datetime(2026, 11, 4, 9, 30))


@pytest.fixture
def line(user):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "H-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}], user, order_date=date(2026, 11, 2)
    )
    return order.lines.get()


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


@pytest.fixture
def workorder(user, line, cut):
    return workorders_services.issue_workorder(line, Decimal("10"), [{"process": cut, "planned_qty": Decimal("10")}], user)


@pytest.fixture
def other(db):
    return get_user_model().objects.create_user("naoshite", password="pass-for-tests-only")


def _rows(workorder):
    return list(ChangeLog.objects.filter(owner_model="workorders.workorder", owner_pk=workorder.pk))


def _history_part(client, workorder):
    body = client.get(reverse("workorders:workorder_detail", args=[workorder.pk])).content.decode()
    start = body.index('aria-label="履歴"')
    return body[start : body.index("data-last-change", start)]


@pytest.mark.django_db
def test_issue_records_workorder_and_steps(workorder):
    assert sorted((row.model, row.action) for row in _rows(workorder)) == [
        ("workorders.workorder", "created"),
        ("workorders.workorderstep", "created"),
    ]


@pytest.mark.django_db
def test_plan_step_before_after(logged_in, user, workorder):
    step = workorder.steps.get()

    workorders_services.plan_step(step, date(2026, 11, 5), date(2026, 11, 6), user)
    workorders_services.plan_step(step, date(2026, 11, 5), date(2026, 11, 7), user)

    rows = [row for row in _rows(workorder) if row.action == "updated"]
    assert [row.changes for row in rows] == [
        [{"field": "planned_end", "label": "終了日", "type": "date", "before": "2026-11-06", "after": "2026-11-07"}],
        [
            {"field": "planned_start", "label": "開始日", "type": "date", "before": "", "after": "2026-11-05"},
            {"field": "planned_end", "label": "終了日", "type": "date", "before": "", "after": "2026-11-06"},
        ],
    ]
    assert f"{step} を直した" in _history_part(logged_in, workorder)

    count = ChangeLog.objects.count()
    workorders_services.plan_step(step, date(2026, 11, 5), date(2026, 11, 7), user)  # 何も変えない
    assert ChangeLog.objects.count() == count


@pytest.mark.django_db
def test_cancel_records_status_before_after(logged_in, user, workorder):
    workorders_services.cancel_workorder(workorder, user, reason="作り物の理由")

    row = _rows(workorder)[0]
    assert (row.model, row.action) == ("workorders.workorder", "cancelled")
    assert {"field": "status", "label": "状態", "type": "text", "before": "発行", "after": "取消"} in row.changes
    part = _history_part(logged_in, workorder)
    assert "取り消した" in part and "状態: 発行 → 取消" in part


@pytest.mark.django_db
def test_progress_created_updated_deleted_by_other_person(logged_in, user, other, workorder):
    step = workorder.steps.get()
    progress = progress_services.record_progress(step, Decimal("5"), AT, user, defect_qty=Decimal("1"), completes_step=True)
    created_text = str(progress)

    created = _rows(workorder)[0]
    assert (created.model, created.action, created.actor_label) == ("progress.progress", "created", "tester")
    assert {"field": "completes_step", "label": "工程の完了", "type": "bool", "before": "", "after": "true"} in created.changes

    progress_services.update_progress(progress, Decimal("6"), AT, other, defect_qty=Decimal("2"), completes_step=False)

    updated = _rows(workorder)[0]
    assert (updated.action, updated.actor_label) == ("updated", "naoshite")  # 直した人 (実績の人は入れた人のまま)
    assert updated.changes == [
        {"field": "qty", "label": "実績の数", "type": "number", "before": "5", "after": "6"},
        {"field": "defect_qty", "label": "不良の数", "type": "number", "before": "1", "after": "2"},
        {"field": "completes_step", "label": "工程の完了", "type": "bool", "before": "true", "after": "false"},
    ]
    part = _history_part(logged_in, workorder)
    assert "実績の数: 5 → 6" in part
    assert "不良の数: 1 → 2" in part
    assert "工程の完了: はい → いいえ" in part
    assert "工程の完了: はい" in part  # 作った行の「その時の値」

    count = ChangeLog.objects.count()
    progress_services.update_progress(progress, Decimal("6"), AT, other, defect_qty=Decimal("2"), completes_step=False)
    assert ChangeLog.objects.count() == count  # 何も変えない

    deleted_text = str(progress.__class__.objects.get(pk=progress.pk))
    progress_services.delete_progress(progress, other)

    deleted = _rows(workorder)[0]
    assert (deleted.action, deleted.actor_label, deleted.object_text) == ("deleted", "naoshite", deleted_text)
    assert created_text != deleted_text
    part = _history_part(logged_in, workorder)
    assert f"{deleted_text} を消した" in part


def _queries(client, url) -> int:
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200
    return len(ctx.captured_queries)


@pytest.mark.django_db
def test_detail_queries_do_not_grow_with_history_rows(logged_in, user, line, cut, workorder):
    many = workorders_services.issue_workorder(line, Decimal("5"), [{"process": cut, "planned_qty": Decimal("5")}], user)
    few = workorder
    for n in range(10):  # 状態を変えずに、履歴の行だけを増やす
        record_history(many, user, changes=[{"field": "qty", "label": "数量", "type": "number", "before": str(n), "after": str(n + 1)}])
    assert len(_rows(many)) > len(_rows(few)) + 5

    few_url = reverse("workorders:workorder_detail", args=[few.pk])
    many_url = reverse("workorders:workorder_detail", args=[many.pk])
    for url in (few_url, many_url):  # 最初の 1 回の控えを外す
        assert logged_in.get(url).status_code == 200

    assert _queries(logged_in, many_url) == _queries(logged_in, few_url)
