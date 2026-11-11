"""R30: 不良の区分の一覧に使用中の区分がある会社で、区分を選んでいない不良のある実績を入れたら (標準のポカよけ。会社の設定 progress_defect_kind)。

検査の層 (seizou.core.checks) を設定の 3 つの値 (stop・warn・off) で回し、services の record_progress を直に呼んで保存を見る
(実績の入力の画面に不良の行の欄が入るのは 2 段目の N407b。今の画面は不良の数 (defect_qty) だけを渡す)。
区分が 0 件の会社・使用停止の区分しか無い会社では何もしない。実績の編集 (progress.update_progress) には掛けない。
社員は作らない (同じ action の R26 を出さない)。良品の数は予定 (10) より少なくし、R07 を出さない。
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
from seizou.progress.models import Progress, ProgressDefect
from seizou.workorders import services as workorders_services
from tests.rules_helpers import rules_md_text

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
ACTION = "progress.record_progress"
R30_HEAD = "区分を選んでいない不良が 3 あります。"
R30_WARN = R30_HEAD + "不良の集計では「未設定」の行に入ります。このまま保存できます。"
R30_STOP = "保存できません。" + R30_HEAD + "不良の行の区分を選んでください。"


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
def scratch(user):
    return masters_services.create_defect_kind({"name": "作り物の傷"}, user)


def _codes(warnings):
    return [warning.code for warning in warnings]


def _data(step, **values):
    # 良品の数は予定 (10) より少なくし、同じ action の R07 (予定より多い実績) を出さない
    return {"workorder_step": step, "qty": Decimal("1"), **values}


def _mixed(kind):
    """区分のある行 (数 2) と区分の無い行 (数 3) の不良の行の並び。区分を選んでいない不良は 3。"""
    return [{"kind": kind, "qty": Decimal("2")}, {"kind": None, "qty": Decimal("3")}]


def test_r30_row_in_rules_md_and_setting_key():
    assert "区分を選んでいない不良" in rules_md_text("R30")
    assert checks.setting_key_for("R30") == "progress_defect_kind"


def test_r30_is_silent_without_kinds(settings, user, step):
    """(a) 区分が 0 件の会社では、区分の無い不良の行と、不良の数だけの実績を既定の設定で入れても、知らせ無しで保存される。"""
    settings.SEIZOU = {}
    lines = [{"kind": None, "qty": Decimal("3")}]
    assert checks.run(ACTION, _data(step, defects=lines), user) == []
    assert checks.run(ACTION, _data(step, defect_qty=Decimal("3")), user) == []
    services.record_progress(step, Decimal("1"), RECORDED_AT, user, defects=lines)
    services.record_progress(step, Decimal("1"), RECORDED_AT, user, defect_qty=Decimal("3"))
    assert Progress.objects.count() == 2
    assert ProgressDefect.objects.count() == 2


def test_r30_warns_on_line_without_kind(settings, user, step, scratch):
    """(b) 使用中の区分が 1 つあると、区分の無い行 (数 3) のある実績に R30 の黄色の知らせが出る。「このまま保存」で保存される。"""
    settings.SEIZOU = {}
    warnings = checks.run(ACTION, _data(step, defects=_mixed(scratch)), user)
    assert _codes(warnings) == ["R30"]
    assert warnings[0].message == R30_WARN
    # 「このまま保存」= 土台は検査を飛ばして services を呼ぶ
    saved = services.record_progress(step, Decimal("1"), RECORDED_AT, user, defects=_mixed(scratch))
    assert Progress.objects.get() == saved
    assert saved.defect_qty == Decimal("5")


def test_r30_warns_on_defect_qty_only(settings, user, step, scratch):
    """(b) 不良の行を渡さず不良の数だけを渡した実績 (今の画面の入力) にも、同じ知らせが出る (区分の無い 1 行になるため)。"""
    settings.SEIZOU = {}
    warnings = checks.run(ACTION, _data(step, defect_qty=Decimal("3")), user)
    assert _codes(warnings) == ["R30"]
    assert warnings[0].message == R30_WARN
    # defects の鍵が None のときも、渡していないのと同じに見る
    warnings = checks.run(ACTION, _data(step, defects=None, defect_qty=Decimal("3")), user)
    assert [(warning.code, warning.message) for warning in warnings] == [("R30", R30_WARN)]
    saved = services.record_progress(step, Decimal("1"), RECORDED_AT, user, defect_qty=Decimal("3"))
    line = saved.defects.get()
    assert (line.kind_id, line.qty) == (None, Decimal("3"))


def test_r30_stop_refuses(settings, user, step, scratch):
    """(c) progress_defect_kind が stop なら赤の知らせ (保存できません。…) で止まり、実績は増えない。"""
    settings.SEIZOU = {"progress_defect_kind": "stop"}
    with pytest.raises(CheckViolation) as caught:
        checks.run(ACTION, _data(step, defects=_mixed(scratch)), user)
    assert caught.value.code == "R30"
    assert caught.value.message == R30_STOP
    with pytest.raises(CheckViolation) as caught:
        checks.run(ACTION, _data(step, defect_qty=Decimal("3")), user)
    assert caught.value.message == R30_STOP
    assert not Progress.objects.exists()


def test_r30_off_is_silent(settings, user, step, scratch):
    """(c) progress_defect_kind が off なら知らせ無しで保存される。"""
    settings.SEIZOU = {"progress_defect_kind": "off"}
    assert checks.run(ACTION, _data(step, defects=_mixed(scratch)), user) == []
    assert checks.run(ACTION, _data(step, defect_qty=Decimal("3")), user) == []
    services.record_progress(step, Decimal("1"), RECORDED_AT, user, defects=_mixed(scratch))
    assert Progress.objects.count() == 1


@pytest.mark.parametrize("mode", ["warn", "stop"])
def test_r30_passes_with_kinds_or_without_defects(settings, user, step, scratch, mode):
    """(d) 全部の行に区分がある実績と、不良が 0 の実績には出ない。"""
    settings.SEIZOU = {"progress_defect_kind": mode}
    other = masters_services.create_defect_kind({"name": "作り物の寸法"}, user)
    lines = [{"kind": scratch, "qty": Decimal("2")}, {"kind": other, "qty": Decimal("1")}]
    assert checks.run(ACTION, _data(step, defects=lines), user) == []
    assert checks.run(ACTION, _data(step), user) == []
    assert checks.run(ACTION, _data(step, defect_qty=Decimal("0")), user) == []
    assert checks.run(ACTION, _data(step, defects=[]), user) == []


def test_r30_is_silent_with_only_inactive_kind(settings, user, step, scratch):
    """(e) 区分が使用停止の 1 つだけなら出ない。"""
    settings.SEIZOU = {"progress_defect_kind": "stop"}
    masters_services.update_defect_kind(scratch, {"active": False}, user)
    assert checks.run(ACTION, _data(step, defects=_mixed(scratch)), user) == []
    assert checks.run(ACTION, _data(step, defect_qty=Decimal("3")), user) == []


def test_r30_is_not_on_update_progress(settings, user, step, scratch):
    """(f) 実績の編集 (progress.update_progress) には R30 が掛からない (区分を作る前に入れた実績を直すたびに知らせが出ないため)。"""
    settings.SEIZOU = {"progress_defect_kind": "stop"}
    progress = services.record_progress(step, Decimal("1"), RECORDED_AT, user, defect_qty=Decimal("3"))
    assert "R30" not in [check.name for check in checks.registered("progress.update_progress")]
    data = {"object": progress, "qty": Decimal("2"), "defect_qty": Decimal("4")}
    assert checks.run("progress.update_progress", data, user) == []
    services.update_progress(progress, Decimal("2"), RECORDED_AT, user, defect_qty=Decimal("4"))
    progress.refresh_from_db()
    assert progress.defect_qty == Decimal("4")
