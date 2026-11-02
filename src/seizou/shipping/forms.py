"""shipping の入力の form。保存は form でなく services が行う (土台が form の save を呼ぶことは無い)。

土台は service_kwargs() の辞書を検査の層の data に使い、同じ辞書で services の関数を呼ぶ (01 の 10.4)。
出荷の入力の鍵 order_line と qty は、検査の層の受注より多い出荷のポカよけが読む。
画面の側では数を比べない (止めるか知らせるかは会社の設定に従って検査の層が決める)。
ほかの部品の model は import せず、外部キーの先として引く (読むだけ)。
"""

from django import forms
from django.utils import timezone

from seizou.core.widgets import RemoteSelect

from .models import Shipment

_OrderLine = Shipment._meta.get_field("order_line").related_model

#: 選ぶ欄の空の選択肢の字 (選ぶ欄の JS が載る前と、JS が無いときに見える)
EMPTY_LABEL = "選んでください"


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
    )
    note = forms.CharField(label="備考", max_length=500, required=False)

    def service_kwargs(self) -> dict:
        data = self.cleaned_data
        return {
            "order_line": data["order_line"],
            "qty": data["qty"],
            "shipped_at": data["shipped_at"],
            "note": data.get("note", ""),
        }
