"""業務マニュアルの入力の form (全部 forms.Form)。

ModelForm (ServiceModelForm) にしない: ModelForm は検査の時に、土台が渡した対象の物へ form の値を書き写す。
services は行を取り直す (保存前の中身を版に残すため) が、form の側でも対象を書き換えない形にする。
選ぶ欄の queryset は __init__ で決める (class の中で DB を読まない)。
書く処理は services の関数だけ (R13)。引数は service_kwargs() が作る。
"""

from __future__ import annotations

from django import forms
from django.apps import apps
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from seizou.core.widgets import RemoteSelect

from . import services
from .models import COLOR_CHOICES, FORMAT_CHOICES, FORMAT_STEPS, Manual, ManualFolder
from .services import FOLDER_NOT_FOUND

FORMAT_HELP = "ステップ形式は番号付きの手順、ページ形式は長い文書向けです。後から切り替えられます。"
NOTE_HELP = "新しい版として保存するときだけ残ります (「保存」ではメモは残りません)。"
LINK_ONE_ONLY = "紐付けは 1 件だけです。部品か取引先のどちらかを選んでください。"
#: 紐付けの欄 (欄の名前, 部品の app, model, label, 候補の口の URL の名前)
LINK_FIELDS = (
    ("linked_item", "masters", "Item", "紐付け (部品)", "masters:item_select"),
    ("linked_partner", "masters", "Partner", "紐付け (取引先)", "masters:partner_select"),
)
#: 本文の欄の class (_body.html の comment にも同じ字を並べる)
BODY_WIDGET_CLASS = "textarea w-full font-mono"


class FolderChoiceField(forms.ModelChoiceField):
    """フォルダを選ぶ欄。選ぶ字はルートからの道 (同じ名前のフォルダを見分けるため)。空はルート。"""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("queryset", None)
        kwargs.setdefault("empty_label", "トップ")
        super().__init__(*args, **kwargs)

    def label_from_instance(self, obj):
        return obj.path_text()


def folder_queryset():
    return ManualFolder.objects.select_related("parent__parent__parent").order_by("name", "pk")


class FolderForm(forms.Form):
    """新しいフォルダ。置き場 (parent) は一覧の今のフォルダ (?parent=<pk>) を隠した欄で持つ。"""

    parent = FolderChoiceField(label="フォルダ", required=False, widget=forms.HiddenInput)
    name = forms.CharField(label="名前")
    color = forms.ChoiceField(label="色", choices=COLOR_CHOICES, initial="gray")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = folder_queryset()

    def clean(self):
        cleaned = super().clean()
        if self.has_error("parent"):
            # 隠した欄の誤りは画面に出ないので、欄の外の誤りにする。
            self._errors.pop("parent", None)
            self.add_error(None, FOLDER_NOT_FOUND)
        return cleaned

    def service_kwargs(self):
        data = self.cleaned_data
        return {"parent": data.get("parent"), "name": data["name"], "color": data["color"]}


class ManualCreateForm(forms.Form):
    """マニュアルの新規登録 (置き場・題・形式)。置き場の初期値は一覧の今のフォルダ (?folder=<pk>)。"""

    folder = FolderChoiceField(label="フォルダ", required=False)
    title = forms.CharField(label="題")
    format = forms.ChoiceField(label="形式", choices=FORMAT_CHOICES, initial=FORMAT_STEPS, help_text=FORMAT_HELP)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["folder"].queryset = folder_queryset()

    def service_kwargs(self):
        data = self.cleaned_data
        return {"folder": data.get("folder"), "title": data["title"], "format": data["format"]}


class ManualForm(forms.Form):
    """マニュアルの編集 (題・形式・置き場・本文・紐付け)。本文の前後の空白と字下げは消さない。上限は services が見る。

    版のメモの欄は「新しい版として保存」のボタンの横に出す。「保存」(この form) はメモを渡さない。
    紐付けは部品か取引先の 1 件 (候補の口は masters の名前だけを使う。masters の model は apps から引く)。
    """

    title = forms.CharField(label="題")
    format = forms.ChoiceField(label="形式", choices=FORMAT_CHOICES, help_text=FORMAT_HELP)
    folder = FolderChoiceField(label="フォルダ", required=False)
    body = forms.CharField(
        label="本文 (Markdown)",
        required=False,
        strip=False,
        widget=forms.Textarea(attrs={"rows": 24, "class": BODY_WIDGET_CLASS}),
    )
    note = forms.CharField(label="版のメモ", required=False, help_text=NOTE_HELP)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        self.fields["folder"].queryset = folder_queryset()
        linked = instance.linked_object() if instance is not None else None
        self.link_models = {}
        for name, app_label, model_name, label, url_name in LINK_FIELDS:
            try:
                model = apps.get_model(app_label, model_name)
                reverse(url_name)
            except (LookupError, NoReverseMatch):
                # 部品か候補の口が無ければ、その欄を作らない。
                continue
            self.link_models[name] = model
            self.fields[name] = forms.ModelChoiceField(
                label=label,
                required=False,
                queryset=model._default_manager.all(),
                widget=RemoteSelect(url_name),
            )
        # 欄の無い種類の記録に紐付いているときは、保存で紐付けを外さない。
        self.keep_link = linked is not None and not any(isinstance(linked, m) for m in self.link_models.values())
        if instance is not None and not self.is_bound:
            initial = {
                "title": instance.title,
                "format": instance.format,
                "folder": instance.folder_id,
                "body": instance.body,
            }
            for name, model in self.link_models.items():
                if isinstance(linked, model):
                    initial = {**initial, name: linked.pk}
            self.initial = {**self.initial, **initial}

    def clean(self):
        cleaned = super().clean()
        chosen = [name for name in self.link_models if cleaned.get(name) is not None]
        if len(chosen) > 1:
            self.add_error(None, LINK_ONE_ONLY)
        return cleaned

    def manual_data(self) -> dict:
        data = self.cleaned_data
        values = {
            "title": data["title"],
            "format": data["format"],
            "folder": data.get("folder"),
            "body": data.get("body", ""),
        }
        chosen = next((data[name] for name in self.link_models if data.get(name) is not None), None)
        if chosen is None and self.keep_link:
            return values
        return {**values, "linked": chosen}

    def service_kwargs(self):
        return {"data": self.manual_data()}


class ManualVersionForm(ManualForm):
    """「新しい版として保存」。編集の画面の同じ form から送る。版のメモを渡す。"""

    def service_kwargs(self):
        return {"data": self.manual_data(), "note": self.cleaned_data.get("note", "")}


# --- 確認の画面 (confirm.html) の form。対象を持ち、画面の字と戻り先を属性で返す ---


def local_text(value) -> str:
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M") if value else ""


def detail_url_of(obj) -> str:
    if isinstance(obj, ManualFolder):
        return reverse("manuals:manualfolder_detail", args=[obj.pk])
    return reverse("manuals:manual_detail", args=[obj.pk])


def name_of(obj) -> str:
    return obj.name if isinstance(obj, ManualFolder) else obj.title


class ConfirmForm(forms.Form):
    """確認の画面の form の土台。confirm_text・confirm_label・danger・cancel_url・refusal を持つ。"""

    danger = False
    #: 押す前に分かる断りの字。空でなければ manuals/confirm.html は form と送るボタンを出さず、この字を黄で出す
    refusal = ""

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance

    @property
    def cancel_url(self) -> str:
        return detail_url_of(self.instance)

    def service_kwargs(self):
        return {}


class ProtectForm(ConfirmForm):
    """保護と保護の解除。隠した欄 on は今の逆 (2 回押しても同じ結果)。"""

    on = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, instance=instance, **kwargs)
        self.fields["on"].initial = not instance.protected

    @property
    def turning_on(self) -> bool:
        return not self.instance.protected

    @property
    def confirm_text(self) -> str:
        if self.turning_on:
            return f"「{name_of(self.instance)}」を保護します。保護すると移動と削除ができなくなります。"
        return f"「{name_of(self.instance)}」の保護を解除します。"

    @property
    def confirm_label(self) -> str:
        return "保護" if self.turning_on else "保護を解除"

    def service_kwargs(self):
        return {"on": bool(self.cleaned_data.get("on"))}


class FolderDeleteForm(ConfirmForm):
    danger = True
    confirm_label = "削除"
    with_contents = forms.BooleanField(
        label="中身も削除する (中のフォルダとマニュアルと、その版と画像)", required=False
    )

    def __init__(self, *args, instance=None, **kwargs):
        # instance の名を引数に置く (土台の _accepts_instance が、この名があるかで対象のフォルダを渡すかを決める)。
        super().__init__(*args, instance=instance, **kwargs)
        self.contents = services.folder_contents(instance)
        if not self.contents["folders"] and not self.contents["manuals"]:
            del self.fields["with_contents"]

    @property
    def refusal(self) -> str:
        """中に保護されたフォルダかマニュアルがあれば、押す前に見せる断りの字 (無ければ空の字)。

        字は delete_folder が押した後に断る字と同じ (services.FOLDER_PROTECTED_INSIDE の 1 か所)。
        訳: 押した後に断られる削除を、押す前の確かめの画面で分かるようにするため (断る条件は delete_folder のまま)。出所 = 押す前に見せることは AI の案 (依頼書 N231 = 候補 C73。UX の見直しの案 confirm_print-06)。中に保護された物があると削除しない決まり自体の出所は未確認。
        変えてよい条件 = delete_folder の断る条件を変えるときは一緒に変える。ほかは Issue「決まりの提案」でこの訳に対して議論してから。
        """
        if self.contents["protected"]:
            return "削除できません。" + services.FOLDER_PROTECTED_INSIDE
        return ""

    @property
    def confirm_text(self) -> str:
        head = f"フォルダ「{self.instance.name}」を削除します。"
        folders, manuals = self.contents["folders"], self.contents["manuals"]
        if not folders and not manuals:
            return head + "中は空です。元に戻せません。"
        parts = []
        if folders:
            parts.append(f"フォルダ {folders} 個")
        if manuals:
            parts.append(f"マニュアル {manuals} 件")
        inside = "と".join(parts)
        return head + f"中に{inside}があります。消すときは「中身も削除する」に印を付けます。元に戻せません。"

    def service_kwargs(self):
        return {"with_contents": bool(self.cleaned_data.get("with_contents"))}


class ManualDeleteForm(ConfirmForm):
    danger = True
    confirm_label = "削除"

    @property
    def confirm_text(self) -> str:
        return f"「{self.instance.title}」を削除します。版と画像も削除し、元に戻せません。"


class RestoreForm(ConfirmForm):
    confirm_label = "この版に戻す"

    @property
    def confirm_text(self) -> str:
        version = self.instance
        return f"版 {version.number} ({local_text(version.saved_at)}) の中身に戻します。今の中身は版として残ります。"

    @property
    def cancel_url(self) -> str:
        return reverse("manuals:manualversion_list", args=[self.instance.manual_id])


# --- 名前と色の変更・移動 (core/form.html) ---


class FolderEditForm(forms.Form):
    name = forms.CharField(label="名前")
    color = forms.ChoiceField(label="色", choices=COLOR_CHOICES)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        if instance is not None and not self.is_bound:
            self.initial = {**self.initial, "name": instance.name, "color": instance.color}

    def service_kwargs(self):
        return {"name": self.cleaned_data["name"], "color": self.cleaned_data["color"]}


class FolderMoveForm(forms.Form):
    """フォルダの移動。選べるのはルートと、自分と自分の子孫の外のフォルダ。"""

    parent = FolderChoiceField(label="移動先", required=False)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        excluded = {instance.pk, *services.descendant_ids(instance)} if instance is not None else set()
        self.fields["parent"].queryset = folder_queryset().exclude(pk__in=excluded)
        if instance is not None and not self.is_bound:
            self.initial = {**self.initial, "parent": instance.parent_id}

    def service_kwargs(self):
        return {"parent": self.cleaned_data.get("parent")}


class ManualMoveForm(forms.Form):
    folder = FolderChoiceField(label="移動先", required=False)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        self.fields["folder"].queryset = folder_queryset()
        if isinstance(instance, Manual) and not self.is_bound:
            self.initial = {**self.initial, "folder": instance.folder_id}

    def service_kwargs(self):
        return {"folder": self.cleaned_data.get("folder")}
