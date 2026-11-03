"""部署の services と画面 (02 の 7 節の「第 5 波以降」の前倒し)。部署を直しても既にいる人の権限は変えない。

services は表の状態を、画面は Django の test client で HTML と表の状態を見る (判定は settings の fixture で 3 つとも on)。
"""

import pytest
from django.contrib.auth import get_user_model

from seizou.accounts import services
from seizou.accounts.models import Access, AccessLog, Department, app_labels

User = get_user_model()

PASSWORD = "kumo-tests-2611"
ALL_ON = {"department": "on", "write": "on", "money": "on"}


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


def account(login_id, *, departments=(), permissions=(), **flags):
    services.issue_account(login_id, PASSWORD, list(departments), list(permissions), flags, None)
    return User.objects.get(username=login_id)


@pytest.fixture
def manager(db):
    """アカウント管理の担当者。"""
    return account("soumu", manage_accounts=True)


@pytest.fixture
def seizou_dept(db):
    return services.create_department("製造", ["workorders", "progress", "masters"], ["workorders"], None)


@pytest.fixture
def sales_dept(db):
    return services.create_department("受注と出荷", ["orders", "shipping"], ["orders", "shipping"], None)


def fresh(dept):
    return Department.objects.get(pk=dept.pk)


def state(dept):
    """部署の今の様子 (断る変更で何も変わらないことを見る)。"""
    row = fresh(dept)
    return (row.name, list(row.apps), list(row.default_permissions), row.sort_order)


# ---------------------------------------------------------------- 表示の method


@pytest.mark.django_db
def test_display_helpers_use_menu_labels(manager, seizou_dept, sales_dept):
    labels = app_labels()
    assert labels["masters"] == "部品マスター・取引先・工程"
    assert labels["purchasing"] == "発注・受入"
    assert "指示書" in labels["workorders"]
    assert "intake" not in labels

    shown = seizou_dept.get_apps_display()
    positions = [shown.index(word) for word in ("指示書", "進捗", "部品マスター・取引先・工程")]
    assert positions == sorted(positions), shown
    assert seizou_dept.get_default_permissions_display() == "指示書"
    assert sales_dept.get_default_permissions_display() == "受注・出荷と納品書"

    assert seizou_dept.member_count() == 0
    account("genba", departments=[seizou_dept])
    assert seizou_dept.member_count() == 1
    gone = account("taishoku", departments=[seizou_dept])
    services.delete_account(gone.seizou_access, manager)
    assert seizou_dept.member_count() == 1
    assert [a.user.get_username() for a in seizou_dept.members] == ["genba"]


# ---------------------------------------------------------------- 直す


@pytest.mark.django_db
def test_update_department_keeps_member_permissions(manager, seizou_dept):
    genba = account("genba", departments=[seizou_dept], permissions=["workorders"])
    logs = AccessLog.objects.count()

    result = services.update_department(
        seizou_dept,
        {"name": "製造部", "apps": ["stock", "workorders"], "default_permissions": ["slip_edit"], "sort_order": 9},
        manager,
    )

    assert isinstance(result, Department)
    assert result.pk == seizou_dept.pk
    row = fresh(seizou_dept)
    assert row.name == "製造部"
    assert row.apps == ["workorders", "stock"]
    assert row.default_permissions == ["slip_edit"]
    assert row.sort_order == 9
    member = Access.objects.get(user=genba)
    assert member.write_permissions == ["workorders"]
    assert list(member.departments.all()) == [row]
    assert AccessLog.objects.count() == logs

    services.update_department(row, {"sort_order": 3}, manager)
    assert state(row) == ("製造部", ["workorders", "stock"], ["slip_edit"], 3)


@pytest.mark.django_db
def test_update_department_keeps_own_name(manager, seizou_dept):
    services.update_department(seizou_dept, {"name": "製造"}, manager)
    assert fresh(seizou_dept).name == "製造"

    services.update_department(seizou_dept, {"name": " 製造 "}, manager)
    assert fresh(seizou_dept).name == "製造"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("data", "words"),
    [
        pytest.param({"name": "受注と出荷"}, "既にあります", id="name-used"),
        pytest.param({"name": "  "}, "名前を入れてください", id="name-empty"),
        pytest.param({"apps": ["accounts"]}, "選べない名前", id="app-accounts"),
        pytest.param({"apps": ["core"]}, "選べない名前", id="app-core"),
        pytest.param({"apps": ["bogus"]}, "選べない名前", id="app-bogus"),
        pytest.param({"default_permissions": ["bogus"]}, "知らない権限の名前", id="permission-bogus"),
        pytest.param({"sort_order": -1}, "並び順は 0 以上", id="sort-negative"),
        pytest.param({"sort_order": "x"}, "並び順は 0 以上", id="sort-text"),
        pytest.param({"sort_order": True}, "並び順は 0 以上", id="sort-bool"),
    ],
)
def test_update_department_refuses_bad_input(manager, seizou_dept, sales_dept, data, words):
    before = state(seizou_dept)

    with pytest.raises(ValueError, match=words):
        services.update_department(seizou_dept, {"name": "製造部", **data}, manager)

    assert state(seizou_dept) == before


# ---------------------------------------------------------------- 消す


@pytest.mark.django_db
def test_delete_department_refuses_with_members_then_deletes(manager, seizou_dept, sales_dept):
    genba = account("genba", departments=[seizou_dept])

    with pytest.raises(ValueError) as refused:
        services.delete_department(seizou_dept, manager)
    message = str(refused.value)
    for word in ("製造", "1 人", "先にアカウント管理で"):
        assert word in message, word
    assert Department.objects.filter(pk=seizou_dept.pk).exists()

    gone = account("taishoku", departments=[seizou_dept])
    services.delete_account(gone.seizou_access, manager)
    with pytest.raises(ValueError, match="1 人"):
        services.delete_department(seizou_dept, manager)
    assert Department.objects.filter(pk=seizou_dept.pk).exists()

    services.update_account(genba.seizou_access, {"departments": []}, manager)
    logs = AccessLog.objects.count()
    assert services.delete_department(seizou_dept, manager) is None

    assert not Department.objects.filter(pk=seizou_dept.pk).exists()
    through = Access.departments.through.objects
    assert not through.filter(department_id=seizou_dept.pk).exists()
    assert Access.objects.filter(pk=gone.seizou_access.pk, deleted_at__isnull=False).exists()
    assert AccessLog.objects.filter(target=gone).exists()
    assert AccessLog.objects.count() == logs

    services.delete_department(sales_dept, manager)
    assert not Department.objects.filter(pk=sales_dept.pk).exists()


# ---------------------------------------------------------------- 作る (今のまま) と開ける部品の確かめ


@pytest.mark.django_db
def test_create_department_is_unchanged(seizou_dept):
    made = services.create_department("総務", ["masters"], ["master_edit"], None, sort_order=4)
    row = fresh(made)
    assert (row.name, row.apps, row.default_permissions, row.sort_order) == ("総務", ["masters"], ["master_edit"], 4)
    assert fresh(seizou_dept).apps == ["workorders", "progress", "masters"]

    with pytest.raises(ValueError, match="既にあります"):
        services.create_department("総務", [], [], None)

    assert services.clean_apps(["masters", "orders", "masters"]) == ["masters", "orders"]
    with pytest.raises(ValueError, match="選べない名前"):
        services.clean_apps(["bogus"])
