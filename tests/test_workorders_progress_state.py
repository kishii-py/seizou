"""指示書の進みの状態 (未着手・着手済・完了・取消) を実績から出す (初見テスト R5 の候補 C27 の 3〜4。C27-1 = a・WO-1 = b)。

決め方は WorkOrder.progress_state_of の 1 か所: 取消 → 完了 (最後の工程の実績の合計が予定の数以上) →
着手済 (どれかの工程に実績がある) → 未着手。状態は列に持たない。名前と品番は作り物だけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
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
