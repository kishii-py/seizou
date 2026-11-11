"""検査の層 (seizou.core.checks) の約束。"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.core import checks
from seizou.core.exceptions import CheckViolation, RuleError
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

SHIPPED_AT = date(2026, 11, 5)
#: 止めるの赤の知らせの頭 (保存の画面。1 文目は太字の span)
SAVE_HEAD = '<span class="font-semibold">保存できません。</span>'


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
    """R28 の行は、本人の決め 11-11 13:1x による読み替え (使用停止の社員を断らず、R28 で知らせる) で足した。"""
    from seizou.core.checks import builtin

    settings.SEIZOU = {"over_shipment": "stop", "over_workorder": "off", "over_payment": "stop"}
    modes = {c.name: c.current_mode() for c in builtin.BUILTIN}
    assert modes == {"R01": "stop", "R04": "stop", "R06": "off", "R07": "off", "R24": "stop", "R26": "warn", "R27": "warn", "R28": "warn", "R29": "warn", "R30": "warn"}


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


# --- 止めるの赤の頭と、黄の後のボタンの字 (本体の決め 11-11 15:0x。依頼書 N403b) ---
# 削除の画面は「削除できません。」と「このまま削除」、取消の画面は「取り消せません。」と「このまま取り消す」。
# 保存の画面の字は今のまま「保存できません。」と「このまま保存」(下で、出荷の R01 と入金の記録の R24 の画面の字が変わらない事を固める)。


def test_stop_head_defaults_to_save():
    """頭の字を渡さない呼び方は今のまま「保存できません。」。渡せばその字を頭に付ける。"""
    checks.register(_Always("stop"))
    with pytest.raises(CheckViolation) as info:
        checks.run("tests.action", {}, user=None)
    assert info.value.message == "保存できません。テストの知らせ"
    with pytest.raises(CheckViolation) as info:
        checks.run("tests.action", {}, user=None, stop_head="削除できません。")
    assert info.value.message == "削除できません。テストの知らせ"


def test_check_words_follow_service_name():
    """入力の土台は service の関数の名前で字を選ぶ。表 (REFUSAL_HEAD_BY_SERVICE) の「記録できません。」「発行できません。」は使わない。"""
    from seizou.core.views import BaseFormView

    def words(name):
        def service():
            return None

        service.__name__ = name
        view = BaseFormView()
        view.service = service
        return view.check_words()

    assert words("delete_shipment") == ("削除できません。", "このまま削除")
    assert words("remove_member") == ("削除できません。", "このまま削除")
    assert words("cancel_delivery_note") == ("取り消せません。", "このまま取り消す")
    for name in ("update_shipment", "ship", "record_payment", "create_invoice", "record_progress"):
        assert words(name) == ("保存できません。", "このまま保存")
    assert BaseFormView().check_words() == ("保存できません。", "このまま保存")


@pytest.fixture
def order_line(user):
    """数量 10・単価 1500 の明細 1 行の受注の明細 (作り物)。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}], user, order_date=SHIPPED_AT
    )
    return order.lines.get()


@pytest.mark.django_db
def test_r01_shipment_screen_keeps_save_words(settings, logged_in, order_line):
    """出荷の新規登録 (service = ship) の R01: 止めるの頭は「保存できません。」、知らせるの黄の後は「このまま保存」のまま。"""
    url = reverse("shipping:shipment_create")
    data = {"order_line": order_line.pk, "qty": "11", "shipped_at": SHIPPED_AT.isoformat(), "note": ""}

    settings.SEIZOU = {"over_shipment": "stop"}
    stopped = logged_in.post(url, data).content.decode()
    assert 'data-notice="stop"' in stopped and "R01" in stopped and SAVE_HEAD in stopped

    settings.SEIZOU = {"over_shipment": "warn"}
    warned = logged_in.post(url, data).content.decode()
    assert "R01" in warned and "このまま保存</button>" in warned
    assert "このまま削除" not in warned and "このまま取り消す" not in warned


@pytest.mark.django_db
def test_r24_payment_screen_keeps_save_words(settings, user, logged_in, order_line):
    """入金の記録 (service = record_payment。欄の誤りの頭は「記録できません。」) の R24: 止めるの頭は今のまま「保存できません。」、
    知らせるの黄の後は今のまま「このまま保存」(検査の層の頭は refusal_head の表を使わない)。"""
    shipment = shipping_services.ship(order_line, Decimal("10"), SHIPPED_AT, user)
    invoice = billing_services.create_invoice(order_line.order.customer, [shipment], user, issued_on=SHIPPED_AT)
    billing_services.record_payment(invoice, SHIPPED_AT, invoice.total, user)
    url = reverse("billing:payment_create", args=[invoice.pk])
    data = {"received_on": SHIPPED_AT.isoformat(), "amount": "1000", "method": "transfer", "note": ""}

    settings.SEIZOU = {"over_payment": "stop"}
    stopped = logged_in.post(url, data).content.decode()
    assert 'data-notice="stop"' in stopped and "R24" in stopped and SAVE_HEAD in stopped
    assert "記録できません。" not in stopped

    settings.SEIZOU = {"over_payment": "warn"}
    warned = logged_in.post(url, data).content.decode()
    assert "R24" in warned and "このまま保存</button>" in warned
