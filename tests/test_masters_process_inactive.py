"""工程の使用停止 (N140。部品と取引先の N135 と同じ形)。消さずに指示書の発行の候補から外し、今までの指示書の工程はそのまま残る。

作り物のデータは全部 services で作る。使用停止は update_process に active=False を渡す (編集の画面と同じ道)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.workorders import services as workorders_services
from seizou.workorders.forms import WorkOrderIssueForm
from seizou.workorders.models import WorkOrder

DIMMED = " text-base-content/50"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def processes(user):
    """使用中の工程 CUT と BEND、使用停止の工程 OLD (並び順は OLD が真ん中)。"""
    cut = masters_services.create_process({"code": "CUT", "name": "作り物の切断", "sort_order": 1}, user)
    old = masters_services.create_process({"code": "OLD", "name": "作り物の古い塗装", "sort_order": 2}, user)
    bend = masters_services.create_process({"code": "BEND", "name": "作り物の曲げ", "sort_order": 3}, user)
    masters_services.update_process(old, {"active": False}, user)
    return cut, old, bend


@pytest.fixture
def line(user):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}], user, order_date=date(2026, 11, 4)
    )
    return order.lines.get()


def _row(body, pk):
    start = body.index(f'<tr id="row-{pk}"')
    return body[start : body.index(">", start)]


@pytest.mark.django_db
def test_process_list_shows_active_only_until_flag_and_dims_inactive_rows(logged_in, processes):
    cut, old, _bend = processes
    url = reverse("masters:process_list")

    body = logged_in.get(url).content.decode()
    assert f'id="row-{cut.pk}"' in body and f'id="row-{old.pk}"' not in body
    assert "使用停止も出す" in body
    assert DIMMED not in _row(body, cut.pk)

    shown = logged_in.get(url, {"f_inactive": "1"}).content.decode()
    assert DIMMED in _row(shown, old.pk) and DIMMED not in _row(shown, cut.pk)

    rows = logged_in.get(url, {"f_inactive": "1"}, HTTP_HX_REQUEST="true").content.decode()
    assert "<html" not in rows and DIMMED in _row(rows, old.pk)

    default_csv = logged_in.get(url, {"format": "csv"}).content.decode("utf-8-sig")
    shown_csv = logged_in.get(url, {"format": "csv", "f_inactive": "1"}).content.decode("utf-8-sig")
    assert "CUT" in default_csv and "OLD" not in default_csv and "OLD" in shown_csv


@pytest.mark.django_db
def test_edit_screen_turns_process_off_and_detail_shows_notice(logged_in, user):
    process = masters_services.create_process({"code": "CUT", "name": "作り物の切断", "sort_order": 1}, user)
    assert "active" not in logged_in.get(reverse("masters:process_create")).context["form"].fields
    url = reverse("masters:process_update", args=[process.pk])
    assert logged_in.get(url).context["form"]["active"].value() is True

    # チェックを外して保存 (チェックの無い欄は送られない) すると使用停止
    response = logged_in.post(url, {"code": "CUT", "name": "作り物の切断", "sort_order": "1"})
    assert response.status_code == 302
    process.refresh_from_db()
    assert process.active is False
    assert logged_in.get(url).context["form"]["active"].value() is False
    detail = reverse("masters:process_detail", args=[process.pk])
    assert "使用停止の工程です。" in logged_in.get(detail).content.decode()

    logged_in.post(url, {"code": "CUT", "name": "作り物の切断", "sort_order": "1", "active": "on"})
    process.refresh_from_db()
    assert process.active is True
    assert "使用停止の工程です。" not in logged_in.get(detail).content.decode()


@pytest.mark.django_db
def test_active_processes_keeps_only_the_given_inactive_one(processes):
    cut, old, bend = processes

    assert list(masters_services.active_processes()) == [cut, bend]
    assert list(masters_services.active_processes(keep=old)) == [cut, old, bend]
    assert list(masters_services.active_processes(keep=old.pk)) == [cut, old, bend]
    assert Process._meta.get_field("active").editable is False


@pytest.mark.django_db
def test_issue_form_offers_active_processes_only(logged_in, processes, line):
    cut, old, bend = processes
    assert list(WorkOrderIssueForm().fields["processes"].queryset) == [cut, bend]

    # 使用停止の工程を送っても指示書を作らない (欄の誤り)
    response = logged_in.post(
        reverse("workorders:workorder_create"), {"order_line": line.pk, "qty": "4", "processes": [old.pk]}
    )
    assert response.status_code == 200
    assert response.context["form"].has_error("processes")
    assert not WorkOrder.objects.exists()


@pytest.mark.django_db
def test_issue_copies_standard_processes_without_inactive_ones(logged_in, user, processes, line):
    cut, old, bend = processes
    masters_services.set_item_processes(line.item, [bend, old, cut], user)

    response = logged_in.post(reverse("workorders:workorder_create"), {"order_line": line.pk, "qty": "4"})

    assert response.status_code == 302
    workorder = WorkOrder.objects.get()
    assert [(step.seq, step.process) for step in workorder.steps.order_by("seq")] == [(1, bend), (2, cut)]
    # 部品の標準の工程の並びの行は消さない (使用中に戻すとまた写る)
    assert [row.process for row in line.item.item_processes.order_by("seq")] == [bend, old, cut]


@pytest.mark.django_db
def test_existing_workorder_steps_stay_after_stop(logged_in, user, processes, line):
    cut, _old, _bend = processes
    workorder = workorders_services.issue_workorder(line, Decimal("4"), [{"process": cut, "planned_qty": Decimal("4")}], user)
    masters_services.update_process(cut, {"active": False}, user)

    # 今までの指示書の工程はそのまま見え、進捗の盤の工程の切り替え (今ある指示書の工程から作る) にも出る
    assert [step.process for step in workorder.steps.all()] == [cut]
    assert "作り物の切断" in logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk])).content.decode()
    assert ">作り物の切断</a>" in logged_in.get(reverse("progress:progress_list")).content.decode()
