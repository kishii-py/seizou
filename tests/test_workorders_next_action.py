"""指示書の詳細の頭の右の緑のボタン = その伝票で次にする操作 (UX-2 a。11-06 22:1x。初見テスト R5 の候補 C93)。

未着手の指示書は今のまま緑の「着手」。着手済で、全部の工程の実績が予定の数に届き、まだ入庫にしていない完成の数がある指示書は、
「着手済」の印の右に緑の「製造の完了を入庫にする」(入庫の画面の全体へ移る。?workorder= で指示書と残りの数が入る)。
部署の判定が on のとき、部署で在庫を開けない人には出さない。判定は workorders.plans.finished_due。
作り物のデータは services で作る (受注は tests/test_detail_columns.py の物を借りる)。
"""

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.progress import services as progress_services
from seizou.stock import services as stock_services
from seizou.workorders import plans
from seizou.workorders import services as workorders_services
from seizou.workorders.models import WorkOrder
from tests import test_detail_columns
from tests.test_core_access import account
from tests.test_core_department_buttons import _departments
from tests.test_detail_header import _actions

# 受注と発注の詳細の 2 列の試験の作り物を借りる (pytest は module の名前で fixture を見つける)。
customer = test_detail_columns.customer
item = test_detail_columns.item
order = test_detail_columns.order

ALL_ON = {"department": "on", "write": "on", "money": "on"}


@pytest.fixture
def processes(user):
    cut = masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)
    bend = masters_services.create_process({"code": "BEND", "name": "曲げ", "sort_order": 2}, user)
    return cut, bend


@pytest.fixture
def workorder(user, order, processes):
    """発行 (未着手) の指示書。受注の 1 行目から 10 個、工程 2 つ (予定は 10 ずつ)。"""
    line = order.lines.order_by("line_no").first()
    steps = [{"process": process, "planned_qty": Decimal("10")} for process in processes]
    return workorders_services.issue_workorder(line, Decimal("10"), steps, user)


def _record_all(workorder, user, qty="10"):
    for step in workorder.steps.order_by("seq"):
        progress_services.record_progress(step, Decimal(qty), timezone.now(), user)


def _head(client, workorder):
    response = client.get(reverse("workorders:workorder_detail", args=[workorder.pk]))
    assert response.status_code == 200
    return _actions(response.content.decode())


def _finished_link(workorder):
    detail = reverse("workorders:workorder_detail", args=[workorder.pk])
    return f'{reverse("stock:finished_create")}?workorder={workorder.pk}&amp;next={detail}'


def _due(workorder):
    return plans.finished_due(WorkOrder.objects.get(pk=workorder.pk))


def _assert_finished_green(head, workorder):
    green = f'<a class="btn btn-primary" href="{_finished_link(workorder)}">'
    assert green in head and "製造の完了を入庫にする" in head
    # 「着手済」の印の右
    assert head.index("着手済") < head.index(green)
    assert head.count("btn-primary") == 1


@pytest.mark.django_db
def test_all_steps_done_shows_green_finished(user, logged_in, workorder):
    workorders_services.accept_workorder(workorder, user)
    _record_all(workorder, user)

    _assert_finished_green(_head(logged_in, workorder), workorder)
    assert _due(workorder) is True


@pytest.mark.django_db
def test_no_green_until_every_step_reaches_plan(user, logged_in, workorder):
    workorders_services.accept_workorder(workorder, user)
    first = workorder.steps.order_by("seq").first()
    progress_services.record_progress(first, Decimal("10"), timezone.now(), user)

    head = _head(logged_in, workorder)

    assert "製造の完了を入庫にする" not in head
    assert "btn-primary" not in head
    assert _due(workorder) is False


@pytest.mark.django_db
def test_green_follows_stocked_rest(user, logged_in, workorder):
    workorders_services.accept_workorder(workorder, user)
    _record_all(workorder, user)

    # 4 だけ入庫にした後は、残り 6 があるので出る。開いた入力の画面の数量は残りの 6
    stock_services.record_finished(WorkOrder.objects.get(pk=workorder.pk), Decimal("4"), user)
    _assert_finished_green(_head(logged_in, workorder), workorder)
    assert _due(workorder) is True
    form = logged_in.get(_finished_link(workorder).replace("&amp;", "&")).context["form"]
    assert form.initial["qty"] == Decimal("6")

    # 残りを全部入庫にした後は出ない
    stock_services.record_finished(WorkOrder.objects.get(pk=workorder.pk), Decimal("6"), user)
    head = _head(logged_in, workorder)
    assert "製造の完了を入庫にする" not in head
    assert "btn-primary" not in head
    assert _due(workorder) is False


@pytest.mark.django_db
def test_head_link_prefills_rest(user, logged_in, workorder):
    workorders_services.accept_workorder(workorder, user)
    _record_all(workorder, user)

    response = logged_in.get(_finished_link(workorder).replace("&amp;", "&"))

    assert response.status_code == 200
    assert response.context["form"].initial["qty"] == Decimal("10")


@pytest.mark.django_db
def test_unaccepted_workorder_keeps_accept_only(user, logged_in, workorder):
    _record_all(workorder, user)

    head = _head(logged_in, workorder)

    assert "着手</button>" in head
    assert "製造の完了を入庫にする" not in head
    assert head.count("btn-primary") == 1


@pytest.mark.django_db
def test_cancelled_workorder_has_neither(user, logged_in, workorder):
    workorders_services.cancel_workorder(workorder, user)

    head = _head(logged_in, workorder)

    assert "着手" not in head
    assert "製造の完了を入庫にする" not in head
    assert "btn-primary" not in head
    assert _due(workorder) is False


@pytest.mark.django_db
def test_finished_button_follows_stock_department(settings, client, user, workorder):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    workorders_services.accept_workorder(workorder, user)
    _record_all(workorder, user)
    depts = _departments()

    # 経理の部署 (在庫を開けない) の人: 詳細は開けるが出さない
    client.force_login(account("keiri", departments=[depts["keiri"]], permissions=["billing"], see_money=True))
    head = _head(client, workorder)
    assert "製造の完了を入庫にする" not in head
    assert "btn-primary" not in head

    # 製造の部署 (在庫を開ける) の人: 出る
    client.force_login(account("seizo", departments=[depts["seizo"]]))
    _assert_finished_green(_head(client, workorder), workorder)
