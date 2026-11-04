"""shipping の入力の form。保存は form でなく services が行う (土台が form の save を呼ぶことは無い)。

土台は service_kwargs() の辞書を検査の層の data に使い、同じ辞書で services の関数を呼ぶ (01 の 10.4)。
出荷の入力の鍵 order_line と qty は、検査の層の受注より多い出荷のポカよけが読む。
画面の側では数を比べない (止めるか知らせるかは会社の設定に従って検査の層が決める)。
ほかの部品の model は import せず、外部キーの先として引く (読むだけ)。
"""

from django import forms
from django.db.models import Q
from django.utils import timezone

from seizou.core.widgets import RemoteSelect
from seizou.orders.services import remaining_qty

from .models import DeliveryNote, Shipment

_OrderLine = Shipment._meta.get_field("order_line").related_model

#: 選ぶ欄の空の選択肢の字 (選ぶ欄の JS が載る前と、JS が無いときに見える)
EMPTY_LABEL = "選んでください"
#: 出荷の入力で受注の明細が決まっていないときの、数量の欄の下の字 (shipping/shipment-form.js も同じ字を出す)
NO_LINE_HELP = "受注の明細を選ぶと、残りの数が出ます。"
#: 出荷日の欄の誤りの文 (出荷の入力と出荷の編集の 2 つの form が読む。空のときと、日付として読めない字のとき)
SHIPPED_AT_ERRORS = {
    "required": "出荷日を入れてください。",
    "invalid": "出荷日は 2026-11-04 の形で入れてください。",
}


class ShipmentForm(forms.Form):
    """出荷の入力。受注の明細の候補は候補の口 (orders:orderline_select) が残のある明細だけを返す。

    欄の値として受けるのは全部の明細 (取消の受注の明細も)。取消の受注の明細は services が R05 で断る
    (画面で先に断らない)。
    """

    order_line = forms.ModelChoiceField(
        label="受注の明細",
        queryset=_OrderLine.objects.select_related("order", "item"),
        widget=RemoteSelect("orders:orderline_select"),
        empty_label=EMPTY_LABEL,
    )
    qty = forms.DecimalField(label="数量", max_digits=12, decimal_places=2)
    shipped_at = forms.DateField(
        label="出荷日",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        error_messages=SHIPPED_AT_ERRORS,
    )
    note = forms.CharField(label="備考", max_length=500, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 数量の欄の下に、選んだ受注の明細の残り (受注の数 − 出荷済みの数) を出す。明細の pk は送られた値か、
        # 受注の明細から移る入口の初期値 (?order_line=<pk>。土台の get_initial が字のまま写す)。数を比べて断ることはしない。
        raw = self.data.get("order_line") if self.is_bound else self.initial.get("order_line")
        line = None
        if raw is not None and str(raw).isdigit():
            line = _OrderLine.objects.filter(pk=int(raw)).first()
        if line is None:
            self.fields["qty"].help_text = NO_LINE_HELP
        else:
            self.fields["qty"].help_text = f"残り {remaining_qty(line).normalize():f}"

    def service_kwargs(self) -> dict:
        data = self.cleaned_data
        return {
            "order_line": data["order_line"],
            "qty": data["qty"],
            "shipped_at": data["shipped_at"],
            "note": data.get("note", ""),
        }


class ShipmentChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        line = obj.order_line
        return (
            f"{obj.shipment_no} {line.order.customer.name} {line.order.order_no} "
            f"{line.item.part_no} {line.item.name} 数量 {obj.qty.normalize():f}"
        )


class DeliveryNoteForm(forms.Form):
    """納品書を作る。発行の納品書に入っていない出荷を選び、得意先は選んだ出荷の受注の得意先にする。

    得意先の違う出荷が混ざったときは、services の create_delivery_note が R08 で断る (画面で先に断らない)。
    """

    shipments = ShipmentChoiceField(
        label="出荷",
        queryset=Shipment.objects.filter(
            Q(delivery_note__isnull=True) | Q(delivery_note__status=DeliveryNote.STATUS_CANCELLED)
        )
        .select_related("order_line__order__customer", "order_line__item")
        .order_by("order_line__order__customer__code", "shipped_at", "pk"),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkbox"}),
        help_text="同じ得意先の出荷を選びます。納品書に入っていない出荷だけが並びます。",
    )

    def service_kwargs(self) -> dict:
        shipments = sorted(self.cleaned_data["shipments"], key=lambda shipment: shipment.pk)
        return {"customer": shipments[0].order_line.order.customer, "shipments": shipments}


class ShipmentUpdateForm(forms.Form):
    """出荷の編集。数量と出荷日と備考だけ (受注の明細の欄は出さない。services.update_shipment が直さない)。

    ModelForm にしない。ModelForm は検査の前に新しい値を対象へ書き込み、検査の層 (R01) が直す前の数を読めなくなる。
    土台は名指しの instance に直す前の出荷を渡すので、その値を初期値にだけ使う (instance には書き込まない)。
    """

    qty = forms.DecimalField(label="数量", max_digits=12, decimal_places=2)
    shipped_at = forms.DateField(
        label="出荷日",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        error_messages=SHIPPED_AT_ERRORS,
    )
    note = forms.CharField(label="備考", max_length=500, required=False)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        if instance is not None:
            for name in ("qty", "shipped_at", "note"):
                self.initial.setdefault(name, getattr(instance, name))

    def service_kwargs(self) -> dict:
        data = self.cleaned_data
        return {
            "qty": data["qty"],
            "shipped_at": data["shipped_at"],
            "note": data.get("note", ""),
        }


class DeliveryNoteCancelForm(forms.Form):
    """納品書を取り消す form。欄は無い。"""
    submit_label = "取り消す"

    def service_kwargs(self) -> dict:
        return {}
