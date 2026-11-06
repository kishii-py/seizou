"""stock の入力の form (製造の完了を入庫にする・払い出し・棚卸しの調整)。保存は form でなく services が行う。

- 全部 forms.Form。services の引数は service_kwargs() が作る (土台が service(**引数, user=…) で呼ぶ)。
- ほかの部品の model は import しない。指示書は apps.get_model で __init__ の中で引き、部品は StockMove の
  外部キーの先 (related_model) として引く (読むだけ)。
- 部品を選ぶ欄は字を打って探す欄 (RemoteSelect("masters:item_select"))。
- 指示書を選ぶ欄も字を打って探す欄 (RemoteSelect("workorders:workorder_select"))。
- 指示書の欄の候補は発行の指示書だけ。送られた値は全部の指示書で受け、取消の指示書は services が日本語の文で断る
  (出荷の form が全部の明細を受けるのと同じ考え。赤の知らせで理由を出すため)。
- 製造の完了の form は、初期値の workorder (土台が GET の ?workorder=<pk> を写す) があれば、
  その指示書の 完成の数 − 入庫にした数 を数量の初期値にする (0 以下なら入れない)。
- 欄の見た目の class は固定の字で書く (土台の base.html の注記にある物だけ)。
"""

from django import forms

from seizou.core.forms import PlainNumberInput
from django.apps import apps
from django.utils import timezone

from seizou.core.widgets import IsoDateInput, RemoteSelect

from . import services
from .models import StockMove, _qty_text


def _date_input():
    return IsoDateInput(attrs={"class": "input w-full"})


def _qty_field(label="数量"):
    return forms.DecimalField(
        label=label,
        max_digits=12,
        decimal_places=2,
        widget=PlainNumberInput(attrs={"class": "input w-full", "step": "any"}),
    )


def _date_field():
    return forms.DateField(label="日付", initial=timezone.localdate, widget=_date_input())


def _note_field(label="備考"):
    return forms.CharField(
        label=label, max_length=500, required=False, widget=forms.TextInput(attrs={"class": "input w-full"})
    )


def _item_field():
    return forms.ModelChoiceField(
        label="部品",
        queryset=StockMove._meta.get_field("item").related_model._default_manager.all(),
        widget=RemoteSelect("masters:item_select"),
        empty_label="選んでください",
    )


def _workorder_model():
    """指示書の表。workorders の部品が無ければ None。"""
    try:
        return apps.get_model("workorders", "WorkOrder")
    except LookupError:
        return None


def _pk_or_none(raw):
    """pk の字を数にする。読めない字と 1 未満は None (画面は 200 のまま)。"""
    if raw is None:
        return None
    try:
        pk = int(str(raw))
    except (TypeError, ValueError):
        return None
    return pk if pk > 0 else None


class WorkOrderChoiceField(forms.ModelChoiceField):
    """指示書を選ぶ欄。字は「<指示書番号> <品番> <品名> 数量 <数>」。"""

    def label_from_instance(self, obj):
        item = obj.order_line.item
        return f"{obj.wo_no} {item.part_no} {item.name} 数量 {_qty_text(obj.qty)}"


class FinishedForm(forms.Form):
    """製造の完了を入庫にする form。"""

    workorder = WorkOrderChoiceField(
        label="指示書",
        queryset=StockMove.objects.none(),
        empty_label="選んでください",
        widget=RemoteSelect("workorders:workorder_select"),
    )
    qty = _qty_field()
    moved_on = _date_field()
    note = _note_field()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        model = _workorder_model()
        if model is None:
            return
        workorders = model._default_manager.select_related("order_line__item").order_by("-issued_on", "-pk")
        issued = workorders.filter(status=model.STATUS_ISSUED)
        # 候補は口 (workorders:workorder_select) が発行の指示書だけを返す。ここの queryset は、選んである値の字を描くのと
        # 送られた値を受けるのに使う (送られた値は全部の指示書で受け、取消は services が断る)。
        self.fields["workorder"].queryset = workorders if self.is_bound else issued
        pk = _pk_or_none(self.initial.get("workorder"))
        workorder = issued.filter(pk=pk).first() if pk is not None else None
        if workorder is not None:
            rest = services.finished_qty(workorder) - services.stocked_qty(workorder)
            if rest > 0:
                self.initial.setdefault("qty", rest)

    def service_kwargs(self):
        data = self.cleaned_data
        return {"workorder": data["workorder"], "qty": data["qty"], "moved_on": data["moved_on"], "note": data["note"]}


class IssueForm(forms.Form):
    """払い出しの form。"""

    item = _item_field()
    qty = _qty_field()
    moved_on = _date_field()
    note = _note_field()

    def service_kwargs(self):
        data = self.cleaned_data
        return {"item": data["item"], "qty": data["qty"], "moved_on": data["moved_on"], "note": data["note"]}


class AdjustForm(forms.Form):
    """棚卸しの調整の form。実地の在庫数を入れると、services が今の在庫数との差を動きとして残す。"""

    item = _item_field()
    counted_qty = _qty_field("実地の在庫数")
    moved_on = _date_field()
    note = _note_field("備考 (理由)")

    def service_kwargs(self):
        data = self.cleaned_data
        return {
            "item": data["item"],
            "counted_qty": data["counted_qty"],
            "moved_on": data["moved_on"],
            "note": data["note"],
        }
