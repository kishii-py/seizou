"""実績に作業した人を残す (progress の Staff への外部キーと、名前と番号の写し。本人の決め 11-11 09:0x)。

社員が居る間は外部キーだけを書き、見せる字 (Progress.get_staff_name_display) は社員の今の番号と名前。
社員の一覧に居ない人 (応援) は名前の写しだけ。社員を消すときは、その社員を指す実績の全部に消す時の名前と番号を写してから
外部キーを空にし、社員を消す事と同じ transaction.atomic の中で行う (masters.services.delete_staff → progress.services.copy_staff_before_delete)。
作り物のデータは全部 services で作る (工程の行だけは R07 の試験と同じく表に直に作る)。
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.db.models import ProtectedError

from seizou.core.models import ChangeLog
from seizou.core.views.base import display_value
from seizou.masters import services as masters_services
from seizou.masters.models import Process, Staff
from seizou.orders import services as orders_services
from seizou.progress import services
from seizou.progress.models import Progress
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def deleter(db):
    return get_user_model().objects.create_user("deleter", password="pass-for-tests-only")


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
    return masters_services.create_staff({"code": "0012", "name": "作り物の太郎"}, user)


@pytest.fixture
def jiro(user):
    return masters_services.create_staff({"code": "0034", "name": "作り物の次郎"}, user)


def _record(step, user, **kwargs):
    return services.record_progress(step, Decimal("1"), RECORDED_AT, user, **kwargs)


def _update(progress, user, **kwargs):
    return services.update_progress(progress, Decimal("2"), RECORDED_AT, user, **kwargs)


def _step_text(step):
    return f"{step.workorder.wo_no} の工程 {step.seq} {step.process.name}"


# ---------------------------------------------------------------- 入れる


def test_record_with_staff_writes_only_foreign_key(user, step, taro):
    progress = _record(step, user, staff=taro, staff_name="渡しても使わない名前")
    progress.refresh_from_db()
    assert progress.staff == taro
    assert progress.staff_name == "" and progress.staff_code == ""
    assert progress.get_staff_name_display() == "0012 作り物の太郎"
    # 社員の一覧で名前を直すと、前の実績の見せる字も直る
    masters_services.update_staff(taro, {"name": "作り物の太郎 (直した)"}, user)
    assert Progress.objects.get().get_staff_name_display() == "0012 作り物の太郎 (直した)"
    # 一覧の列と CSV の staff_name も同じ字 (土台の display_value は get_<欄の名前>_display を先に見る)
    assert display_value(Progress.objects.get(), "staff_name") == "0012 作り物の太郎 (直した)"


def test_record_writes_inactive_staff(user, step, taro):
    """services は使用停止の社員の実績も保存し、外部キーを書く。

    本人の決め 11-11 13:1x による読み替え (使用停止の社員を断らず、R28 で知らせる)。知らせるか止めるかは検査の層の R28 が
    services の前に見る (tests/test_r28_inactive_staff.py)。
    """
    masters_services.update_staff(taro, {"active": False}, user)
    progress = _record(step, user, staff=taro)
    progress.refresh_from_db()
    assert progress.staff == taro
    assert progress.staff_name == "" and progress.staff_code == ""
    assert progress.get_staff_name_display() == "0012 作り物の太郎"


def test_record_with_name_only(user, step):
    progress = _record(step, user, staff_name="  応援の作り物の花子 ")
    progress.refresh_from_db()
    assert progress.staff is None
    assert progress.staff_name == "応援の作り物の花子" and progress.staff_code == ""
    assert progress.get_staff_name_display() == "応援の作り物の花子"


def test_record_refuses_too_long_name(user, step):
    with pytest.raises(ValueError) as error:
        _record(step, user, staff_name="長" * 101)
    assert "100 字" in str(error.value)
    assert not Progress.objects.exists()
    assert _record(step, user, staff_name="長" * 100).staff_name == "長" * 100


def test_saved_message_and_str_with_staff(user, step, taro):
    progress = _record(step, user, staff=taro)
    assert progress.saved_message == f"{_step_text(step)} に実績 1 を記録しました。作業した人は 0012 作り物の太郎 です。"
    assert str(progress).endswith(" 作業 0012 作り物の太郎")
    completed = _record(step, user, staff_name="応援の作り物の花子", completes_step=True)
    assert completed.saved_message == (
        f"{_step_text(step)} に実績 1 を記録しました。作業した人は 応援の作り物の花子 です。この工程に完了の印を付けました。"
    )


def test_saved_message_and_str_without_staff(user, step):
    progress = _record(step, user)
    assert progress.saved_message == f"{_step_text(step)} に実績 1 を記録しました。"
    assert progress.get_staff_name_display() == ""
    assert str(progress) == f"{step} 実績 1 (2026-11-03 10:00)"


# ---------------------------------------------------------------- 直す


def test_update_without_staff_keeps_worker(user, step, taro):
    with_staff = _record(step, user, staff=taro)
    with_name = _record(step, user, staff_name="応援の作り物の花子")
    _update(with_staff, user)
    _update(with_name, user)
    with_staff.refresh_from_db()
    with_name.refresh_from_db()
    assert with_staff.qty == Decimal("2") and with_staff.staff == taro and with_staff.staff_name == ""
    assert with_name.qty == Decimal("2") and with_name.staff is None and with_name.staff_name == "応援の作り物の花子"


def test_update_with_same_inactive_staff_passes(user, step, taro):
    progress = _record(step, user, staff=taro)
    masters_services.update_staff(taro, {"active": False}, user)
    _update(progress, user, staff=taro)
    progress.refresh_from_db()
    assert progress.qty == Decimal("2") and progress.staff == taro


def test_update_with_other_inactive_staff_writes(user, step, taro, jiro):
    """services は、実績を別の使用停止の社員に直せる (外部キーを書き、2 つの写しを空にする)。

    本人の決め 11-11 13:1x による読み替え (使用停止の社員を断らず、R28 で知らせる)。知らせるか止めるかは検査の層の R28 が
    services の前に見る (tests/test_r28_inactive_staff.py)。
    """
    progress = _record(step, user, staff=jiro)
    masters_services.update_staff(taro, {"active": False}, user)
    _update(progress, user, staff=taro)
    progress.refresh_from_db()
    assert progress.qty == Decimal("2") and progress.staff == taro
    assert progress.staff_name == "" and progress.staff_code == ""


def test_update_switches_between_staff_and_name(user, step, taro, jiro):
    progress = _record(step, user, staff_name="応援の作り物の花子")
    _update(progress, user, staff=jiro)
    progress.refresh_from_db()
    assert progress.staff == jiro and progress.staff_name == "" and progress.staff_code == ""
    _update(progress, user, staff=None, staff_name=" 応援の作り物の夏子 ")
    progress.refresh_from_db()
    assert progress.staff is None and progress.staff_name == "応援の作り物の夏子" and progress.staff_code == ""


# ---------------------------------------------------------------- 社員を消す


def test_delete_staff_copies_name_and_code(user, deleter, step, taro):
    first = _record(step, user, staff=taro)
    second = _record(step, user, staff=taro)
    masters_services.update_staff(taro, {"name": "作り物の太郎 (直した)"}, user)
    before = {row.pk: row.get_staff_name_display() for row in Progress.objects.all()}
    assert set(before.values()) == {"0012 作り物の太郎 (直した)"}
    assert masters_services.staff_progress_count(taro) == 2

    deleted = masters_services.delete_staff(taro, deleter)

    assert deleted.pk is None
    assert deleted.saved_message == "社員 0012 作り物の太郎 (直した) を削除しました。実績 2 件に、名前と番号を写して残しました。"
    assert not Staff.objects.exists()
    for row in Progress.objects.all():
        assert row.staff_id is None
        assert row.staff_name == "作り物の太郎 (直した)" and row.staff_code == "0012"
        assert row.get_staff_name_display() == before[row.pk]
        assert str(row).endswith(" 作業 0012 作り物の太郎 (直した)")
    # 2 件それぞれに、社員を消した人の「直した」の履歴が残る (指示書の詳細の「履歴」に出る)
    for progress in (first, second):
        rows = ChangeLog.objects.filter(model="progress.progress", object_pk=progress.pk, action=ChangeLog.ACTION_UPDATED)
        assert [row.actor for row in rows] == [deleter]
        assert rows[0].owner_model == "workorders.workorder"


def test_delete_staff_rolls_back_when_it_stops(user, deleter, step, taro, monkeypatch):
    _record(step, user, staff=taro)
    _record(step, user, staff=taro)

    def _stop(obj, user, **kwargs):
        raise RuntimeError("作り物の止まり")

    monkeypatch.setattr(masters_services, "record_history_deleted", _stop)
    with pytest.raises(RuntimeError):
        masters_services.delete_staff(taro, deleter)

    assert Staff.objects.filter(pk=taro.pk).exists()
    for row in Progress.objects.all():
        assert row.staff_id == taro.pk and row.staff_name == "" and row.staff_code == ""
    assert not ChangeLog.objects.filter(model="progress.progress", action=ChangeLog.ACTION_UPDATED).exists()


def test_delete_without_services_is_protected(user, step, taro):
    _record(step, user, staff=taro)
    with pytest.raises(ProtectedError):
        Staff.objects.get(pk=taro.pk).delete()
    progress = Progress.objects.get()
    assert progress.staff_id == taro.pk and progress.staff_name == "" and progress.staff_code == ""


def test_staff_progress_count(user, step, taro, jiro):
    _record(step, user, staff=taro)
    _record(step, user, staff=taro)
    _record(step, user, staff=jiro)
    _record(step, user, staff_name="応援の作り物の花子")
    assert masters_services.staff_progress_count(taro) == 2
    assert masters_services.staff_progress_count(jiro) == 1
    assert services.count_by_staff(taro) == 2
