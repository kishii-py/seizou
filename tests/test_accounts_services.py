"""アカウント管理の services (02 の 7 節)。発行・変更・使用停止と再開・削除と、断る変更。

断る変更は日本語の ValueError で、何も変えず記録も残さない。
"""

import pytest
from django.contrib.auth import authenticate, get_user_model

from seizou.accounts import services
from seizou.accounts.models import Access, AccessLog, Department

User = get_user_model()

PASSWORD = "kumo-tests-2611"
OTHER_PASSWORD = "hoshi-tests-2611"


@pytest.fixture
def seizou_dept(db):
    return services.create_department("製造", ["workorders", "progress", "masters"], ["workorders"], None)


@pytest.fixture
def sales_dept(db):
    return services.create_department("受注と出荷", ["orders", "shipping"], ["orders", "shipping"], None)


@pytest.fixture
def manager(db):
    """アカウント管理を使える使用中の担当者 (1 人目)。"""
    services.issue_account("soumu", PASSWORD, [], [], {"manage_accounts": True}, None)
    return User.objects.get(username="soumu")


def issue(login_id, *, departments=(), permissions=(), by=None, **flags):
    services.issue_account(login_id, PASSWORD, list(departments), list(permissions), flags, by)
    return Access.objects.select_related("user").get(user__username=login_id)


def fresh(row):
    return Access.objects.select_related("user").get(pk=row.pk)


def state():
    """表の今の様子 (断る変更で何も変わらないことを見る)。"""
    return (
        sorted(User.objects.values_list("username", "is_active", "password")),
        sorted(
            (a.user_id, tuple(a.write_permissions), a.see_money, a.full_control, a.manage_accounts, a.deleted_at)
            for a in Access.objects.all()
        ),
        sorted(Access.departments.through.objects.values_list("access_id", "department_id")),
        AccessLog.objects.count(),
    )


# ---------------------------------------------------------------- 発行


@pytest.mark.django_db
def test_issue_creates_user_access_and_log(manager, seizou_dept):
    account = services.issue_account(
        "genba",
        PASSWORD,
        [seizou_dept],
        ["workorders", "slip_edit"],
        {"see_money": True},
        manager,
    )

    assert isinstance(account, User)
    row = Access.objects.get(user=account)
    assert list(row.departments.all()) == [seizou_dept]
    assert row.write_permissions == ["slip_edit", "workorders"]
    assert row.see_money is True
    assert row.full_control is False
    assert row.manage_accounts is False
    assert account.is_active
    assert authenticate(username="genba", password=PASSWORD) == account

    log = AccessLog.objects.filter(target=account).get()
    assert log.action == AccessLog.ACTION_ISSUE
    assert log.actor == manager
    assert log.password_changed is True
    assert log.before == {}
    assert log.after == {
        "login_id": "genba",
        "departments": ["製造"],
        "permissions": ["slip_edit", "workorders"],
        "see_money": True,
        "full_control": False,
        "manage_accounts": False,
        "is_active": True,
        "deleted": False,
    }
    assert PASSWORD not in str(log.before) + str(log.after)
    assert account.password not in str(log.after)


@pytest.mark.parametrize(
    ("login_id", "password", "permissions", "words"),
    [
        ("soumu", PASSWORD, [], "このログイン ID は使われています"),
        ("gen ba", PASSWORD, [], "ログイン ID は英数字と"),
        ("", PASSWORD, [], "ログイン ID は英数字と"),
        ("genba", "kumo-26", [], "パスワードを受け付けられません"),
        ("genba", "2611202611", [], "パスワードを受け付けられません"),
        ("genba", "password1", [], "パスワードを受け付けられません"),
        ("genbataro", "genbataro1", [], "パスワードを受け付けられません"),
        ("genba", PASSWORD, ["admin_all"], "知らない権限の名前です: admin_all"),
    ],
)
@pytest.mark.django_db
def test_issue_refuses_bad_input(manager, login_id, password, permissions, words):
    before = state()

    with pytest.raises(ValueError) as caught:
        services.issue_account(login_id, password, [], permissions, {}, manager)

    assert words in str(caught.value)
    assert state() == before


# ---------------------------------------------------------------- 変更


@pytest.mark.django_db
def test_update_departments_permissions_and_password(manager, seizou_dept, sales_dept):
    row = issue("genba", departments=[seizou_dept], permissions=["workorders"], by=manager)

    services.update_account(
        row,
        {"departments": [sales_dept.pk], "permissions": ["orders", "shipping"], "see_money": True},
        manager,
        password="",
    )
    row = fresh(row)
    assert list(row.departments.all()) == [sales_dept]
    assert row.write_permissions == ["orders", "shipping"]
    assert row.see_money is True
    assert authenticate(username="genba", password=PASSWORD) is not None
    log = AccessLog.objects.filter(target=row.user).first()
    assert log.action == AccessLog.ACTION_UPDATE
    assert log.password_changed is False
    assert log.before["departments"] == ["製造"]
    assert log.after["departments"] == ["受注と出荷"]

    # 無い鍵は今のまま。パスワードを入れたら変わる。
    services.update_account(row, {}, manager, password=OTHER_PASSWORD)
    row = fresh(row)
    assert row.write_permissions == ["orders", "shipping"]
    assert authenticate(username="genba", password=PASSWORD) is None
    assert authenticate(username="genba", password=OTHER_PASSWORD) is not None
    assert AccessLog.objects.filter(target=row.user).first().password_changed is True


@pytest.mark.django_db
def test_disable_enable_logs(manager):
    row = issue("genba", by=manager)

    services.disable_account(row, manager)
    assert fresh(row).user.is_active is False
    assert AccessLog.objects.filter(target=row.user).first().action == AccessLog.ACTION_DISABLE
    assert fresh(row).status_label() == "使用停止"

    services.enable_account(row, manager)
    assert fresh(row).user.is_active is True
    assert AccessLog.objects.filter(target=row.user).first().action == AccessLog.ACTION_ENABLE
    assert fresh(row).status_label() == "使用中"


# ---------------------------------------------------------------- 削除


@pytest.mark.django_db
def test_delete_frees_login_id(manager):
    row = issue("genba", by=manager)
    pk = row.user.pk

    services.delete_account(row, manager)

    row = fresh(row)
    assert row.deleted_at is not None
    assert row.user.is_active is False
    assert row.user.username == f"genba_deleted_{pk}"
    assert row.status_label() == "削除"
    log = AccessLog.objects.filter(target=row.user).first()
    assert log.action == AccessLog.ACTION_DELETE
    assert log.before["login_id"] == "genba"
    assert log.after["deleted"] is True

    again = issue("genba", by=manager)
    assert again.pk != row.pk

    before = state()
    with pytest.raises(ValueError, match="削除したアカウントは変更も削除もできません。"):
        services.update_account(row, {"see_money": True}, manager)
    with pytest.raises(ValueError, match="削除したアカウントは変更も削除もできません。"):
        services.delete_account(row, manager)
    assert state() == before


# ---------------------------------------------------------------- 断る変更


def _self_delete(manager, second, admin_row):
    services.delete_account(fresh(manager.seizou_access), manager)


def _self_disable(manager, second, admin_row):
    services.update_account(fresh(manager.seizou_access), {"is_active": False}, manager)


def _self_unmanage(manager, second, admin_row):
    services.update_account(fresh(manager.seizou_access), {"manage_accounts": False}, manager)


def _last_manager(manager, second, admin_row):
    # 2 人目を使用停止にしてから、もう 1 人のアカウント管理を外す (担当者が 0 になる)。
    services.update_account(fresh(manager.seizou_access), {"manage_accounts": False}, second.user)


def _self_full_control(manager, second, admin_row):
    services.update_account(fresh(manager.seizou_access), {"full_control": True}, manager)


def _self_money(manager, second, admin_row):
    services.update_account(fresh(manager.seizou_access), {"see_money": True}, manager)


def _superuser(manager, second, admin_row):
    services.update_account(admin_row, {"see_money": True}, manager)


REFUSED = [
    (_self_delete, "自分のアカウントは削除できません。別のアカウント管理の担当者に頼んでください。", False),
    (_self_disable, "自分のアカウントは使用停止にできません。別のアカウント管理の担当者に頼んでください。", False),
    (_self_unmanage, "自分のアカウントからアカウント管理の権限は外せません。別のアカウント管理の担当者に頼んでください。", False),
    (
        _last_manager,
        "アカウント管理を使える使用中のアカウントがこの 1 つだけのため、止められません。"
        "先に別のアカウントにアカウント管理の権限を付けてください。",
        True,
    ),
    (_self_full_control, "自分のアカウントにフルコントロールと金額の閲覧は付けられません。別のアカウント管理の担当者に頼んでください。", False),
    (_self_money, "自分のアカウントにフルコントロールと金額の閲覧は付けられません。別のアカウント管理の担当者に頼んでください。", False),
    (_superuser, "システム管理者のアカウントは、この画面から変更も削除もできません。", False),
]


@pytest.mark.parametrize(("change", "message", "only_one"), REFUSED, ids=[r[0].__name__ for r in REFUSED])
@pytest.mark.django_db
def test_refused_changes(manager, change, message, only_one):
    # 担当者を 2 人にして、自分への断りが「最後の 1 人」の断りに紛れないようにする。
    second = issue("soumu2", by=manager, full_control=True)
    admin = User.objects.create_superuser("admin", password=PASSWORD)
    admin_row = Access.objects.create(user=admin)
    if only_one:
        services.disable_account(second, manager)
        assert services.manager_count() == 1
    before = state()

    with pytest.raises(ValueError) as caught:
        change(User.objects.get(pk=manager.pk), fresh(second), admin_row)

    assert str(caught.value) == message
    assert state() == before


@pytest.mark.django_db
def test_last_manager_cannot_be_deleted_or_disabled(manager):
    # 担当者が 1 人だけのとき、ほかの人 (システム管理者) が止めるのも消すのも断る。
    admin = User.objects.create_superuser("admin", password=PASSWORD)
    row = fresh(manager.seizou_access)
    before = state()

    for change in (services.disable_account, services.delete_account):
        with pytest.raises(ValueError, match="この 1 つだけのため、止められません"):
            change(row, admin)
    assert state() == before


@pytest.mark.django_db
def test_create_department_refuses_unknown_and_duplicate(db):
    services.create_department("製造", ["workorders"], ["workorders"], None)

    with pytest.raises(ValueError, match="既にあります"):
        services.create_department("製造", ["workorders"], [], None)
    with pytest.raises(ValueError, match="選べない名前"):
        services.create_department("総務", ["accounts"], [], None)
    with pytest.raises(ValueError, match="選べない名前"):
        services.create_department("総務", ["core"], [], None)
    with pytest.raises(ValueError, match="知らない権限の名前です"):
        services.create_department("総務", ["masters"], ["admin_all"], None)
    assert list(Department.objects.values_list("name", flat=True)) == ["製造"]


@pytest.mark.django_db
def test_self_update_keeps_own_flags(manager, seizou_dept):
    # 変更の form は印を 4 つとも送る。印を変えない自分の保存と、自分の金額の閲覧を外す変更は通す。
    second = issue("soumu2", by=manager, full_control=True)
    own = {"full_control": True, "see_money": False, "manage_accounts": False, "departments": [seizou_dept]}

    services.update_account(fresh(second), own, second.user)
    assert list(fresh(second).departments.all()) == [seizou_dept]

    services.update_account(fresh(second), {"see_money": True}, manager)
    assert fresh(second).see_money is True

    services.update_account(fresh(second), own, second.user)
    row = fresh(second)
    assert (row.full_control, row.see_money) == (True, False)
