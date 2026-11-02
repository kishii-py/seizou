"""workorders の入力の form。保存は form でなく services が行う (土台が form の save を呼ぶことは無い)。

土台は service_kwargs() の辞書を検査の層の data に使い、同じ辞書で
services.issue_workorder(order_line=..., qty=..., steps=..., user=...) を呼ぶ (01 の 10.4)。
ほかの部品の model は import せず、外部キーの先として引く (読むだけ)。
"""

from django import forms

from .models import WorkOrder, WorkOrderStep

_OrderLine = WorkOrder._meta.get_field("order_line").related_model
_Process = WorkOrderStep._meta.get_field("process").related_model


class OrderLineChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.order.order_no} の {obj.line_no} 行目 {obj.item.part_no} {obj.item.name}"


class ProcessChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.code} {obj.name}"


class WorkOrderIssueForm(forms.Form):
    """指示書の発行。工程を選ばなければ、選んだ明細の品目の標準の工程の並びを写す。"""

    order_line = OrderLineChoiceField(
        label="受注の明細",
        queryset=_OrderLine.objects.exclude(order__status="cancelled").select_related("order", "item"),
    )
    qty = forms.DecimalField(label="数量", max_digits=12, decimal_places=2)
    processes = ProcessChoiceField(
        label="工程",
        queryset=_Process.objects.all(),
        required=False,
        help_text="選ばなければ、品目の標準の工程の並びを写します。予定の数は工程ごとに数量と同じです。",
    )

    def service_kwargs(self):
        line = self.cleaned_data["order_line"]
        qty = self.cleaned_data["qty"]
        processes = list(self.cleaned_data.get("processes") or ())
        if not processes:
            standard = line.item.item_processes.select_related("process").order_by("seq")
            processes = [row.process for row in standard]
        return {
            "order_line": line,
            "qty": qty,
            "steps": [{"process": process, "planned_qty": qty} for process in processes],
        }
