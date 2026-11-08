"""progress の入力の form。保存は form でなく services が行う (土台が form の save を呼ぶことは無い)。

土台は service_kwargs() の辞書を検査の層の data に使い、同じ辞書で
services.record_progress(workorder_step=..., qty=..., recorded_at=..., user=..., note=..., defect_qty=..., completes_step=...) を呼ぶ (部品の公開する関数の設計の決め)。
ほかの部品の model は import せず、外部キーの先として引く (読むだけ)。
"""

from decimal import Decimal

from django import forms
from django.utils import timezone

from seizou.core.widgets import RemoteSelect

from . import services
from .models import Progress

_WorkOrderStep = Progress._meta.get_field("workorder_step").related_model

#: 印の付いた工程を選んだときに「指示書の工程」の欄の下に出す字 (作者の決め。断らない・色を変えない)。
#: 実績の新規登録の画面で工程を選び直したときは progress/progress-form.js が同じ字を出し入れする。
STEP_COMPLETED_HELP = "この工程には完了の印が付いています。実績は足せます。"


class ProgressRecordForm(forms.Form):
    """実績の入力。日時の既定は今。予定を超えた実数も入れられる (現物優先)。

    日時はブラウザの日付と時刻の選び (分まで) で、値の形はその欄が受ける「2026-11-06T14:30」(日付と時刻の間が T・秒なし) にする。
    不良の数は空なら 0、「この工程を完了にする」はいつも空で始める (作者の決め。?completes_step=on の付いた URL でも)。
    選んである工程 (送った値か ?workorder_step= の初期値) に完了の印の付いた実績があれば、「指示書の工程」の欄の下に
    STEP_COMPLETED_HELP を出す (作者の決め)。help_text は宣言でなく __init__ で置く (「?」の「入れる物」の表は宣言 = base_fields を読むので、字を変えない)。
    """

    submit_label = "記録する"
    #: 行の頭 (幅 640 以上の 2 列の左) から始める欄 (入力のカード core/_form_card.html が読む)。「実績の数」と「不良の数」を同じ行の左右に並べる。
    row_start_fields = ("qty",)

    workorder_step = forms.ModelChoiceField(
        label="指示書の工程",
        queryset=_WorkOrderStep.objects.select_related("workorder", "process"),
        widget=RemoteSelect("workorders:workorderstep_select"),
    )
    qty = forms.DecimalField(
        label="実績の数",
        max_digits=12,
        decimal_places=2,
        help_text="この工程を終えた良品の数 (不良を除く)。",
    )
    defect_qty = forms.DecimalField(
        label="不良の数",
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=Decimal("0"),
        help_text="この工程で不良になった数。無ければ空のままにします。",
        error_messages={"min_value": "不良の数は 0 以上の数にしてください。"},
    )
    completes_step = forms.BooleanField(
        label="この工程を完了にする",
        required=False,
        help_text="この工程を終えたら付けます。最後の工程に付けると、指示書が「完了」になります。",
    )
    recorded_at = forms.DateTimeField(
        label="日時",
        initial=timezone.now,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    note = forms.CharField(label="備考", max_length=500, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 印はいつも空で始める (作者の決め)。土台の get_initial は新規登録の GET の鍵を全部初期値に写すので、ここで外す。
        self.initial.pop("completes_step", None)
        step_pk = _chosen_pk(self.data.get(self.add_prefix("workorder_step")) if self.is_bound else self.initial.get("workorder_step"))
        if step_pk is not None and services.step_completed(step_pk):
            self.fields["workorder_step"].help_text = STEP_COMPLETED_HELP

    def service_kwargs(self):
        return {
            "workorder_step": self.cleaned_data["workorder_step"],
            "qty": self.cleaned_data["qty"],
            "defect_qty": self.cleaned_data["defect_qty"] or Decimal("0"),
            "completes_step": self.cleaned_data["completes_step"],
            "recorded_at": self.cleaned_data["recorded_at"],
            "note": self.cleaned_data["note"],
        }


class ProgressUpdateForm(forms.Form):
    """実績の編集 (作者の決め)。良品の数・不良の数・完了の印・日時・備考を直す。工程の欄は出さない (別の工程に入れた実績は消して入れ直す)。

    欄の名前・説明・誤りの字は実績の入力 (ProgressRecordForm) と同じ。
    ModelForm にしない。ModelForm は検査の前に新しい値を対象へ書き込み、検査の層 (R07) が直す前の数を読めなくなる。
    土台は名指しの instance に直す前の実績を渡すので、その値を初期値にだけ使う (instance には書き込まない。出荷の編集と同じ形)。
    どの工程の実績かは保存のボタンの字に出す (「<指示書の番号> <順> <工程の名前> の実績を直す」)。
    """

    submit_label = "実績を直す"

    qty = forms.DecimalField(
        label=ProgressRecordForm.base_fields["qty"].label,
        max_digits=12,
        decimal_places=2,
        help_text=ProgressRecordForm.base_fields["qty"].help_text,
    )
    defect_qty = forms.DecimalField(
        label=ProgressRecordForm.base_fields["defect_qty"].label,
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=Decimal("0"),
        help_text=ProgressRecordForm.base_fields["defect_qty"].help_text,
        error_messages={"min_value": ProgressRecordForm.base_fields["defect_qty"].error_messages["min_value"]},
    )
    completes_step = forms.BooleanField(
        label=ProgressRecordForm.base_fields["completes_step"].label,
        required=False,
        help_text=ProgressRecordForm.base_fields["completes_step"].help_text,
    )
    recorded_at = forms.DateTimeField(
        label="日時",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    note = forms.CharField(label="備考", max_length=500, required=False)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        if instance is not None:
            for name in ("qty", "defect_qty", "completes_step", "recorded_at", "note"):
                self.initial.setdefault(name, getattr(instance, name))
            self.submit_label = f"{instance.workorder_step} の実績を直す"

    def service_kwargs(self):
        return {
            "qty": self.cleaned_data["qty"],
            "defect_qty": self.cleaned_data["defect_qty"] or Decimal("0"),
            "completes_step": self.cleaned_data["completes_step"],
            "recorded_at": self.cleaned_data["recorded_at"],
            "note": self.cleaned_data["note"],
        }


class ProgressDeleteForm(forms.Form):
    """実績の削除の確かめ (作者の決め)。欄は無い (土台が delete_progress(対象, user=…) を呼ぶ)。実行のボタンは赤の「削除」。"""

    submit_label = "削除"
    danger = True  # 実行のボタンを削除の確かめと同じ赤 (btn btn-error) にする (core/_form_card.html)

    def service_kwargs(self):
        return {}


def _chosen_pk(value):
    """選んである指示書の工程の pk (送った値か初期値。model・pk・pk の字のどれか)。読めなければ None。"""
    pk = str(getattr(value, "pk", value) or "")
    return int(pk) if pk.isdecimal() else None
