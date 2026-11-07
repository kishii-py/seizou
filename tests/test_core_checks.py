"""検査の層 (seizou.core.checks) の約束。"""

import pytest

from seizou.core import checks
from seizou.core.exceptions import CheckViolation, RuleError


class _Always(checks.Check):
    name = "T01"
    action = "tests.action"

    def __init__(self, mode):
        self.mode = mode

    def run(self, data, user):
        return "テストの知らせ"


class _Never(checks.Check):
    name = "T02"
    action = "tests.action"

    def run(self, data, user):
        return None


@pytest.fixture(autouse=True)
def _clean_registry():
    checks.clear("tests.action")
    yield
    checks.clear("tests.action")


def test_warn_returns_warning_and_does_not_raise():
    checks.register(_Always("warn"))
    checks.register(_Never())
    warnings = checks.run("tests.action", {}, user=None)
    assert [w.code for w in warnings] == ["T01"]
    assert warnings[0].message == "テストの知らせ"


def test_stop_raises_check_violation_which_is_a_rule_error():
    checks.register(_Always("stop"))
    with pytest.raises(CheckViolation) as info:
        checks.run("tests.action", {}, user=None)
    assert info.value.code == "T01"
    assert isinstance(info.value, RuleError)


def test_off_is_not_called():
    checks.register(_Always("off"))
    assert checks.run("tests.action", {}, user=None) == []


def test_register_replaces_same_name():
    checks.register(_Always("warn"))
    checks.register(_Always("off"))
    assert len(checks.registered("tests.action")) == 1
    assert checks.run("tests.action", {}, user=None) == []


def test_builtin_checks_follow_company_setting(settings):
    from seizou.core.checks import builtin

    settings.SEIZOU = {"over_shipment": "stop", "over_workorder": "off", "over_payment": "stop"}
    modes = {c.name: c.current_mode() for c in builtin.BUILTIN}
    assert modes == {"R01": "stop", "R04": "stop", "R06": "off", "R07": "off", "R24": "stop"}


def test_builtin_default_mode_is_warn(settings):
    from seizou.core.checks import builtin

    settings.SEIZOU = {}
    assert {c.current_mode() for c in builtin.BUILTIN} == {"warn"}


def test_r06_skips_cancelled_order_line(monkeypatch):
    """取消の受注の明細は R06 (数の超え) を見ない。service の R05 (取消した受注には作れない) が先に赤で止める (11-04。精査 A7 の 3 節の 5)。"""
    from decimal import Decimal
    from types import SimpleNamespace

    from seizou.core.checks import builtin, tools

    monkeypatch.setattr(tools, "issued_qty", lambda line: Decimal("150"))
    check = builtin.OverWorkorder()
    cancelled = SimpleNamespace(qty=Decimal("150"), order=SimpleNamespace(status="cancelled"))
    assert check.run({"order_line": cancelled, "qty": "1"}, user=None) is None
    open_line = SimpleNamespace(qty=Decimal("150"), order=SimpleNamespace(status="open"))
    assert "超えます" in (check.run({"order_line": open_line, "qty": "1"}, user=None) or "")
