"""billing の入力の form。保存は form でなく services が行う (土台が form の save を呼ぶことは無い)。

- 作る form: 得意先・出荷 (checkbox)・発行日。service_kwargs() が create_invoice の引数を作る。
  候補は billable_shipments() (発行の納品書に入っていて、取消でない請求書に入っていない出荷) と、その出荷のある得意先。
  得意先と違う出荷を選んだときは、画面で先に断らず services の R09 の赤の知らせに任せる。
  checkbox の行ごとに data-partner (出荷の受注の得意先の pk) を付け、テンプレートの小さな JS が得意先の欄で行を絞る。
- 取り消す form は欄が無い。service_kwargs() は空の辞書 (土台が cancel_invoice(請求書, user=...) を呼ぶ)。
- ほかの部品の model は import しない。外部キーの先を related_model で引く。
- 入金の form: 入金日・金額・入金の方法・備考。請求書は土台が URL の pk で引き、record_payment の 1 つ目の引数に渡す (欄に持たない)。
  金額の 0 以下は欄の横で断る (services でも断る)。入金を取り消す form は欄が無い (土台が cancel_payment(入金, user=...) を呼ぶ)。
- 入金と請求書の取消の保存の釦の字は、押した結果の金額と対象を書く (N138。字を組むのは core の SubmitLabelMixin)。
"""

from decimal import Decimal

from django import forms
from django.utils import timezone

from seizou.core.forms import PlainNumberInput, SubmitLabelMixin, label_number

from .models import Invoice
from .models import Payment
from .services import billable_shipments

_Partner = Invoice._meta.get_field("customer").related_model


class ShipmentCheckboxes(forms.CheckboxSelectMultiple):
    """出荷の checkbox。行ごとに data-partner (出荷の受注の得意先の pk) を付ける。"""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-partner"] = instance.order_line.order.customer_id
        return option

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        # 外の枠 (div) には checkbox の見た目を付けない (Django の雛形は widget の class を外の枠にも付けるため)。
        context["widget"]["attrs"].pop("class", None)
        return context


class ShipmentChoiceField(forms.ModelMultipleChoiceField):
    """出荷を選ぶ欄。字は「出荷番号 得意先 品番 品名 数量 出荷日 納品書番号」。"""

    def label_from_instance(self, obj):
        line = obj.order_line
        qty = f"{obj.qty.normalize():f}"
        note_no = obj.delivery_note.note_no if obj.delivery_note else ""
        return (
            f"{obj.shipment_no} {line.order.customer.name} {line.item.part_no} {line.item.name} "
            f"{qty} {obj.shipped_at:%Y-%m-%d} {note_no}"
        )


class InvoiceForm(forms.Form):
    """請求書を作る form。"""

    customer = forms.ModelChoiceField(label="得意先", queryset=_Partner.objects.none(), widget=forms.Select)
    shipments = ShipmentChoiceField(
        label="出荷",
        queryset=billable_shipments().none(),
        widget=ShipmentCheckboxes(attrs={"class": "checkbox"}),
        help_text="納品書の出た出荷のうち、まだ請求書に入っていない物だけが並びます。",
    )
    issued_on = forms.DateField(
        label="発行日",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date", "class": "input w-full"}, format="%Y-%m-%d"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        candidates = billable_shipments()
        self.fields["customer"].queryset = _Partner.objects.filter(
            pk__in=candidates.values("order_line__order__customer_id")
        ).order_by("code")
        self.fields["shipments"].queryset = candidates.select_related(
            "order_line__order__customer", "order_line__item", "delivery_note"
        ).order_by("shipped_at", "shipment_no")

    def service_kwargs(self):
        data = self.cleaned_data
        return {
            "customer": data["customer"],
            "shipments": list(data["shipments"]),
            "issued_on": data["issued_on"],
        }


class InvoiceCancelForm(forms.Form):
    """請求書を取り消す form。欄は無い。保存の釦は「IV-000001 を取り消す」(土台が対象を instance= で渡す。N138)。"""
    submit_label = "取り消す"

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        if instance is not None:
            self.submit_label = f"{instance.invoice_no} を取り消す"

    def service_kwargs(self):
        return {}


class PaymentForm(SubmitLabelMixin, forms.Form):
    """入金を記録する form。請求書は欄に持たない (土台が URL の pk の請求書を record_payment に渡す)。

    保存の釦は「¥12,000 を入金として記録する」(金額を入れる前は「入金として記録する」。N138)。
    金額は使う人が自分で入れる字なので、金額の閲覧が無い人にも隠さない (発注の合計は form が掛けて出す字なので隠す)。
    """

    submit_label_plain = "入金として記録する"

    received_on = forms.DateField(
        label="入金日",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date", "class": "input w-full"}, format="%Y-%m-%d"),
    )
    amount = forms.DecimalField(
        label="金額",
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        error_messages={"min_value": "入金の金額は 0 より大きい数にしてください。"},
        widget=PlainNumberInput(attrs={"class": "input w-full", "step": "any", "data-label-amount": True}),
    )
    method = forms.ChoiceField(label="入金の方法", choices=Payment.METHOD_CHOICES, initial=Payment.METHOD_TRANSFER)
    note = forms.CharField(
        label="備考", max_length=500, required=False, widget=forms.TextInput(attrs={"class": "input w-full"})
    )

    def submit_label_templates(self) -> list:
        return ["¥{amount} を入金として記録する"]

    def submit_label_values(self) -> dict:
        return {"amount": label_number(self.submit_value("amount"))}

    def service_kwargs(self):
        data = self.cleaned_data
        return {
            "received_on": data["received_on"],
            "amount": data["amount"],
            "method": data["method"],
            "note": data["note"],
        }


class PaymentCancelForm(forms.Form):
    """入金を取り消す form。欄は無い。"""
    submit_label = "取り消す"

    def service_kwargs(self):
        return {}
