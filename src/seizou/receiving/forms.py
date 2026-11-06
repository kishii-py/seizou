"""receiving の入力の form (入荷の新規登録)。保存は form でなく services が行う (土台が form の save を呼ぶことは無い)。

- forms.Form。services の引数は service_kwargs() が作る (土台が create_receiving(data=…, order_line=…, user=…) で呼ぶ)。
- ほかの部品の model は import しない。部品・取引先・受注の明細は Receiving と Allocation の外部キーの先 (related_model) として引く (読むだけ)。
- 部品と支給元と受注の明細を選ぶ欄は、字を打って探す欄 (RemoteSelect)。支給元の候補は得意先と両方だけ (受注の得意先の欄と同じ)。
- 受注の明細の欄は空でよい (空なら引当を作らない)。候補は取消でない受注の、引当が受注の数に届いていない明細だけ
  (receiving:orderline_select)。送られた値は全部の明細で受ける。
- 初期値の order_line (受注の明細の pk。土台が GET の ?order_line=<pk> を写す) があれば、部品 = 明細の部品・
  支給元 = 受注の得意先・数量 = 受注の数 − 引当の合計 (0 以下なら入れない)・出所 = 預かり を初期値にする。
- 欄の見た目の class は固定の字で書く (土台の base.html の注記にある物だけ)。
"""

from django import forms
from django.utils import timezone

from seizou.core.forms import PlainNumberInput
from seizou.core.widgets import RemoteSelect

from . import services
from .models import SOURCE_CHOICES, SOURCE_CONSIGNED, Allocation, Receiving

#: 部品・取引先・受注の明細の表 (外部キーの先として引く。読むだけ)
ItemModel = Receiving._meta.get_field("item").related_model
PartnerModel = Receiving._meta.get_field("partner").related_model
OrderLineModel = Allocation._meta.get_field("order_line").related_model


def _pk_or_none(raw):
    """pk の字を数にする。読めない字と 1 未満は None (画面は 200 のまま)。"""
    if raw is None or isinstance(raw, (list, tuple)):
        return None
    try:
        pk = int(str(raw))
    except (TypeError, ValueError):
        return None
    return pk if pk > 0 else None


class ReceivingForm(forms.Form):
    """入荷の新規登録の form。"""

    order_line = forms.ModelChoiceField(
        label="受注の明細",
        queryset=OrderLineModel._default_manager.select_related("order", "item"),
        widget=RemoteSelect("receiving:orderline_select"),
        required=False,
        empty_label="選んでください",
        help_text="選ぶと、入荷をその明細に引き当てます (空なら引き当てません)。",
    )
    item = forms.ModelChoiceField(
        label="部品",
        queryset=ItemModel._default_manager.all(),
        widget=RemoteSelect("masters:item_select"),
        empty_label="選んでください",
    )
    partner = forms.ModelChoiceField(
        label="支給元",
        queryset=PartnerModel._default_manager.all(),
        widget=RemoteSelect("masters:partner_select", query={"role": "customer"}),
        required=False,
        empty_label="選んでください",
    )
    source = forms.ChoiceField(label="出所", choices=SOURCE_CHOICES, widget=forms.Select(attrs={"class": "select w-full"}))
    qty = forms.DecimalField(
        label="数量",
        max_digits=12,
        decimal_places=2,
        widget=PlainNumberInput(attrs={"class": "input w-full", "step": "any"}),
    )
    received_on = forms.DateField(
        label="入荷日",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date", "class": "input w-full"}, format="%Y-%m-%d"),
    )
    lot = forms.CharField(
        label="ロット", max_length=50, required=False, widget=forms.TextInput(attrs={"class": "input w-full"})
    )
    note = forms.CharField(
        label="備考", max_length=500, required=False, widget=forms.TextInput(attrs={"class": "input w-full"})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        pk = _pk_or_none(self.initial.get("order_line"))
        line = (
            OrderLineModel._default_manager.select_related("order", "item").filter(pk=pk).first()
            if pk is not None
            else None
        )
        if line is None:
            return
        self.initial.setdefault("item", line.item_id)
        self.initial.setdefault("partner", line.order.customer_id)
        self.initial.setdefault("source", SOURCE_CONSIGNED)
        rest = line.qty - services.allocated_total(line)
        if rest > 0:
            self.initial.setdefault("qty", rest)

    def service_kwargs(self):
        data = self.cleaned_data
        return {
            "data": {
                "item": data["item"],
                "partner": data.get("partner"),
                "source": data["source"],
                "qty": data["qty"],
                "received_on": data["received_on"],
                "lot": data.get("lot", ""),
                "note": data.get("note", ""),
            },
            "order_line": data.get("order_line"),
        }
