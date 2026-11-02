"""指示書を発行する関数 (01 の 10.4 の issue_workorder) と、指示書の数量の合計 (issued_qty)。

R02・R05 の指示書の側のテストもここに置く (受注の側は tests/test_r02_*・tests/test_r05_*)。
受注より多い指示書のポカよけは tests/test_r06_over_workorder.py が見る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from seizou.core.checks import tools
from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.workorders import services
from seizou.workorders.models import WorkOrder, WorkOrderStep

ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


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
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
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


@pytest.mark.django_db
def test_issue_with_two_steps(user, line, cut, bend):
    workorder = services.issue_workorder(
        line,
        Decimal("4"),
        [
            {"process": bend, "planned_qty": Decimal("4")},
            {"process": cut, "planned_qty": 5},
        ],
        user,
    )
    assert isinstance(workorder, WorkOrder)
    workorder.refresh_from_db()
    assert workorder.wo_no.startswith("WO-")
    assert workorder.order_line == line
    assert workorder.qty == Decimal("4")
    assert workorder.status == WorkOrder.STATUS_ISSUED
    steps = list(workorder.steps.all())
    assert [(s.seq, s.process, s.planned_qty) for s in steps] == [
        (1, bend, Decimal("4")),
        (2, cut, Decimal("5")),
    ]
    assert str(steps[0]) == f"{workorder.wo_no} 1 曲げ"

    second = services.issue_workorder(line, 1, [{"process": cut, "planned_qty": 1}], user)
    assert second.wo_no != workorder.wo_no


@pytest.mark.django_db
def test_cancelled_order_is_r05(user, order, line, cut):
    orders_services.cancel_order(order, user)
    with pytest.raises(RuleViolation) as caught:
        services.issue_workorder(line, Decimal("1"), [{"process": cut, "planned_qty": 1}], user)
    assert caught.value.code == "R05"
    assert caught.value.message == (
        "取消した受注には、出荷も指示書も作れない。取消していない受注の明細を選んでください。"
    )
    assert WorkOrder.objects.count() == 0


@pytest.mark.django_db
def test_zero_qty_is_r02(user, line, cut):
    for qty in (Decimal("0"), Decimal("-1")):
        with pytest.raises(RuleViolation) as caught:
            services.issue_workorder(line, qty, [{"process": cut, "planned_qty": 1}], user)
        assert caught.value.code == "R02"
        assert caught.value.message.startswith("数量と単価は 0 より大きい。")
    assert WorkOrder.objects.count() == 0


@pytest.mark.django_db
def test_zero_planned_qty_is_r02(user, line, cut, bend):
    with pytest.raises(RuleViolation) as caught:
        services.issue_workorder(
            line,
            Decimal("2"),
            [{"process": cut, "planned_qty": 2}, {"process": bend, "planned_qty": 0}],
            user,
        )
    assert caught.value.code == "R02"
    assert WorkOrder.objects.count() == 0
    assert WorkOrderStep.objects.count() == 0


@pytest.mark.django_db
def test_empty_steps_is_value_error(user, line, cut):
    with pytest.raises(ValueError, match="工程がありません。工程を 1 つ以上選んでください。"):
        services.issue_workorder(line, Decimal("2"), [], user)
    with pytest.raises(ValueError, match="知らない項目"):
        services.issue_workorder(
            line, Decimal("2"), [{"process": cut, "planned_qty": 2, "seq": 9}], user
        )
    assert WorkOrder.objects.count() == 0


@pytest.mark.django_db
def test_db_constraint_backs_r02(user, line, cut):
    # services を通さずに表へ直接書いても、DB の制約が止める。
    with pytest.raises(IntegrityError), transaction.atomic():
        WorkOrder.objects.create(wo_no="WO-T1", order_line=line, qty=0, issued_on=ORDER_DATE)
    workorder = services.issue_workorder(line, 1, [{"process": cut, "planned_qty": 1}], user)
    with pytest.raises(IntegrityError), transaction.atomic():
        WorkOrderStep.objects.create(workorder=workorder, process=cut, seq=2, planned_qty=0)


@pytest.mark.django_db
def test_issued_qty_excludes_cancelled(user, line, cut):
    assert services.issued_qty(line) == Decimal("0")
    first = services.issue_workorder(line, Decimal("3"), [{"process": cut, "planned_qty": 3}], user)
    services.issue_workorder(line, Decimal("4"), [{"process": cut, "planned_qty": 4}], user)
    assert services.issued_qty(line) == Decimal("7")

    # 取消に進める関数はまだ無いので、試験の中で状態だけを書く。
    WorkOrder.objects.filter(pk=first.pk).update(status=WorkOrder.STATUS_CANCELLED)
    assert services.issued_qty(line) == Decimal("4")


@pytest.mark.django_db
def test_tools_issued_qty_matches(user, line, cut):
    assert tools.issued_qty(line) == services.issued_qty(line) == Decimal("0")
    services.issue_workorder(line, Decimal("2.5"), [{"process": cut, "planned_qty": 2}], user)
    assert tools.issued_qty(line) == services.issued_qty(line) == Decimal("2.5")
