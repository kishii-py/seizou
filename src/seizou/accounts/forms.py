"""アカウント管理の入力 (アカウントと権限の設計の決め)。form は欄と、services の引数 (service_kwargs) を作るだけ。

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

#: 権限の欄の群の見出し (まとめの名前 → 見出し。左のメニューの「マスター」と、まとめ「伝票編集」の字から取る)
PERMISSION_GROUP_HEADINGS = {"master_edit": "マスター", "slip_edit": "伝票"}

#: どの部署でも開ける画面の字 (access.ALWAYS_OPEN_APPS の画面。core の画面は左のメニューの app に無いので、字で書く)
ALWAYS_OPEN_SCREENS = "ダッシュボード・検索・使い方・設定・AI チャット・マニュアル・添付ファイル・フィードバック・メモ"

#: 「部署」の欄の注記 (付けた結果 = 開ける画面と、外したときに残る物。作者の決め)
DEPARTMENTS_HELP = (
    "部署に チェックを入れると、その部署の既定の権限に チェックが入ります。部署を外しても、入った権限は残ります。"
    "括弧の中が、その部署の人が左のメニューで開ける画面です。"
    f"どの部署でも開ける画面 ({ALWAYS_OPEN_SCREENS}) は、部署によらず開けます。"
    "部署が 1 つも無い人は全部の画面が開きます。"
    "部署の外の画面を閉じるのは、設定の「権限の判定」の部署が「有効」のときです (「記録だけ」では開けて、記録に残ります)。"
)

#: 「アカウント管理」の欄の注記 (付けた結果 = できること。access.ACCOUNT_ACTIONS と設定の画面の門 settings_denied と feedback の世話役の判定から書く)
MANAGE_ACCOUNTS_HELP = (
    "アカウントと部署の発行・変更・削除、会社の選べる設定と会社の情報 (請求書の振込先と登録番号を含む) の変更、"
    "設定の「点検を走らせる」と「試しに送る」、GitHub とつなぐ、フィードバックの世話役 (確認済みと送ったにする) ができます。"
    "アカウント管理の画面が開けるようになりますが、ほかの業務の画面を開く範囲と、登録・変更できるデータの種類は増えません。"
    "設定の「権限の判定」を変えるのはシステム管理者だけです。"
)


class DepartmentChoiceField(forms.ModelMultipleChoiceField):
    """部署の check の字に、その部署の人が開ける画面を添える (例「製造 (指示書・生産計画・進捗・実績)」)。

    付けた結果を check の字で見せる (権限の種類の check に KIND_NOTES を添えるのと同じ形。作者の決め)。
    開ける画面の字 (app_labels) は要求ごとに変わるので、form の __init__ が 1 回だけ読んで app_labels に置く。
    開ける画面を 1 つも持たない部署は「(開ける画面なし)」(どの部署でも開ける画面だけが開く)。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app_labels: dict[str, str] = {}

    def label_from_instance(self, obj):
        screens = "・".join(self.app_labels.get(app) or access.app_label(app) for app in (obj.apps or []))
        return f"{obj.name} ({screens or '開ける画面なし'})"


def permission_choices() -> list[tuple[str, list[tuple[str, str]]]]:
    """権限の欄の選び (まとめごとの 2 つの群。群の先頭にまとめ、その下に中の種類 5 つ。種類には中身の注記を添える)。

    画面の並びだけが変わり、保存する並びは services.clean_permissions が access.PERMISSION_NAMES にそろえる。
    """
    choices = []
    for group, (_label, kinds) in access.GROUPS.items():
        options = []
        for name in (group, *kinds):
            label = access.permission_label(name)
            note = access.KIND_NOTES.get(name)
            options.append((name, f"{label} ({note})" if note else label))
        choices.append((PERMISSION_GROUP_HEADINGS[group], options))
    return choices


class AccountForm(forms.Form):
    """アカウントの発行と編集。instance (Access) があれば編集 (ログイン ID は変えない)。"""

    login_id = forms.CharField(label="ログイン ID", max_length=150)
    password = forms.CharField(label="パスワード", strip=False, widget=forms.PasswordInput(render_value=False))
    departments = DepartmentChoiceField(
        label="部署",
        queryset=Department.objects.none(),
        required=False,
        widget=Checkboxes(attrs={"class": CHECKBOX_CSS}),
        help_text=DEPARTMENTS_HELP,
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
    manage_accounts = forms.BooleanField(label="アカウント管理", required=False, help_text=MANAGE_ACCOUNTS_HELP)
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
        self.fields["departments"].app_labels = app_labels()
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
