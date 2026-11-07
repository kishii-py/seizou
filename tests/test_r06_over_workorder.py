"""R06: 指示書の数量の合計が、受注の明細の数を超えたら (標準のポカよけ。設定 over_workorder)。

検査の層 (seizou.core.checks) を 3 つの値で回す。services は止めない (現物優先)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.core import checks
from seizou.core.checks import CheckWarning
from seizou.core.exceptions import CheckViolation
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.workorders import services

ORDER_DATE = date(2026, 11, 2)
ACTION = "workorders.issue_workorder"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def process(db):
    return Process.objects.create(code="CUT", name="切断")


@pytest.fixture
def line(user):
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
    return order.lines.get()


@pytest.fixture
def issued_six(user, line, process):
    return services.issue_workorder(
        line, Decimal("6"), [{"process": process, "planned_qty": Decimal("6")}], user
    )


@pytest.mark.django_db
def test_r06_stop_raises_check_violation(settings, user, line, issued_six):
    settings.SEIZOU = {"over_workorder": "stop"}
    with pytest.raises(CheckViolation) as caught:
        checks.run(ACTION, {"order_line": line, "qty": Decimal("5")}, user)
    assert caught.value.code == "R06"
    assert "指示書の数量の合計 (11" in caught.value.message


@pytest.mark.django_db
def test_r06_warn_returns_warning(settings, user, line, issued_six):
    settings.SEIZOU = {"over_workorder": "warn"}
    warnings = checks.run(ACTION, {"order_line": line, "qty": Decimal("5")}, user)
    assert len(warnings) == 1
    assert isinstance(warnings[0], CheckWarning)
    assert warnings[0].code == "R06"


@pytest.mark.django_db
def test_r06_off_is_silent(settings, user, line, issued_six):
    settings.SEIZOU = {"over_workorder": "off"}
    assert checks.run(ACTION, {"order_line": line, "qty": Decimal("5")}, user) == []


@pytest.mark.django_db
def test_r06_at_limit_is_silent(settings, user, line, issued_six):
    for mode in ("stop", "warn"):
        settings.SEIZOU = {"over_workorder": mode}
        assert checks.run(ACTION, {"order_line": line, "qty": Decimal("4")}, user) == []


@pytest.mark.django_db
def test_services_do_not_stop_r06(user, line, process, issued_six):
    over = services.issue_workorder(
        line, Decimal("5"), [{"process": process, "planned_qty": Decimal("5")}], user
    )
    assert over.qty == Decimal("5")
    assert services.issued_qty(line) == Decimal("11")
