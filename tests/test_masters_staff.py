"""社員の一覧の services (masters.services の create_staff・update_staff・delete_staff と読むだけの関数)。

社員番号は照合の字 (code_key) で一意にする。全角を半角にそろえ、前後の空白を落とし、数字だけの番号は先頭の 0 を落とす
(「0012」「12」「１２」は同じ番号)。英字を含む番号は 0 を落とさず、大文字と小文字も分ける。作り物のデータだけを使う。
実績を持つ社員を消す試験は tests/test_progress_staff.py に置く。
"""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from seizou.core.models import ChangeLog
from seizou.masters import services
from seizou.masters.models import Staff

TARO = {"code": "0012", "name": "作り物の太郎"}
DUPLICATE_12 = (
    "保存できません。社員番号 {entered} は 0012 作り物の太郎 と同じ番号です "
    "(先頭の 0 の数が違う番号も同じ番号と見ます)。別の番号にしてください。"
)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def taro(user):
    return services.create_staff(dict(TARO), user)


def _history(staff_pk):
    return list(ChangeLog.objects.filter(model="masters.staff", object_pk=staff_pk).order_by("pk"))


@pytest.mark.parametrize(
    ("code", "key"),
    [
        ("0012", "12"),
        ("12", "12"),
        ("１２", "12"),
        ("０１２", "12"),
        (" 0012 ", "12"),
        ("000", "0"),
        ("0", "0"),
        ("A012", "A012"),
        ("A12", "A12"),
        ("a12", "a12"),
        ("Ａ０１２", "A012"),
    ],
)
def test_staff_code_key(code, key):
    assert services.staff_code_key(code) == key


def test_create_staff_writes_key_and_history(user, taro):
    assert Staff.objects.get() == taro
    assert taro.code == "0012" and taro.code_key == "12"
    assert taro.name == "作り物の太郎" and taro.note == "" and taro.active is True
    assert str(taro) == "0012 作り物の太郎"
    rows = _history(taro.pk)
    assert [row.action for row in rows] == [ChangeLog.ACTION_CREATED]
    assert rows[0].actor == user


@pytest.mark.parametrize("entered", ["12", "１２", "012"])
def test_create_staff_refuses_same_key(user, taro, entered):
    with pytest.raises(ValueError) as error:
        services.create_staff({"code": entered, "name": "作り物の次郎"}, user)
    assert str(error.value) == DUPLICATE_12.format(entered=entered)
    assert Staff.objects.count() == 1


def test_create_staff_refuses_same_key_on_race(user, taro, monkeypatch):
    """2 人が同時に保存したとき (先の確かめを通った後) は、表の一意の制約で止め、同じ文の ValueError にする。"""
    monkeypatch.setattr(services, "_refuse_duplicate_staff_code", lambda code, *, exclude_pk=None: None)
    with pytest.raises(ValueError) as error:
        services.create_staff({"code": "12", "name": "作り物の次郎"}, user)
    assert not isinstance(error.value, IntegrityError)
    assert str(error.value) == DUPLICATE_12.format(entered="12")
    assert Staff.objects.count() == 1


def test_update_staff_refuses_other_staff_key(user, taro):
    jiro = services.create_staff({"code": "0034", "name": "作り物の次郎"}, user)
    with pytest.raises(ValueError) as error:
        services.update_staff(jiro, {"code": "12"}, user)
    assert str(error.value) == DUPLICATE_12.format(entered="12")
    jiro.refresh_from_db()
    assert jiro.code == "0034" and jiro.code_key == "34"


def test_update_staff_keeps_own_code(user, taro):
    updated = services.update_staff(taro, {"code": "0012", "name": "作り物の太郎 (直した)", "note": "夜勤"}, user)
    assert updated.name == "作り物の太郎 (直した)" and updated.note == "夜勤"
    # 自分の番号の先頭の 0 だけを変える編集も通る (照合の字は同じ 12)
    services.update_staff(taro, {"code": "012"}, user)
    taro.refresh_from_db()
    assert taro.code == "012" and taro.code_key == "12"
    assert [row.action for row in _history(taro.pk)] == [ChangeLog.ACTION_CREATED, ChangeLog.ACTION_UPDATED, ChangeLog.ACTION_UPDATED]


def test_update_staff_records_stop_and_resume(user, taro):
    services.update_staff(taro, {"active": False}, user)
    services.update_staff(taro, {"active": True}, user)
    assert [row.action for row in _history(taro.pk)][1:] == [ChangeLog.ACTION_STOPPED, ChangeLog.ACTION_RESUMED]


def test_letters_keep_zeros_and_case(user):
    services.create_staff({"code": "A012", "name": "作り物の一郎"}, user)
    services.create_staff({"code": "A12", "name": "作り物の二郎"}, user)
    services.create_staff({"code": "a12", "name": "作り物の三郎"}, user)
    assert Staff.objects.count() == 3
    with pytest.raises(ValueError):
        services.create_staff({"code": "Ａ０１２", "name": "作り物の四郎"}, user)


def test_create_staff_refuses_unknown_keys(user):
    with pytest.raises(ValueError) as error:
        services.create_staff({**TARO, "code_key": "12"}, user)
    assert "code_key" in str(error.value) and "社員" in str(error.value)
    assert not Staff.objects.exists()


def test_staff_by_code_finds_by_key_and_inactive(user, taro):
    assert services.staff_by_code("12") == taro
    assert services.staff_by_code("１２") == taro
    assert services.staff_by_code("13") is None
    services.update_staff(taro, {"active": False}, user)
    assert services.staff_by_code("0012") == taro


def test_active_staff_and_has_active_staff(user):
    assert services.has_active_staff() is False
    taro = services.create_staff(dict(TARO), user)
    jiro = services.create_staff({"code": "0034", "name": "作り物の次郎"}, user)
    assert services.has_active_staff() is True
    services.update_staff(taro, {"active": False}, user)
    assert list(services.active_staff()) == [jiro]
    assert set(services.active_staff(keep=taro)) == {taro, jiro}
    assert set(services.active_staff(keep=taro.pk)) == {taro, jiro}
    services.update_staff(jiro, {"active": False}, user)
    assert services.has_active_staff() is False


def test_staff_ordering_is_code_then_pk(user):
    services.create_staff({"code": "0034", "name": "作り物の次郎"}, user)
    services.create_staff(dict(TARO), user)
    assert [staff.code for staff in Staff.objects.all()] == ["0012", "0034"]


def test_delete_staff_without_progress(user, taro):
    pk = taro.pk
    assert services.staff_progress_count(taro) == 0
    deleted = services.delete_staff(taro, user)
    assert deleted.pk is None
    assert deleted.saved_message == "社員 0012 作り物の太郎 を削除しました。"
    assert not Staff.objects.exists()
    rows = _history(pk)
    assert rows[-1].action == ChangeLog.ACTION_DELETED and rows[-1].actor == user
    assert rows[-1].object_text == "0012 作り物の太郎"


def test_delete_staff_twice_is_refused(user, taro):
    services.delete_staff(taro, user)
    with pytest.raises(ValueError) as error:
        services.delete_staff(taro, user)
    assert str(error.value) == "削除できません。この社員は既に消してあります。"


def test_same_code_can_be_used_after_delete(user, taro):
    services.delete_staff(taro, user)
    again = services.create_staff({"code": "12", "name": "作り物の新しい太郎"}, user)
    assert again.code_key == "12"
    assert services.staff_by_code("0012") == again
