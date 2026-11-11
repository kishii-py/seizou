"""R26: 社員の一覧に使用中の社員が居る会社で、作業した人の無い実績を入れたら (標準のポカよけ。会社の設定 progress_staff)。

検査の層 (seizou.core.checks) を設定の 3 つの値 (stop・warn・off) で回し、services の record_progress を直に呼んで保存を見る
(実績の新規登録の画面に作業した人の欄が入るのは 2 段目の N401b。今の画面はこの欄を渡さない)。
社員が 0 人の会社・使用停止の社員しか居ない会社では何もしない。実績の編集 (progress.update_progress) には掛けない。
作り物のデータは全部 services で作る (工程の行だけは R07 の試験と同じく表に直に作る)。
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model

from seizou.core import checks
from seizou.core.exceptions import CheckViolation
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services
from seizou.progress.models import Progress
from seizou.workorders import services as workorders_services
from tests.rules_helpers import rules_md_text

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
ACTION = "progress.record_progress"
R26_MESSAGE = "作業した人が入っていません。社員番号か、社員の一覧に居ない人の名前を入れてください。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def step(user):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
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
def staff(user):
    return masters_services.create_staff({"code": "0012", "name": "作り物の太郎"}, user)


def _codes(warnings):
    return [warning.code for warning in warnings]


def _data(step, **values):
    # 数は予定 (10) より少なくし、同じ action の R07 (予定より多い実績) を出さない
    return {"workorder_step": step, "qty": Decimal("1"), **values}


def test_r26_row_in_rules_md_and_setting_key():
    assert "作業した人" in rules_md_text("R26")
    assert checks.setting_key_for("R26") == "progress_staff"


def test_r26_is_silent_without_staff(settings, user, step):
    """(a) 社員が 0 人の会社では、作業した人の無い実績を既定の設定で入れると、知らせ無しで保存される。"""
    settings.SEIZOU = {}
    assert checks.run(ACTION, _data(step), user) == []
    services.record_progress(step, Decimal("1"), RECORDED_AT, user)
    assert Progress.objects.count() == 1


def test_r26_warns_when_staff_exists(settings, user, step, staff):
    """(b) 使用中の社員が 1 人居ると、作業した人の無い実績に R26 の黄色の知らせが出る。「このまま保存」で保存される。"""
    settings.SEIZOU = {}
    warnings = checks.run(ACTION, _data(step), user)
    assert _codes(warnings) == ["R26"]
    assert warnings[0].message == R26_MESSAGE
    # 「このまま保存」= 土台は検査を飛ばして services を呼ぶ
    saved = services.record_progress(step, Decimal("1"), RECORDED_AT, user)
    assert Progress.objects.get() == saved
    assert saved.staff is None and saved.staff_name == ""


def test_r26_stop_refuses(settings, user, step, staff):
    """(c) progress_staff が stop なら赤の知らせ (保存できません。…) で止まる。"""
    settings.SEIZOU = {"progress_staff": "stop"}
    with pytest.raises(CheckViolation) as caught:
        checks.run(ACTION, _data(step), user)
    assert caught.value.code == "R26"
    assert caught.value.message == "保存できません。" + R26_MESSAGE


def test_r26_off_is_silent(settings, user, step, staff):
    """(c) progress_staff が off なら知らせ無しで保存される。"""
    settings.SEIZOU = {"progress_staff": "off"}
    assert checks.run(ACTION, _data(step), user) == []
    services.record_progress(step, Decimal("1"), RECORDED_AT, user)
    assert Progress.objects.count() == 1


@pytest.mark.parametrize("mode", ["warn", "stop"])
def test_r26_passes_with_staff_or_name(settings, user, step, staff, mode):
    """(d) 社員を渡した実績と、名前だけ (社員の一覧に居ない人) を渡した実績には知らせが出ない。"""
    settings.SEIZOU = {"progress_staff": mode}
    assert checks.run(ACTION, _data(step, staff=staff), user) == []
    assert checks.run(ACTION, _data(step, staff_name="応援の作り物の花子"), user) == []
    assert checks.run(ACTION, _data(step, staff=None, staff_name="応援の作り物の花子"), user) == []


def test_r26_blank_name_counts_as_empty(settings, user, step, staff):
    """(d) 名前が空白だけなら知らせが出る (全角の空白も空と見る)。"""
    settings.SEIZOU = {}
    assert _codes(checks.run(ACTION, _data(step, staff_name="   "), user)) == ["R26"]
    assert _codes(checks.run(ACTION, _data(step, staff=None, staff_name="　"), user)) == ["R26"]
    assert _codes(checks.run(ACTION, _data(step, staff=None, staff_name=""), user)) == ["R26"]


def test_r26_is_silent_with_only_inactive_staff(settings, user, step, staff):
    """(e) 社員が使用停止の 1 人だけなら知らせが出ない。"""
    settings.SEIZOU = {}
    masters_services.update_staff(staff, {"active": False}, user)
    assert checks.run(ACTION, _data(step), user) == []


def test_r26_is_not_on_update_progress(settings, user, step, staff):
    """(f) 実績の編集 (progress.update_progress) には R26 が掛からない (社員を登録する前の実績を直すたびに知らせが出ないため)。"""
    settings.SEIZOU = {"progress_staff": "stop"}
    progress = services.record_progress(step, Decimal("1"), RECORDED_AT, user)
    assert "R26" not in [check.name for check in checks.registered("progress.update_progress")]
    assert checks.run("progress.update_progress", {"object": progress, "qty": Decimal("2")}, user) == []
