"""業務マニュアルの入力の form (全部 forms.Form)。

ModelForm (ServiceModelForm) にしない: ModelForm は検査の時に、土台が渡した対象の物へ form の値を書き写す。
services は行を取り直す (保存前の中身を版に残すため) が、form の側でも対象を書き換えない形にする。
選ぶ欄の queryset は __init__ で決める (class の中で DB を読まない)。
書く処理は services の関数だけ (R13)。引数は service_kwargs() が作る。
"""

from __future__ import annotations

from django import forms

from .models import COLOR_CHOICES, FORMAT_CHOICES, FORMAT_STEPS, ManualFolder
from .services import FOLDER_NOT_FOUND

FORMAT_HELP = "ステップ形式は番号付きの手順、ページ形式は長い文書向けです。後から切り替えられます。"
#: 本文の欄の class (_body.html の comment にも同じ字を並べる)
BODY_WIDGET_CLASS = "textarea w-full font-mono"


class FolderChoiceField(forms.ModelChoiceField):
    """フォルダを選ぶ欄。選ぶ字はルートからの道 (同じ名前のフォルダを見分けるため)。空はルート。"""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("queryset", None)
        kwargs.setdefault("empty_label", "ルート")
        super().__init__(*args, **kwargs)

    def label_from_instance(self, obj):
        return obj.path_text()


def folder_queryset():
    return ManualFolder.objects.select_related("parent__parent__parent").order_by("name", "pk")


class FolderForm(forms.Form):
    """新しいフォルダ。置き場 (parent) は一覧の今のフォルダ (?parent=<pk>) を隠した欄で持つ。"""

    parent = FolderChoiceField(label="置き場", required=False, widget=forms.HiddenInput)
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

    folder = FolderChoiceField(label="置き場", required=False)
    title = forms.CharField(label="題")
    format = forms.ChoiceField(label="形式", choices=FORMAT_CHOICES, initial=FORMAT_STEPS, help_text=FORMAT_HELP)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["folder"].queryset = folder_queryset()

    def service_kwargs(self):
        data = self.cleaned_data
        return {"folder": data.get("folder"), "title": data["title"], "format": data["format"]}


class ManualForm(forms.Form):
    """マニュアルの編集 (題・形式・置き場・本文)。本文の前後の空白と字下げは消さない。上限は services が見る。"""

    title = forms.CharField(label="題")
    format = forms.ChoiceField(label="形式", choices=FORMAT_CHOICES, help_text=FORMAT_HELP)
    folder = FolderChoiceField(label="置き場", required=False)
    body = forms.CharField(
        label="本文 (Markdown)",
        required=False,
        strip=False,
        widget=forms.Textarea(attrs={"rows": 24, "class": BODY_WIDGET_CLASS}),
    )

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        self.fields["folder"].queryset = folder_queryset()
        if instance is not None and not self.is_bound:
            self.initial = {
                **self.initial,
                "title": instance.title,
                "format": instance.format,
                "folder": instance.folder_id,
                "body": instance.body,
            }

    def manual_data(self) -> dict:
        data = self.cleaned_data
        return {
            "title": data["title"],
            "format": data["format"],
            "folder": data.get("folder"),
            "body": data.get("body", ""),
        }

    def service_kwargs(self):
        return {"data": self.manual_data()}
