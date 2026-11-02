"""progress の入力の form。保存は form でなく services が行う (土台が form の save を呼ぶことは無い)。

土台は service_kwargs() の辞書を検査の層の data に使い、同じ辞書で
services.record_progress(workorder_step=..., qty=..., recorded_at=..., user=..., note=...) を呼ぶ (01 の 10.4)。
ほかの部品の model は import せず、外部キーの先として引く (読むだけ)。
"""

from django import forms
from django.utils import timezone

from .models import Progress

_WorkOrderStep = Progress._meta.get_field("workorder_step").related_model


class ProgressRecordForm(forms.Form):
    """実績の入力。日時の既定は今。予定を超えた実数も入れられる (現物優先)。"""

    workorder_step = forms.ModelChoiceField(
        label="指示書の工程",
        queryset=_WorkOrderStep.objects.select_related("workorder", "process"),
    )
    qty = forms.DecimalField(label="実績の数", max_digits=12, decimal_places=2)
    recorded_at = forms.DateTimeField(label="日時", initial=timezone.now)
    note = forms.CharField(label="備考", max_length=500, required=False)

    def service_kwargs(self):
        return {
            "workorder_step": self.cleaned_data["workorder_step"],
            "qty": self.cleaned_data["qty"],
            "recorded_at": self.cleaned_data["recorded_at"],
            "note": self.cleaned_data["note"],
        }
