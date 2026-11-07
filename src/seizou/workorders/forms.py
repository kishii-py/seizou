"""workorders の入力の form。保存は form でなく services が行う (土台が form の save を呼ぶことは無い)。

土台は service_kwargs() の辞書を検査の層の data に使い、同じ辞書で
services.issue_workorder(order_line=..., qty=..., steps=..., user=...) を呼ぶ (01 の 10.4)。
ほかの部品の model は import せず、外部キーの先として引く (読むだけ)。
"""

from django import forms
from django.db.models import Q

from seizou.core.forms import CHECKBOX_CSS, Checkboxes
from seizou.core.widgets import RemoteSelect
from seizou.masters.services import active_processes

from .models import WorkOrder, WorkOrderStep

_OrderLine = WorkOrder._meta.get_field("order_line").related_model


class ProcessChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.code} {obj.name}"


class WorkOrderIssueForm(forms.Form):
    """指示書の発行。工程を選ばなければ、選んだ明細の部品の標準の工程の並びを写す。"""

    submit_label = "発行する"

    order_line = forms.ModelChoiceField(
        label="受注の明細",
        queryset=_OrderLine.objects.exclude(order__status="cancelled").select_related("order", "item"),
        widget=RemoteSelect("orders:orderline_select", query={"for": "workorder"}),
    )
    qty = forms.DecimalField(label="数量", max_digits=12, decimal_places=2)
    processes = ProcessChoiceField(
        label="工程",
        queryset=None,
        required=False,
        widget=Checkboxes(attrs={"class": CHECKBOX_CSS}),
        help_text="選ばなければ、部品の標準の工程の並びを写します (使用停止の工程は写しません)。予定の数は工程ごとに数量と同じです。",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 工程は使用中だけ (N140。発行は新しく作るだけの form なので keep を使わない)
        self.fields["processes"].queryset = active_processes()
        # 取消した受注の明細は選択肢に出さない。ただし選んで届いた明細 (POST の値か、新規登録の GET の ?order_line= の初期値) は
        # 選択肢に残す。保存のときに services.issue_workorder が R05 の赤の文で止める (出荷の入力と同じ知らせ)。
        # Django の「正しく選択してください」で、選んだ明細が欄から消える形にしない (初見テスト 3 回目)。
        chosen = self.data.get(self.add_prefix("order_line")) if self.is_bound else self.initial.get("order_line")
        pk = str(getattr(chosen, "pk", chosen) or "")
        if pk.isdecimal():
            field = self.fields["order_line"]
            field.queryset = _OrderLine.objects.filter(~Q(order__status="cancelled") | Q(pk=int(pk))).select_related("order", "item")

    def service_kwargs(self):
        line = self.cleaned_data["order_line"]
        qty = self.cleaned_data["qty"]
        processes = list(self.cleaned_data.get("processes") or ())
        if not processes:
            # 使用停止の工程は写さない (N140)。部品の標準の工程の並びの行は消さないので、使用中に戻すとまた写る。
            standard = line.item.item_processes.filter(process__active=True).select_related("process").order_by("seq")
            processes = [row.process for row in standard]
        return {
            "order_line": line,
            "qty": qty,
            "steps": [{"process": process, "planned_qty": qty} for process in processes],
        }


class PlanPlaceForm(forms.Form):
    """生産計画の盤で日を置く・動かす・戻す (plan_place が使う。書くのは services.plan_step)。

    step は予定の全部 (取消の物も入れる。R05 の赤の知らせを出すため)。
    end が送られなければ clean() が決める: start が空なら end も空 (未計画に戻す)。
    予定に既に日があれば同じ日数ずらす (動かす)。無ければ start と同じ日 (置く = 1 日)。
    """

    step = forms.ModelChoiceField(label="工程の予定", queryset=WorkOrderStep.objects.all())
    start = forms.DateField(label="開始日", required=False)
    end = forms.DateField(label="終了日", required=False)

    def clean(self):
        cleaned = super().clean()
        step = cleaned.get("step")
        if step is None or self.has_error("start") or self.has_error("end"):
            return cleaned
        if self.data.get("end"):
            return cleaned
        start = cleaned.get("start")
        if start is None:
            cleaned["end"] = None
        elif step.planned_start is not None and step.planned_end is not None:
            cleaned["end"] = start + (step.planned_end - step.planned_start)
        else:
            cleaned["end"] = start
        return cleaned


class WorkOrderCancelForm(forms.Form):
    """指示書の取消。理由は指示書の「取消の理由」に残る (services.cancel_workorder)。保存のボタンは「WO-000001 を取り消す」(N138)。"""
    submit_label = "取り消す"
    danger = True  # 実行のボタンを削除の確かめと同じ赤 (btn btn-error) にする (core/_form_card.html。初見テスト R5 の U125。候補 C24)

    reason = forms.CharField(
        label="取消の理由",
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="書くと、指示書の『取消の理由』に残ります。",
    )

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        if instance is not None:
            self.submit_label = f"{instance.wo_no} を取り消す"

    def service_kwargs(self) -> dict:
        return {"reason": self.cleaned_data["reason"]}


class WorkOrderFindForm(forms.Form):
    """上の帯の「現品票を読む」の小窓の「指示書」の欄 (カメラの無い端末で、指示書番号か品番を打って選ぶ)。

    保存しない。GET で工程の行を引くだけ (workorders.views.workorder_reader)。入力の土台を通らないので、
    字を打って探す欄の class (core.views.widget_css と同じ字) は欄に自分で付ける。
    """

    workorder = forms.ModelChoiceField(
        label="指示書",
        queryset=WorkOrder.objects.all(),
        required=False,
        widget=RemoteSelect("workorders:workorder_select", attrs={"class": "w-full"}),
        help_text="カメラが使えないときは、指示書番号か品番を打って選びます。",
    )
