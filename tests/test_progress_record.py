"""実績を記録する関数 (01 の 10.4 の record_progress) と、工程ごとの実績の合計 (recorded_qty)。

予定より多い実績のポカよけは tests/test_r07_over_progress.py が見る。
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from seizou.core.checks import tools
from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services
from seizou.progress.models import Progress
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 30, tzinfo=ZoneInfo("Asia/Tokyo"))


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def step(user):
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user
    )
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    process = Process.objects.create(code="CUT", name="切断")
    workorder = workorders_services.issue_workorder(
        order.lines.get(), Decimal("10"), [{"process": process, "planned_qty": Decimal("10")}], user
    )
    return workorder.steps.get()


@pytest.mark.django_db
def test_record_sets_user_and_time(user, step):
    record = services.record_progress(step, Decimal("4"), RECORDED_AT, user, note="作り物の備考")
    assert isinstance(record, Progress)
    record.refresh_from_db()
    assert record.workorder_step == step
    assert record.qty == Decimal("4")
    assert record.recorded_at == RECORDED_AT
    assert record.recorded_by == user
    assert record.note == "作り物の備考"

    without_user = services.record_progress(step, 1, RECORDED_AT, None)
    without_user.refresh_from_db()
    assert without_user.recorded_by is None
    assert without_user.note == ""


@pytest.mark.django_db
def test_zero_qty_is_r02(user, step):
    for qty in (Decimal("0"), Decimal("-1")):
        with pytest.raises(RuleViolation) as caught:
            services.record_progress(step, qty, RECORDED_AT, user)
        assert caught.value.code == "R02"
        assert caught.value.message.startswith("保存できません。数量と単価は 0 より大きい。")
    assert Progress.objects.count() == 0

    # services を通さずに表へ直接書いても、DB の制約が止める。
    with pytest.raises(IntegrityError), transaction.atomic():
        Progress.objects.create(workorder_step=step, qty=0, recorded_at=RECORDED_AT)


@pytest.mark.django_db
def test_recorded_qty_sums(user, step):
    assert services.recorded_qty(step) == Decimal("0")
    services.record_progress(step, Decimal("4"), RECORDED_AT, user)
    services.record_progress(step, Decimal("2.5"), RECORDED_AT, user)
    assert services.recorded_qty(step) == Decimal("6.5")


@pytest.mark.django_db
def test_tools_recorded_qty_matches(user, step):
    assert tools.recorded_qty(step) == services.recorded_qty(step) == Decimal("0")
    services.record_progress(step, Decimal("3"), RECORDED_AT, user)
    assert tools.recorded_qty(step) == services.recorded_qty(step) == Decimal("3")
