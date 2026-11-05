"""notes の入力の form。表に書くのは services だけ (R13)。form は値を見て、services の引数を作る。"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, ValidationError

from seizou.parts import PARTS

from . import services

MESSAGE_TARGET_NOT_FOUND = "メモを付ける先の記録が見つかりません。記録の詳細の画面を開き直してください。"


def noteable_types():
    """メモを付けられる記録の種類: seizou の部品の表 (PARTS の app) だけ。メモ自身と利用者の表は除く。

    呼んだ時に組み立てる (import の時に DB を読まない)。
    """
    labels = [part.rsplit(".", 1)[-1] for part in PARTS if part != "seizou.notes"]
    user_meta = get_user_model()._meta
    return ContentType.objects.filter(app_label__in=labels).exclude(
        app_label=user_meta.app_label, model=user_meta.model_name
    )


def target_of(content_type_id, object_id):
    """種類の番号と記録の番号から、付ける先の記録を引く (読むだけ)。引けなければ None。"""
    try:
        content_type = noteable_types().get(pk=int(content_type_id))
        model = content_type.model_class()
        if model is None:
            return None
        return model._default_manager.get(pk=int(object_id))
    except (TypeError, ValueError, ObjectDoesNotExist):
        return None


class NoteForm(forms.Form):
    """メモを書く。付ける先 (種類と番号) は隠した欄で、メモの枠の「書く」のボタンが GET で渡す。

    本文 (body_html) は Quill の編集の箱の HTML を、保存の前に JS が隠した欄へ写す。掃除は services がする。
    """

    content_type = forms.ModelChoiceField(queryset=ContentType.objects.none(), widget=forms.HiddenInput)
    object_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    body_html = forms.CharField(label="本文", required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["content_type"].queryset = noteable_types()
        self.target = None
        if not self.is_bound:
            target = target_of(self.initial.get("content_type"), self.initial.get("object_id"))
            if target is not None:
                self.target = target
                self.initial["body_html"] = services.note_for(target).body_html

    def clean(self):
        cleaned = super().clean()
        content_type = cleaned.get("content_type")
        object_id = cleaned.get("object_id")
        target = target_of(content_type.pk, object_id) if content_type is not None and object_id else None
        if target is None:
            raise ValidationError(MESSAGE_TARGET_NOT_FOUND)
        self.target = target
        return cleaned

    def service_kwargs(self) -> dict:
        """services.save_note の引数 (付ける先は clean() で解いた物)。"""
        return {"target": self.target, "html": self.cleaned_data.get("body_html", "")}
