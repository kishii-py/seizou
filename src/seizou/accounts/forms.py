"""アカウント管理の入力 (02 の 7 節)。form は欄と、services の引数 (service_kwargs) を作るだけ。

表を読むのは __init__ の中だけ (部署の候補と、画面の小さな script が読む辞書)。書くのは services だけ (R13)。
"""

from django import forms

from seizou.core import access
from seizou.core.forms import CHECKBOX_CSS, Checkboxes
from seizou.core.screens import all_screens

from . import services
from .models import Department, app_labels

PASSWORD_HELP_NEW = "8 字以上。よくある物・数字だけ・ログイン ID に似た物は使えません。"
PASSWORD_HELP_EDIT = PASSWORD_HELP_NEW + "空のままなら変わりません。"


def permission_choices() -> list[tuple[str, str]]:
    """権限の欄の選び (まとめ 2 つ → 種類 10。種類には中身の注記を添える)。"""
    choices = []
    for name in access.PERMISSION_NAMES:
        label = access.permission_label(name)
        note = access.KIND_NOTES.get(name)
        choices.append((name, f"{label} ({note})" if note else label))
    return choices


class AccountForm(forms.Form):
    """アカウントの発行と編集。instance (Access) があれば編集 (ログイン ID は変えない)。"""

    login_id = forms.CharField(label="ログイン ID", max_length=150)
    password = forms.CharField(label="パスワード", strip=False, widget=forms.PasswordInput(render_value=False))
    departments = forms.ModelMultipleChoiceField(
        label="部署",
        queryset=Department.objects.none(),
        required=False,
        widget=Checkboxes(attrs={"class": CHECKBOX_CSS}),
        help_text="部署に チェックを入れると、その部署の既定の権限に チェックが入ります。",
    )
    permissions = forms.MultipleChoiceField(
        label="権限",
        choices=(),
        required=False,
        widget=Checkboxes(attrs={"class": CHECKBOX_CSS}),
        help_text="まとめ (マスター編集・伝票編集) を付けると、中の種類が全部付きます。",
    )
    see_money = forms.BooleanField(label="金額の閲覧", required=False)
    full_control = forms.BooleanField(
        label="フルコントロール",
        required=False,
        help_text=(
            "部署・編集の権限・金額の閲覧の 3 つの制限を受けません"
            " (部署で開けない画面も開き、権限の無い種類のデータも登録・変更・削除でき、単価と金額が見えます)。"
            "アカウント管理と設定の変更も使えます。設定の「権限の判定」を変えるのはシステム管理者だけです。"
        ),
    )
    manage_accounts = forms.BooleanField(label="アカウント管理", required=False)
    is_active = forms.BooleanField(label="使用中", required=False, initial=True)

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        self.creating = instance is None
        if instance is not None:
            kwargs["initial"] = {
                **(kwargs.get("initial") or {}),
                "login_id": instance.user.get_username(),
                "departments": [d.pk for d in instance.departments.all()],
                "permissions": list(instance.write_permissions or []),
                "see_money": instance.see_money,
                "full_control": instance.full_control,
                "manage_accounts": instance.manage_accounts,
                "is_active": instance.user.is_active,
            }
        super().__init__(*args, **kwargs)
        departments = list(Department.objects.all())
        self.fields["departments"].queryset = Department.objects.all()
        self.fields["permissions"].choices = permission_choices()
        password = self.fields["password"]
        if self.creating:
            password.help_text = PASSWORD_HELP_NEW
        else:
            login_id = self.fields["login_id"]
            login_id.disabled = True
            login_id.required = False
            password.required = False
            password.help_text = PASSWORD_HELP_EDIT
        #: 画面の小さな script が読む辞書 (まとめの中の種類と、部署ごとの既定の権限)
        self.script_data = {
            "groups": {name: list(kinds) for name, (_label, kinds) in access.GROUPS.items()},
            "departments": {str(d.pk): list(d.default_permissions or []) for d in departments},
        }

    def flags(self) -> dict:
        data = self.cleaned_data
        return {key: bool(data.get(key)) for key in ("see_money", "full_control", "manage_accounts", "is_active")}

    def service_kwargs(self) -> dict:
        data = self.cleaned_data
        departments = list(data.get("departments") or [])
        permissions = list(data.get("permissions") or [])
        if self.creating:
            return {
                "login_id": data["login_id"],
                "password": data["password"],
                "departments": departments,
                "permissions": permissions,
                "flags": self.flags(),
            }
        return {
            "data": {"departments": departments, "permissions": permissions, **self.flags()},
            "password": data.get("password") or None,
        }


class AccountDeleteForm(forms.Form):
    """アカウントの削除の確認 (欄なし)。"""

    def service_kwargs(self) -> dict:
        return {}


def department_app_choices() -> list[tuple[str, str]]:
    """部署の「開ける部品」の欄の選び (app の名前, 左のメニューの入口の字)。並びは services.department_apps() (PARTS の順)。

    画面を 1 つも持たない部品 (bookmarks) は、チェックしても何も変わらないので出さない。入口の無い部品 (intake) は部品の表示名。
    """
    with_screens = {screen.app for screen in all_screens()}
    labels = app_labels()
    return [
        (app, labels.get(app) or access.app_label(app)) for app in services.department_apps() if app in with_screens
    ]


class DepartmentForm(forms.Form):
    """部署の新規登録と編集。instance (Department) があれば編集。"""

    name = forms.CharField(label="部署", max_length=50)
    apps = forms.MultipleChoiceField(
        label="開ける画面",
        choices=(),
        required=False,
        widget=Checkboxes(attrs={"class": CHECKBOX_CSS}, columns=True),
        help_text="チェックを入れた画面が、この部署の人の左のメニューに出て開けます。部署が 1 つも無い人は全部の画面が開きます。",
    )
    default_permissions = forms.MultipleChoiceField(
        label="既定の権限",
        choices=(),
        required=False,
        widget=Checkboxes(attrs={"class": CHECKBOX_CSS}),
        help_text="アカウントの発行と編集でこの部署に チェックを入れたときに、自動で チェックが入る権限。既にいる人の権限は変わりません。",
    )
    sort_order = forms.IntegerField(label="並び順", min_value=0, initial=0, help_text="小さい数が先に並びます。")

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        self.creating = instance is None
        if instance is not None:
            kwargs["initial"] = {
                **(kwargs.get("initial") or {}),
                "name": instance.name,
                "apps": list(instance.apps or []),
                "default_permissions": list(instance.default_permissions or []),
                "sort_order": instance.sort_order,
            }
        super().__init__(*args, **kwargs)
        self.fields["apps"].choices = department_app_choices()
        self.fields["default_permissions"].choices = permission_choices()

    def service_kwargs(self) -> dict:
        data = self.cleaned_data
        values = {
            "name": data["name"],
            "apps": list(data.get("apps") or []),
            "default_permissions": list(data.get("default_permissions") or []),
            "sort_order": data["sort_order"],
        }
        if self.creating:
            return values
        return {"data": values}


class DepartmentDeleteForm(forms.Form):
    """部署の削除の確認 (欄なし)。"""

    def service_kwargs(self) -> dict:
        return {}
