"""不良の区分の一覧の services (masters.services の create_defect_kind・update_defect_kind・delete_defect_kind と読むだけの関数)。

名前は前後の空白 (全角の空白を含む) を落としてから書き、同じ名前の区分は 1 つだけ (集計の行が 2 つに分かれないため)。
使わなくなった区分は使用停止 (active が偽) にできる。不良の行を持つ区分を消す試験は tests/test_progress_defect_lines.py に置く。
作り物のデータだけを使う。
"""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from seizou.core.models import ChangeLog
from seizou.masters import services
from seizou.masters.models import DefectKind

DUPLICATE = "保存できません。不良の区分「{name}」は既にあります。集計の行が分かれないように、今の区分を使うか、別の名前にしてください。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def scratch(user):
    return services.create_defect_kind({"name": "傷"}, user)


def _history(kind_pk):
    return list(ChangeLog.objects.filter(model="masters.defectkind", object_pk=kind_pk).order_by("pk"))


def test_create_defect_kind_writes_values_and_history(user):
    kind = services.create_defect_kind({"name": "傷", "sort_order": 2, "note": "外観の傷"}, user)
    assert kind.saved_message == "不良の区分「傷」を登録しました。"
    stored = DefectKind.objects.get()
    assert (stored.pk, stored.name, stored.sort_order, stored.note, stored.active) == (kind.pk, "傷", 2, "外観の傷", True)
    assert str(stored) == "傷"
    rows = _history(kind.pk)
    assert [(row.action, row.actor) for row in rows] == [(ChangeLog.ACTION_CREATED, user)]
    assert rows[0].owner_model == "masters.defectkind"


def test_create_trims_spaces(user):
    kind = services.create_defect_kind({"name": "　寸法 "}, user)
    assert DefectKind.objects.get(pk=kind.pk).name == "寸法"
    assert kind.saved_message == "不良の区分「寸法」を登録しました。"


def test_create_refuses_same_name_after_trim(user, scratch):
    """「傷」の後に「 傷 」(前後に全角と半角の空白) で登録すると断られる。"""
    with pytest.raises(ValueError) as caught:
        services.create_defect_kind({"name": "　傷 "}, user)
    assert str(caught.value) == DUPLICATE.format(name="傷")
    assert DefectKind.objects.count() == 1


def test_create_refuses_same_name_on_race(user, scratch, monkeypatch):
    """2 人が同時に保存したとき (先の確かめを通った後) は、表の一意の制約で止まり、同じ文の ValueError になる。"""
    monkeypatch.setattr(services, "_refuse_duplicate_defect_kind", lambda name, *, exclude_pk=None: None)
    with pytest.raises(ValueError) as caught:
        services.create_defect_kind({"name": "傷"}, user)
    assert str(caught.value) == DUPLICATE.format(name="傷")
    assert not isinstance(caught.value, IntegrityError)
    assert DefectKind.objects.count() == 1


def test_update_refuses_other_kinds_name(user, scratch):
    other = services.create_defect_kind({"name": "寸法"}, user)
    with pytest.raises(ValueError) as caught:
        services.update_defect_kind(other, {"name": " 傷"}, user)
    assert str(caught.value) == DUPLICATE.format(name="傷")
    assert DefectKind.objects.get(pk=other.pk).name == "寸法"


def test_update_refuses_other_kinds_name_on_race(user, scratch, monkeypatch):
    other = services.create_defect_kind({"name": "寸法"}, user)
    monkeypatch.setattr(services, "_refuse_duplicate_defect_kind", lambda name, *, exclude_pk=None: None)
    with pytest.raises(ValueError) as caught:
        services.update_defect_kind(other, {"name": "傷"}, user)
    assert str(caught.value) == DUPLICATE.format(name="傷")
    assert DefectKind.objects.get(pk=other.pk).name == "寸法"


def test_update_keeps_own_name(user, scratch):
    kind = services.update_defect_kind(scratch, {"name": "傷 ", "sort_order": 3, "note": "直した"}, user)
    assert kind.saved_message == "不良の区分「傷」を直しました。"
    stored = DefectKind.objects.get(pk=scratch.pk)
    assert (stored.name, stored.sort_order, stored.note) == ("傷", 3, "直した")
    assert [row.action for row in _history(scratch.pk)] == [ChangeLog.ACTION_CREATED, ChangeLog.ACTION_UPDATED]


def test_update_records_stop_and_resume(user, scratch):
    services.update_defect_kind(scratch, {"active": False}, user)
    services.update_defect_kind(scratch, {"active": True}, user)
    assert [row.action for row in _history(scratch.pk)][1:] == [ChangeLog.ACTION_STOPPED, ChangeLog.ACTION_RESUMED]


def test_create_refuses_unknown_keys(user):
    with pytest.raises(ValueError) as caught:
        services.create_defect_kind({"name": "傷", "code": "K1"}, user)
    assert str(caught.value) == "保存できません。知らない項目 (code) があります。不良の区分の列の名前だけを渡してください。"
    assert not DefectKind.objects.exists()


def test_active_defect_kinds_and_has_active_defect_kinds(user):
    assert services.has_active_defect_kinds() is False
    scratch = services.create_defect_kind({"name": "傷"}, user)
    size = services.create_defect_kind({"name": "寸法"}, user)
    assert services.has_active_defect_kinds() is True
    services.update_defect_kind(scratch, {"active": False}, user)
    assert list(services.active_defect_kinds()) == [size]
    services.update_defect_kind(size, {"active": False}, user)
    assert services.has_active_defect_kinds() is False
    assert list(services.active_defect_kinds()) == []
    # 編集で今の行の区分が使用停止でも、候補に残す (keep は区分か pk)
    assert list(services.active_defect_kinds(keep=scratch)) == [scratch]
    assert list(services.active_defect_kinds(keep=scratch.pk)) == [scratch]


def test_defect_kind_ordering_is_sort_order_then_name(user):
    later = services.create_defect_kind({"name": "あ", "sort_order": 2}, user)
    second = services.create_defect_kind({"name": "う", "sort_order": 1}, user)
    first = services.create_defect_kind({"name": "い", "sort_order": 1}, user)
    assert list(DefectKind.objects.all()) == [first, second, later]


def test_delete_defect_kind_without_lines(user, scratch):
    assert services.defect_kind_line_count(scratch) == 0
    deleted = services.delete_defect_kind(scratch, user)
    assert deleted.pk is None
    assert deleted.saved_message == "不良の区分「傷」を削除しました。"
    assert not DefectKind.objects.exists()
    rows = _history(scratch.pk)
    assert rows[-1].action == ChangeLog.ACTION_DELETED and rows[-1].actor == user


def test_delete_defect_kind_twice_is_refused(user, scratch):
    services.delete_defect_kind(scratch, user)
    with pytest.raises(ValueError) as caught:
        services.delete_defect_kind(scratch, user)
    assert str(caught.value) == "削除できません。この不良の区分は既に消してあります。"


def test_same_name_can_be_used_after_delete(user, scratch):
    services.delete_defect_kind(scratch, user)
    again = services.create_defect_kind({"name": "傷"}, user)
    assert DefectKind.objects.get().pk == again.pk
