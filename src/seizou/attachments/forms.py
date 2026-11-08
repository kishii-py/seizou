"""attachments の入力の form。表に書くのは services だけ (R13)。form は値を見て、services の引数を作る。"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, ValidationError

from seizou.parts import PARTS

from . import services
from .models import KIND_CHOICES

MESSAGE_TARGET_NOT_FOUND = (
    "付ける先の記録が見つかりません。記録の詳細の画面の「添付ファイルを付ける」から開き直してください。"
)


def attachable_types():
    """付けられる記録の種類: seizou の部品の表 (PARTS の app) だけ。添付ファイル自身と利用者の表は除く。

    呼んだ時に組み立てる (import の時に DB を読まない)。
    """
    labels = [part.rsplit(".", 1)[-1] for part in PARTS if part != "seizou.attachments"]
    user_meta = get_user_model()._meta
    return ContentType.objects.filter(app_label__in=labels).exclude(
        app_label=user_meta.app_label, model=user_meta.model_name
    )


class AttachmentForm(forms.Form):
    """添付ファイルを付ける。付ける先 (種類と番号) は隠した欄で、記録の詳細の枠の link が GET で渡す。

    付ける先は clean() で解く。隠した欄の誤りは入力の画面に出ないので、欄の外の誤りにまとめる。
    付ける先の字は入力の頭の 1 行に出す (target_text_for(利用者)。form は利用者を持たないので、テンプレートの tag が渡す)。
    引けないときと、利用者が付ける先の記録を開けないときは空。
    """

    content_type = forms.ModelChoiceField(queryset=ContentType.objects.none(), widget=forms.HiddenInput)
    object_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    file = forms.FileField(
        label="ファイル",
        help_text="PNG・JPEG・WebP・PDF・TIFF・CSV・テキスト・.xlsx・.docx のファイルを、20MB まで付けられます。",
    )
    kind = forms.ChoiceField(label="種類", choices=KIND_CHOICES, initial="other")
    note = forms.CharField(label="備考", required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["content_type"].queryset = attachable_types()
        self.target = None
        self.target_preview = self._target_preview()

    def _target_preview(self):
        """入力の頭の 1 行に出す付ける先の記録。引けなければ None。

        付ける先は送られた値か、開いたときの初期値 (土台の get_initial が GET の字のまま写す)。どちらかが数の字でないとき・
        付けられない種類のとき・記録が無いときは None (断ることはしない。保存のときは今どおり clean() が誤りを出す)。
        """
        source = self.data if self.is_bound else self.initial
        raw_type, raw_id = source.get("content_type"), source.get("object_id")
        # 数の字だけを見る (isdecimal。isdigit は「²」も通し、int() で落ちる)
        if raw_type is None or raw_id is None or not str(raw_type).isdecimal() or not str(raw_id).isdecimal():
            return None
        content_type = self.fields["content_type"].queryset.filter(pk=int(raw_type)).first()
        if content_type is None or content_type.model_class() is None:
            return None
        try:
            return content_type.get_object_for_this_type(pk=int(raw_id))
        except ObjectDoesNotExist:
            return None

    def target_text_for(self, user) -> str:
        """入力の頭に出す付ける先の字「<種類の名前> <記録の字>」(一覧の「付けた先」の列と同じ記録の字)。

        付ける先が引けないときと、user が付ける先の記録を開けないとき (services.can_open_target) は空の字。
        訳: 開けない記録の題やログイン ID を、GET の種類と番号を変えるだけで読ませないため。
        出所 = Claude Security の走査の指摘 (11-07)。変えてよい条件 = services.can_open_target と同じ。
        """
        target = self.target_preview
        if target is None or not services.can_open_target(target, user):
            return ""
        return f"{target._meta.verbose_name} {target}"

    def clean(self):
        cleaned = super().clean()
        content_type = cleaned.get("content_type")
        object_id = cleaned.get("object_id")
        target = None
        if content_type is not None and object_id is not None and content_type.model_class() is not None:
            try:
                target = content_type.get_object_for_this_type(pk=object_id)
            except ObjectDoesNotExist:
                target = None
        if target is None:
            raise ValidationError(MESSAGE_TARGET_NOT_FOUND)
        self.target = target
        return cleaned

    def service_kwargs(self) -> dict:
        """services.add_attachment の引数 (付ける先は clean() で解いた物。ここでは解かない)。"""
        return {
            "target": self.target,
            "file": self.cleaned_data["file"],
            "kind": self.cleaned_data["kind"],
            "note": self.cleaned_data.get("note", ""),
        }


class AttachmentDeleteForm(forms.Form):
    """添付ファイルの削除の確認。欄は無い (土台が remove_attachment(対象, user=…) を呼ぶ)。"""

    def service_kwargs(self) -> dict:
        return {}
