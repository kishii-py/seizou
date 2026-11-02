"""生産計画 (02 の 4 節)。工程の予定に日を置く関数 plan_step と、工程の「計画に使う」の印。"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.workorders import services
from seizou.workorders.models import WorkOrderStep

ORDER_DATE = date(2026, 11, 2)
DUE_DATE = date(2026, 11, 20)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("planner", password="pass-for-tests-only")


@pytest.fixture
def order(user):
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user
    )
    return orders_services.create_order(
        customer,
        [
            {
                "item": item,
                "qty": Decimal("10"),
                "unit_price": Decimal("100"),
                "due_date": DUE_DATE,
            }
        ],
        user,
        order_date=ORDER_DATE,
    )


@pytest.fixture
def line(order):
    return order.lines.get()


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1, use_in_plan=True)


@pytest.fixture
def bend(db):
    return Process.objects.create(code="BEND", name="曲げ", sort_order=2)


@pytest.fixture
def workorder(user, line, cut, bend):
    return services.issue_workorder(
        line,
        Decimal("4"),
        [
            {"process": cut, "planned_qty": Decimal("4")},
            {"process": bend, "planned_qty": Decimal("4")},
        ],
        user,
    )


@pytest.fixture
def step(workorder, cut):
    return workorder.steps.get(process=cut)


def _dates(step):
    step.refresh_from_db()
    return step.planned_start, step.planned_end


# 1 段目: plan_step


@pytest.mark.django_db
def test_plan_step_sets_dates(user, step):
    assert _dates(step) == (None, None)
    result = services.plan_step(step, date(2026, 11, 5), date(2026, 11, 5), user)
    assert isinstance(result, WorkOrderStep)
    assert result.pk == step.pk
    assert _dates(step) == (date(2026, 11, 5), date(2026, 11, 5))


@pytest.mark.django_db
def test_plan_step_moves_dates(user, step):
    services.plan_step(step, date(2026, 11, 5), date(2026, 11, 6), user)
    services.plan_step(step, date(2026, 11, 9), date(2026, 11, 10), user)
    assert _dates(step) == (date(2026, 11, 9), date(2026, 11, 10))


@pytest.mark.django_db
def test_plan_step_clears_to_unplanned(user, step):
    services.plan_step(step, date(2026, 11, 5), date(2026, 11, 6), user)
    services.plan_step(step, None, None, user)
    assert _dates(step) == (None, None)


@pytest.mark.django_db
def test_plan_step_start_after_end_is_value_error(user, step):
    services.plan_step(step, date(2026, 11, 5), date(2026, 11, 6), user)
    with pytest.raises(ValueError) as caught:
        services.plan_step(step, date(2026, 11, 7), date(2026, 11, 6), user)
    assert str(caught.value) == (
        "開始日が終了日より後です。開始日を終了日と同じ日か前の日にしてください。"
    )
    assert _dates(step) == (date(2026, 11, 5), date(2026, 11, 6))


@pytest.mark.django_db
def test_plan_step_one_side_none_is_value_error(user, step):
    for start, end in ((date(2026, 11, 5), None), (None, date(2026, 11, 5))):
        with pytest.raises(ValueError) as caught:
            services.plan_step(step, start, end, user)
        assert str(caught.value) == "開始日と終了日は、両方を入れるか両方を空にしてください。"
    assert _dates(step) == (None, None)


@pytest.mark.django_db
def test_plan_step_cancelled_order_is_r05(user, order, step):
    services.plan_step(step, date(2026, 11, 5), date(2026, 11, 5), user)
    orders_services.cancel_order(order, user)
    with pytest.raises(RuleViolation) as caught:
        services.plan_step(step, date(2026, 11, 9), date(2026, 11, 9), user)
    assert caught.value.code == "R05"
    assert caught.value.message == (
        "取消した受注には、出荷も指示書も作れない。取消していない受注の指示書を選んでください。"
    )
    assert _dates(step) == (date(2026, 11, 5), date(2026, 11, 5))


@pytest.mark.django_db
def test_plan_step_does_not_touch_qty(user, workorder, step):
    services.plan_step(step, date(2026, 11, 5), date(2026, 11, 6), user)
    step.refresh_from_db()
    workorder.refresh_from_db()
    assert step.planned_qty == Decimal("4")
    assert workorder.qty == Decimal("4")


@pytest.mark.django_db
def test_create_process_accepts_use_in_plan(user):
    marked = masters_services.create_process(
        {"code": "PAINT", "name": "塗装", "sort_order": 3, "use_in_plan": True}, user
    )
    plain = masters_services.create_process({"code": "PACK", "name": "梱包"}, user)
    marked.refresh_from_db()
    plain.refresh_from_db()
    assert marked.use_in_plan is True
    assert plain.use_in_plan is False
