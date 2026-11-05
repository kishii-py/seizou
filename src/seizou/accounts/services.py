"""アカウント管理の services (02 の 7 節)。表に書くのはここだけ (R13)。

全部 transaction.atomic の中で書き、アカウントの変更は AccessLog に前と後 (snapshot) を残す。user は変えた人。
返す物は利用者 (User)。core に利用者の詳細の画面が無いので、入力の土台の保存の後の移り先は一覧になる。
断る変更は日本語の ValueError (何も変えず、記録も残さない)。入力の土台が赤の知らせにする。
部署 (Department) の作る・直す・消すもここ。部署を直しても既にいる人の権限は変えない (N36 の約束)。
"""

from __future__ import annotations

from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from seizou.core import access
from seizou.parts import PARTS

from .models import Access, AccessLog, Department

#: ログイン ID の長さの上限 (Django の利用者名と同じ)
LOGIN_ID_MAX = 150
#: 削除のときにログイン ID に付ける印 (<元の ID>_deleted_<利用者の pk>)
DELETED_MARK = "_deleted_"
#: 変更で直せる鍵
UPDATE_KEYS = ("departments", "permissions", "see_money", "full_control", "manage_accounts", "is_active")
#: 発行と変更で受ける印
FLAG_KEYS = ("see_money", "full_control", "manage_accounts")

CONTACT_OTHER = "別のアカウント管理の担当者に頼んでください。"
SELF_DELETE = "自分のアカウントです。" + CONTACT_OTHER
SELF_DISABLE = "自分のアカウントを使用停止にしようとしています。" + CONTACT_OTHER
SELF_MANAGE = "自分のアカウントからアカウント管理の権限を外そうとしています。" + CONTACT_OTHER
SELF_GRANT = "自分のアカウントにフルコントロールか金額の閲覧を付けようとしています。" + CONTACT_OTHER
LAST_MANAGER = (
    "アカウント管理を使える使用中のアカウントが、この 1 つだけです。"
    "先に別のアカウントにアカウント管理の権限を付けてください。"
)
SUPERUSER = "システム管理者のアカウントは、この画面から変更も削除もできません。"
DELETED = "削除したアカウントは変更も削除もできません。"
LOGIN_ID_USED = "このログイン ID は使われています。別の ID にしてください。"
LOGIN_ID_FORM = "ログイン ID は英数字と @ . + - _ で、150 字までにしてください。"
PASSWORD_REFUSED = "パスワードを受け付けられません: "
DEPARTMENT_NAME_EMPTY = "部署の名前を入れてください。"
DEPARTMENT_NAME_USED = "部署「{name}」は既にあります。別の名前にしてください。"
DEPARTMENT_APP_UNKNOWN = "部署の開ける部品に選べない名前です: {app}"
DEPARTMENT_SORT_ORDER = "並び順は 0 以上の整数にしてください。"
DEPARTMENT_IN_USE = (
    "部署「{name}」には {count} 人のアカウントが付いています。"
    "先にアカウント管理で、その人の部署を外してください。"
)


# ---------------------------------------------------------------- 確かめ


def password_validators() -> list:
    """パスワードの 4 つの確かめ (settings の AUTH_PASSWORD_VALIDATORS に頼らず、ここで明示する)。"""
    return [
        password_validation.MinimumLengthValidator(8),
        password_validation.CommonPasswordValidator(),
        password_validation.NumericPasswordValidator(),
        password_validation.UserAttributeSimilarityValidator(),
    ]


def check_password(password: str, account) -> None:
    """4 つの確かめに通らなければ、Django の日本語の文を並べた ValueError。"""
    try:
        password_validation.validate_password(password or "", user=account, password_validators=password_validators())
    except ValidationError as exc:
        raise ValueError("保存できません。" + PASSWORD_REFUSED + " ".join(exc.messages)) from None


def clean_login_id(login_id) -> str:
    login_id = (login_id or "").strip()
    if not login_id or len(login_id) > LOGIN_ID_MAX:
        raise ValueError("保存できません。" + LOGIN_ID_FORM)
    try:
        UnicodeUsernameValidator()(login_id)
    except ValidationError:
        raise ValueError("保存できません。" + LOGIN_ID_FORM) from None
    return login_id


def clean_permissions(names) -> list[str]:
    """権限の名前を確かめ、画面の並び (access.PERMISSION_NAMES) にそろえる。"""
    names = list(names or ())
    for name in names:
        if name not in access.PERMISSION_NAMES:
            raise ValueError(f"保存できません。知らない権限の名前です: {name}")
    return [name for name in access.PERMISSION_NAMES if name in names]


def clean_departments(values) -> list:
    """部署 (Department か pk) の並びを Department の並びにする。"""
    departments = []
    for value in values or ():
        if isinstance(value, Department):
            departments.append(value)
            continue
        department = Department.objects.filter(pk=value).first()
        if department is None:
            raise ValueError(f"保存できません。知らない部署です: {value}")
        departments.append(department)
    return departments


def department_apps() -> list[str]:
    """部署の「開ける部品」に選べる名前 (PARTS の短い名前のうち、いつも開く部品と accounts の外)。"""
    names = [part.rsplit(".", 1)[-1] for part in PARTS]
    return [name for name in names if name not in access.ALWAYS_OPEN_APPS and name not in access.ACCOUNT_APPS]


def clean_apps(apps) -> list[str]:
    """部署の開ける部品の名前を確かめ、department_apps() の並び (PARTS の順) にそろえて重なりを消す。"""
    allowed = department_apps()
    apps = list(apps or ())
    for app in apps:
        if app not in allowed:
            raise ValueError("保存できません。" + DEPARTMENT_APP_UNKNOWN.format(app=app))
    return [name for name in allowed if name in apps]


# ---------------------------------------------------------------- 記録


def snapshot(row) -> dict:
    """記録の前と後の辞書 (1 か所)。パスワードの値とハッシュは入れない。"""
    account = row.user
    return {
        "login_id": account.get_username(),
        "departments": [d.name for d in row.departments.all()],
        "permissions": list(row.write_permissions or []),
        "see_money": row.see_money,
        "full_control": row.full_control,
        "manage_accounts": row.manage_accounts,
        "is_active": account.is_active,
        "deleted": row.deleted_at is not None,
    }


def _log(actor, row, action, before, *, password_changed=False) -> None:
    AccessLog.objects.create(
        actor=actor,
        target=row.user,
        action=action,
        before=before,
        after=snapshot(row),
        password_changed=password_changed,
    )


# ---------------------------------------------------------------- 断る変更


def _is_self(row, user) -> bool:
    return user is not None and row.user_id == getattr(user, "pk", None)


def _counts_as_manager(row, *, is_active=None, manage_accounts=None, full_control=None) -> bool:
    """アカウント管理を使える使用中の人か (システム管理者と削除済みは数えない)。引数があればその値で見る。"""
    account = row.user
    if account.is_superuser or row.deleted_at is not None:
        return False
    active = account.is_active if is_active is None else is_active
    manage = row.manage_accounts if manage_accounts is None else manage_accounts
    full = row.full_control if full_control is None else full_control
    return bool(active and (manage or full))


def manager_count() -> int:
    """アカウント管理を使える使用中のアカウントの数 (システム管理者を除く)。"""
    return (
        Access.objects.filter(deleted_at__isnull=True, user__is_active=True, user__is_superuser=False)
        .filter(Q(manage_accounts=True) | Q(full_control=True))
        .count()
    )


def _locked(target) -> Access:
    """直す前に表から読み直す (渡された物を書き換えない)。削除済みとシステム管理者は断る。"""
    row = Access.objects.select_for_update().select_related("user").get(pk=target.pk)
    if row.deleted_at is not None:
        raise ValueError("保存できません。" + DELETED)
    if row.user.is_superuser:
        raise ValueError("保存できません。" + SUPERUSER)
    return row


# ---------------------------------------------------------------- 部署


@transaction.atomic
def create_department(name, apps, default_permissions, user, *, sort_order=0) -> Department:
    """部署を作る (種と、部署の新規登録の画面が呼ぶ)。記録の表には残さない。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("保存できません。部署の名前を入れてください。")
    if Department.objects.filter(name=name).exists():
        raise ValueError(f"保存できません。部署「{name}」は既にあります。別の名前にしてください。")
    allowed = department_apps()
    apps = list(apps or ())
    for app in apps:
        if app not in allowed:
            raise ValueError(f"保存できません。部署の開ける部品に選べない名前です: {app}")
    return Department.objects.create(
        name=name,
        apps=apps,
        default_permissions=clean_permissions(default_permissions),
        sort_order=sort_order,
    )


@transaction.atomic
def update_department(target, data, user) -> Department:
    """部署を直す。data の鍵は name・apps・default_permissions・sort_order (無い鍵は今のまま)。

    表から読み直して直す (渡された物を書き換えない)。既にいる人の権限と部署の付け外しには触らない
    (N36 の約束「部署と権限は別々に付け外しする」)。記録の表には残さない (create_department と同じ)。
    user は変えた人 (今は使わない。引数の形は入力の土台のため)。返すのは直した部署 (土台が詳細へ移す)。
    """
    row = Department.objects.select_for_update().get(pk=target.pk)
    data = dict(data or {})
    values = {}
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise ValueError("保存できません。" + DEPARTMENT_NAME_EMPTY)
        if Department.objects.filter(name=name).exclude(pk=row.pk).exists():
            raise ValueError("保存できません。" + DEPARTMENT_NAME_USED.format(name=name))
        values["name"] = name
    if "apps" in data:
        values["apps"] = clean_apps(data["apps"])
    if "default_permissions" in data:
        values["default_permissions"] = clean_permissions(data["default_permissions"])
    if "sort_order" in data:
        sort_order = data["sort_order"]
        if isinstance(sort_order, bool):
            raise ValueError("保存できません。" + DEPARTMENT_SORT_ORDER)
        try:
            sort_order = int(sort_order)
        except (TypeError, ValueError):
            raise ValueError("保存できません。" + DEPARTMENT_SORT_ORDER) from None
        if sort_order < 0:
            raise ValueError("保存できません。" + DEPARTMENT_SORT_ORDER)
        values["sort_order"] = sort_order
    for key, value in values.items():
        setattr(row, key, value)
    row.save()
    return row


@transaction.atomic
def delete_department(target, user) -> None:
    """部署を消す。生きている人が 1 人でも付いていれば断る (何も変えない。先にアカウント管理で人の部署を外す)。

    消すときは、削除済みのアカウントとのつながりの行だけを Django が消す (記録の表の前と後は部署の名前の字なので残る)。
    既にいる人の権限には触らない。記録の表には残さない。返すのは None (入力の土台が一覧へ移す)。
    """
    row = Department.objects.select_for_update().get(pk=target.pk)
    count = row.accesses.filter(deleted_at__isnull=True).count()
    if count:
        raise ValueError("削除できません。" + DEPARTMENT_IN_USE.format(name=row.name, count=count))
    row.delete()


# ---------------------------------------------------------------- アカウント


@transaction.atomic
def issue_account(login_id, password, departments, permissions, flags, user):
    """アカウントを発行する。flags は see_money・full_control・manage_accounts・is_active (既定は真) の辞書。"""
    User = get_user_model()
    login_id = clean_login_id(login_id)
    if User._default_manager.filter(username__iexact=login_id).exists():
        raise ValueError("発行できません。" + LOGIN_ID_USED)
    permissions = clean_permissions(permissions)
    departments = clean_departments(departments)
    flags = dict(flags or {})
    check_password(password, User(username=login_id))
    account = User.objects.create_user(login_id, password=password, is_active=bool(flags.get("is_active", True)))
    row = Access.objects.create(
        user=account,
        write_permissions=permissions,
        **{key: bool(flags.get(key, False)) for key in FLAG_KEYS},
    )
    row.departments.set(departments)
    _log(user, row, AccessLog.ACTION_ISSUE, {}, password_changed=True)
    return account


@transaction.atomic
def update_account(target, data, user, *, password=None):
    """アカウントを変える。data の鍵は UPDATE_KEYS (無い鍵は今のまま)。パスワードは None か空なら変えない。"""
    row = _locked(target)
    account = row.user
    data = dict(data or {})
    departments = clean_departments(data["departments"]) if "departments" in data else None
    permissions = clean_permissions(data["permissions"]) if "permissions" in data else None
    flags = {key: bool(data[key]) for key in FLAG_KEYS if key in data}
    is_active = bool(data["is_active"]) if "is_active" in data else account.is_active

    before_manager = _counts_as_manager(row)
    after_manager = _counts_as_manager(
        row,
        is_active=is_active,
        manage_accounts=flags.get("manage_accounts"),
        full_control=flags.get("full_control"),
    )
    if _is_self(row, user):
        if account.is_active and not is_active:
            raise ValueError("保存できません。" + SELF_DISABLE)
        had = row.manage_accounts or row.full_control
        has = flags.get("manage_accounts", row.manage_accounts) or flags.get("full_control", row.full_control)
        if had and not has:
            raise ValueError("保存できません。" + SELF_MANAGE)
        if any(flags.get(key) and not getattr(row, key) for key in ("full_control", "see_money")):
            raise ValueError("保存できません。" + SELF_GRANT)
    if before_manager and not after_manager and manager_count() <= 1:
        raise ValueError("保存できません。" + LAST_MANAGER)
    if password:
        check_password(password, account)

    before = snapshot(row)
    if departments is not None:
        row.departments.set(departments)
    if permissions is not None:
        row.write_permissions = permissions
    for key, value in flags.items():
        setattr(row, key, value)
    row.save()
    changed_active = account.is_active != is_active
    account.is_active = is_active
    if password:
        account.set_password(password)
    account.save()
    if changed_active:
        action = AccessLog.ACTION_ENABLE if is_active else AccessLog.ACTION_DISABLE
    else:
        action = AccessLog.ACTION_UPDATE
    _log(user, row, action, before, password_changed=bool(password))
    return account


def disable_account(target, user):
    """使用停止 (update_account の短い形)。"""
    return update_account(target, {"is_active": False}, user)


def enable_account(target, user):
    """再開 (update_account の短い形)。"""
    return update_account(target, {"is_active": True}, user)


@transaction.atomic
def delete_account(target, user):
    """削除 (行は消さない)。deleted_at を入れ、使用停止にし、ログイン ID に印を付けて元の ID を空ける。"""
    row = _locked(target)
    if _is_self(row, user):
        raise ValueError("削除できません。" + SELF_DELETE)
    if _counts_as_manager(row) and manager_count() <= 1:
        raise ValueError("削除できません。" + LAST_MANAGER)
    before = snapshot(row)
    account = row.user
    mark = f"{DELETED_MARK}{account.pk}"
    account.username = account.get_username()[: LOGIN_ID_MAX - len(mark)] + mark
    account.is_active = False
    account.save()
    row.deleted_at = timezone.now()
    row.save()
    _log(user, row, AccessLog.ACTION_DELETE, before)
    return account
