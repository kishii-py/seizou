"""R07: 進捗の実績の数が、その工程の予定の数を超えたら (同梱のポカよけ。R06 と同じ設定 over_workorder)。

検査の層 (seizou.core.checks) を 3 つの値で回す。services は止めない (現物優先)。
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model

from seizou.core import checks
from seizou.core.checks import CheckWarning
from seizou.core.exceptions import CheckViolation
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
ACTION = "progress.record_progress"


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


@pytest.fixture
def recorded_eight(user, step):
    return services.record_progress(step, Decimal("8"), RECORDED_AT, user)


@pytest.mark.django_db
def test_r07_stop_raises_check_violation(settings, user, step, recorded_eight):
    settings.SEIZOU = {"over_workorder": "stop"}
    with pytest.raises(CheckViolation) as caught:
        checks.run(ACTION, {"workorder_step": step, "qty": Decimal("3")}, user)
    assert caught.value.code == "R07"
    assert "実績の合計 (11" in caught.value.message


@pytest.mark.django_db
def test_r07_warn_returns_warning(settings, user, step, recorded_eight):
    settings.SEIZOU = {"over_workorder": "warn"}
    warnings = checks.run(ACTION, {"workorder_step": step, "qty": Decimal("3")}, user)
    assert len(warnings) == 1
    assert isinstance(warnings[0], CheckWarning)
    assert warnings[0].code == "R07"


@pytest.mark.django_db
def test_r07_off_is_silent(settings, user, step, recorded_eight):
    settings.SEIZOU = {"over_workorder": "off"}
    assert checks.run(ACTION, {"workorder_step": step, "qty": Decimal("3")}, user) == []


@pytest.mark.django_db
def test_services_record_over_plan(user, step, recorded_eight):
    over = services.record_progress(step, Decimal("3"), RECORDED_AT, user)
    assert over.qty == Decimal("3")
    assert services.recorded_qty(step) == Decimal("11")
