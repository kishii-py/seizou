"""purchasing の入力の form。保存は form でなく services が行う (土台が form の save を呼ぶことは無い)。

- 発注を入れる form は、発注の項目の欄と、明細の行の formset (prefix "lines") を持つ。
  is_valid() は両方を確かめ、service_kwargs() が create_purchase_order の引数
  (partner・lines・order_date・note と、会社ごとの項目があれば ext) を作る。
- 送り直す form は欄が無い。service_kwargs() は空の辞書 (土台が send_order(発注, user=...) を呼ぶ)。
- 取り消す form は理由の欄 1 つ。
- ほかの部品の model は import せず、外部キーの先を related_model で引く (読むだけ)。
- 取引先と品目を選ぶ欄は Django の標準の Select。取引先の候補は区分が外注先か両方の物だけ。
- 欄の見た目の class は固定の字で書く (土台の base.html の注記にある物だけ。CSS に入れるため)。
"""

from django import forms
from django.utils import timezone

from seizou.core import fields as company_fields

from .models import PurchaseOrder, PurchaseOrderLine

LINE_KEYS = ("item", "qty", "unit", "unit_price", "due_date", "note")


def _date_input():
    return forms.DateInput(attrs={"type": "date", "class": "input w-full"}, format="%Y-%m-%d")


class PurchaseOrderLineForm(forms.Form):
    """明細の 1 行。何も入れていない行は formset が飛ばす。"""

    item = forms.ModelChoiceField(
        label="品目", queryset=None, widget=forms.Select(attrs={"class": "select w-full"})
    )
    qty = forms.DecimalField(
        label="数量",
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "input w-full", "step": "any"}),
    )
    unit = forms.CharField(
        label="単位",
        max_length=10,
        required=False,
        widget=forms.TextInput(attrs={"class": "input w-full"}),
        help_text="空なら品目の単位を写します。",
    )
    unit_price = forms.DecimalField(
        label="単価",
        max_digits=14,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "input w-full", "step": "any"}),
    )
    due_date = forms.DateField(label="納期", required=False, widget=_date_input())
    note = forms.CharField(
        label="備考",
        max_length=500,
        required=False,
        widget=forms.TextInput(attrs={"class": "input w-full"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        item_model = PurchaseOrderLine._meta.get_field("item").related_model
        self.fields["item"].queryset = item_model._default_manager.all()


PurchaseOrderLineFormSet = forms.formset_factory(PurchaseOrderLineForm, extra=1)


class PurchaseOrderForm(forms.Form):
    """発注を入れる form。保存と同時に送るのは services (create_purchase_order) の中。"""

    partner = forms.ModelChoiceField(label="取引先", queryset=None)
    order_date = forms.DateField(label="発注日", initial=timezone.localdate, widget=_date_input())
    note = forms.CharField(
        label="備考", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        partner_model = PurchaseOrder._meta.get_field("partner").related_model
        self.fields["partner"].queryset = partner_model._default_manager.filter(
            role__in=(partner_model.ROLE_SUBCONTRACTOR, partner_model.ROLE_BOTH)
        )
        self.lines = PurchaseOrderLineFormSet(
            data=self.data if self.is_bound else None, prefix="lines"
        )

    def is_valid(self):
        # 発注の欄と明細の行の両方を確かめ、どちらの誤りも画面に出す。
        header_ok = super().is_valid()
        lines_ok = self.lines.is_valid()
        return header_ok and lines_ok

    def service_kwargs(self):
        data, ext = company_fields.split_ext(self.cleaned_data)
        lines = [
            {key: row.cleaned_data[key] for key in LINE_KEYS}
            for row in self.lines.forms
            if row.has_changed()
        ]
        kwargs = {
            "partner": data["partner"],
            "lines": lines,
            "order_date": data["order_date"],
            "note": data["note"],
        }
        if ext is not None:
            kwargs["ext"] = ext
        return kwargs


class SendForm(forms.Form):
    """送り直す form。欄は無い。"""

    def service_kwargs(self):
        return {}


class CancelForm(forms.Form):
    """取り消す form。理由は備考の末尾に「取消の理由: …」で残る。"""

    reason = forms.CharField(
        label="取消の理由", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3})
    )

    def service_kwargs(self):
        return {"reason": self.cleaned_data["reason"]}
