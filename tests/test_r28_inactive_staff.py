"""R28: 実績の作業した人に、使用停止の社員を入れたら (標準のポカよけ。会社の設定 progress_inactive_staff。本人の決め 11-11 13:1x)。

前は使用停止の社員の実績を services と入力の form で断っていた。運用の誤りを防ぐ物は、まず分かるようにして、止めるかは会社が選ぶ
(北極星のポカよけの考え方 11-11 12:5x)。既定は知らせる。直すときに同じ社員のままなら見ない (前の断りと同じ範囲)。
検査の層 (seizou.core.checks) を設定の 3 つの値 (stop・warn・off) で直に回す形と、実績の新規登録の画面に社員番号を入れて送る形の両方を見る。
作り物のデータは全部 services で作る (工程の行だけは R07 の試験と同じく表に直に作る)。名前は作り物だけ。
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

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
UPDATE_ACTION = "progress.update_progress"
WARN_TARO = "社員番号 0012 (作り物の太郎) は使用停止の社員です。番号に間違いが無ければ、このまま保存できます。"
STOP_TARO = (
    "保存できません。社員番号 0012 (作り物の太郎) は使用停止の社員です。"
    "使用中の社員の番号を入れるか、社員の編集で「使用中」に戻してください。"
)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


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
def taro(user):
    """使用停止の社員 (辞めた人)。"""
    staff = masters_services.create_staff({"code": "0012", "name": "作り物の太郎"}, user)
    masters_services.update_staff(staff, {"active": False}, user)
    staff.refresh_from_db()
    return staff


@pytest.fixture
def jiro(user):
    """使用中の社員 (居ると、実績の入力に社員番号の欄が出る)。"""
    return masters_services.create_staff({"code": "0034", "name": "作り物の次郎"}, user)


def _codes(warnings):
    return [warning.code for warning in warnings]


def _data(step, **values):
    # 数は予定 (10) より少なくし、同じ action の R07 (予定より多い実績) を出さない
    return {"workorder_step": step, "qty": Decimal("1"), **values}


def _post(client, step, **extra):
    data = {"workorder_step": step.pk, "qty": "4", "recorded_at": "2026-11-03T15:00", "note": "", **extra}
    return client.post(reverse("progress:progress_create"), data)


def test_r28_row_in_rules_md_and_setting_key():
    assert "使用停止の社員" in rules_md_text("R28")
    assert checks.setting_key_for("R28") == "progress_inactive_staff"
    assert "R28" in [check.name for check in checks.registered(ACTION)]
    assert "R28" in [check.name for check in checks.registered(UPDATE_ACTION)]


# ---------------------------------------------------------------- (a) 既定は知らせる


def test_r28_warns_by_default(settings, user, step, taro, jiro):
    """(a) 既定の設定で、使用停止の社員の実績に R28 の黄色の知らせ。「このまま保存」で保存され、実績は社員を指す。"""
    settings.SEIZOU = {}
    warnings = checks.run(ACTION, _data(step, staff=taro), user)
    assert _codes(warnings) == ["R28"]
    assert warnings[0].message == WARN_TARO
    # 「このまま保存」= 土台は検査を飛ばして services を呼ぶ
    saved = services.record_progress(step, Decimal("1"), RECORDED_AT, user, staff=taro)
    saved.refresh_from_db()
    assert saved.staff == taro and saved.staff_name == "" and saved.staff_code == ""


def test_r28_warns_on_create_screen_then_saves(settings, client_logged_in, step, taro, jiro):
    """(a) 実績の新規登録の画面に使用停止の社員番号を入れて送ると、黄色の知らせと「このまま保存」が出る。押すと保存される。"""
    settings.SEIZOU = {}
    first = _post(client_logged_in, step, staff_code="12")
    assert first.status_code == 200
    body = first.content.decode()
    assert 'data-notice="check"' in body and "R28" in body and "このまま保存" in body
    assert [warning.message for warning in first.context["warnings"]] == [WARN_TARO]
    assert first.context["form"].errors == {}
    assert not Progress.objects.exists()

    second = _post(client_logged_in, step, staff_code="12", confirmed="1")
    assert second.status_code == 302
    record = Progress.objects.get()
    assert record.staff == taro and record.staff_name == "" and record.staff_code == ""


# ---------------------------------------------------------------- (b) 止める


def test_r28_stop_refuses(settings, user, step, taro, jiro):
    """(b) progress_inactive_staff が stop なら赤の知らせ (保存できません。…) で止まる。"""
    settings.SEIZOU = {"progress_inactive_staff": "stop"}
    with pytest.raises(CheckViolation) as caught:
        checks.run(ACTION, _data(step, staff=taro), user)
    assert caught.value.code == "R28"
    assert caught.value.message == STOP_TARO


def test_r28_stop_on_create_screen(settings, client_logged_in, step, taro, jiro):
    """(b) 止めるの会社では、画面から送っても (「このまま保存」の印を付けても) 保存されず、実績は増えない。"""
    settings.SEIZOU = {"progress_inactive_staff": "stop"}
    response = _post(client_logged_in, step, staff_code="12", confirmed="1")
    assert response.status_code == 200
    assert 'data-notice="stop"' in response.content.decode()
    assert response.context["notice"].message == STOP_TARO
    assert not Progress.objects.exists()


# ---------------------------------------------------------------- (c) 通す


def test_r28_off_is_silent(settings, client_logged_in, user, step, taro, jiro):
    """(c) progress_inactive_staff が off なら知らせ無しで保存される。"""
    settings.SEIZOU = {"progress_inactive_staff": "off"}
    assert checks.run(ACTION, _data(step, staff=taro), user) == []
    response = _post(client_logged_in, step, staff_code="12")
    assert response.status_code == 302
    assert Progress.objects.get().staff == taro


# ---------------------------------------------------------------- (d) 使用停止の社員でない実績


@pytest.mark.parametrize("mode", ["warn", "stop"])
def test_r28_passes_other_workers(settings, user, step, taro, jiro, mode):
    """(d) 使用中の社員・名前だけ (社員の一覧に居ない人)・作業した人の空の実績には R28 が出ない (空は R26 が見る)。"""
    settings.SEIZOU = {"progress_inactive_staff": mode, "progress_staff": "warn"}
    assert checks.run(ACTION, _data(step, staff=jiro), user) == []
    assert checks.run(ACTION, _data(step, staff=None, staff_name="応援の作り物の花子"), user) == []
    assert _codes(checks.run(ACTION, _data(step, staff=None, staff_name=""), user)) == ["R26"]
    assert _codes(checks.run(ACTION, _data(step), user)) == ["R26"]


# ---------------------------------------------------------------- (e) 実績の編集


@pytest.mark.parametrize("mode", ["warn", "stop"])
def test_r28_update_same_inactive_staff_is_silent(settings, user, step, jiro, mode):
    """(e) 実績の編集で、同じ使用停止の社員のままなら R28 が出ない (前の断りと同じ範囲)。"""
    settings.SEIZOU = {"progress_inactive_staff": mode}
    progress = services.record_progress(step, Decimal("1"), RECORDED_AT, user, staff=jiro)
    masters_services.update_staff(jiro, {"active": False}, user)
    jiro.refresh_from_db()
    assert checks.run(UPDATE_ACTION, {"object": progress, "qty": Decimal("2"), "staff": jiro}, user) == []


def test_r28_update_to_other_inactive_staff_warns(settings, user, step, taro, jiro):
    """(e) 実績の編集で、別の使用停止の社員に直すと R28 が出る (止めるの会社では止まる)。"""
    progress = services.record_progress(step, Decimal("1"), RECORDED_AT, user, staff=jiro)
    data = {"object": progress, "qty": Decimal("2"), "staff": taro}
    settings.SEIZOU = {}
    warnings = checks.run(UPDATE_ACTION, data, user)
    assert _codes(warnings) == ["R28"]
    assert warnings[0].message == WARN_TARO
    settings.SEIZOU = {"progress_inactive_staff": "stop"}
    with pytest.raises(CheckViolation) as caught:
        checks.run(UPDATE_ACTION, data, user)
    assert caught.value.code == "R28" and caught.value.message == STOP_TARO
    # 名前だけの実績を、使用停止の社員に直すときも出る
    helper = services.record_progress(step, Decimal("1"), RECORDED_AT, user, staff_name="応援の作り物の花子")
    settings.SEIZOU = {}
    assert _codes(checks.run(UPDATE_ACTION, {"object": helper, "qty": Decimal("2"), "staff": taro}, user)) == ["R28"]


@pytest.mark.parametrize("mode", ["warn", "stop"])
def test_r28_update_without_staff_is_silent(settings, user, step, taro, jiro, mode):
    """(e) 作業した人を渡さない編集 (鍵が無い・KEEP)・社員を外す編集 (None) では R28 が出ない。"""
    settings.SEIZOU = {"progress_inactive_staff": mode}
    progress = services.record_progress(step, Decimal("1"), RECORDED_AT, user, staff=jiro)
    assert checks.run(UPDATE_ACTION, {"object": progress, "qty": Decimal("2")}, user) == []
    assert checks.run(UPDATE_ACTION, {"object": progress, "qty": Decimal("2"), "staff": services.KEEP}, user) == []
    assert checks.run(UPDATE_ACTION, {"object": progress, "qty": Decimal("2"), "staff": None, "staff_name": ""}, user) == []
