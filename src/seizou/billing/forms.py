"""billing の入力の form。保存は form でなく services が行う (土台が form の save を呼ぶことは無い)。

- 作る form: 得意先・出荷 (checkbox)・発行日。service_kwargs() が create_invoice の引数を作る。
  候補は billable_shipments() (発行の納品書に入っていて、取消でない請求書に入っていない出荷) と、その出荷のある得意先。
  得意先と違う出荷を選んだときは、画面で先に断らず services の R09 の赤の知らせに任せる。
  checkbox の行ごとに data-partner (出荷の受注の得意先の pk) を付け、テンプレートの小さな JS が得意先の欄で行を絞る。
  出荷は列の見出しのある表の行 (rows) で描き (billing/_invoice_create_card.html)、印を付けた行の数と税抜の金額の合計を
  合計の枠 (line_totals) と保存のボタンの字 (SubmitLabelMixin) に出す (候補 C74)。行の金額は保存と同じ services.shipment_amount。
  「締め期間」の欄 (period。候補 C02 の 6) は、請求できる出荷を得意先と締め期間 (services.closing_period) でまとめた選択肢で、
  選択肢ごとに data-partner と data-period を持つ。締日でまとめる得意先の出荷の行にも同じ data-period を付け、テンプレートの JS が
  選んだ期間の行に印を付けて発行日に締日を入れる。期間は Python の closing_period の 1 か所で決める (JS で日付を計算しない)。
  欄は service に渡さない補助の欄で、送った値を検査して断らない (空でも、選択肢に無い値でも保存は今のまま)。
- 取り消す form は欄が無い。service_kwargs() は空の辞書 (土台が cancel_invoice(請求書, user=...) を呼ぶ)。
- 作り直す form は「発行日」だけ。service_kwargs() は {"issued_on": …} (土台が reissue_invoice(請求書, user=..., issued_on=...) を呼ぶ)。
- ほかの部品の model は import しない。外部キーの先を related_model で引く。
- 入金の form: 入金日・金額・入金の方法・備考。請求書は土台が URL の pk で引き、record_payment の 1 つ目の引数に渡す (欄に持たない)。
  金額の 0 以下は欄の横で断る (services でも断る)。入金を取り消す form は欄が無い (土台が cancel_payment(入金, user=...) を呼ぶ)。
- 入金と請求書の取消の保存のボタンの字は、押した結果の金額と対象を書く (N138。字を組むのは core の SubmitLabelMixin)。
"""

from decimal import Decimal

from django import forms
from django.utils import timezone

from seizou.core.forms import PlainNumberInput, SubmitLabelMixin, label_number
from seizou.core.widgets import IsoDateInput

from .models import Invoice
from .models import Payment
from .services import billable_shipments, closing_period, shipment_amount

_Partner = Invoice._meta.get_field("customer").related_model


def period_text(period_from, period_to) -> str:
    """締め期間の字 (「2026-10-01〜2026-10-31」)。締め期間の選択肢と出荷の行の data-period が同じ字を使う。"""
    return f"{period_from:%Y-%m-%d}〜{period_to:%Y-%m-%d}"


def shipment_period(shipment) -> str:
    """出荷の締め期間の字 (services.closing_period)。請求の仕方が都度の得意先は締めないので空の字。"""
    customer = shipment.order_line.order.customer
    if customer.billing_method == _Partner.BILLING_PER_DELIVERY:
        return ""
    return period_text(*closing_period(customer, shipment.shipped_at))


class ShipmentCheckboxes(forms.CheckboxSelectMultiple):
    """出荷の checkbox。行ごとに data-partner (出荷の受注の得意先の pk) と、締日でまとめる得意先なら data-period (締め期間の字) を付ける。"""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-partner"] = instance.order_line.order.customer_id
            period = shipment_period(instance)
            if period:
                option["attrs"]["data-period"] = period
        return option

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        # 外の枠 (div) には checkbox の見た目を付けない (Django の雛形は widget の class を外の枠にも付けるため)。
        context["widget"]["attrs"].pop("class", None)
        return context


class PeriodSelect(forms.Select):
    """締め期間の選ぶ欄。選択肢ごとに data-partner (得意先の pk) と data-period (締め期間の字) を付ける (option_data が値ごとに持つ)。

    選択肢は描く時に choices_source (InvoiceForm が渡す関数) から作る。出荷の表の行 (InvoiceForm.rows) と同じ読みを使い、
    出荷を 2 回読まないため (請求書の新規登録の画面の問い合わせの数を増やさない)。
    """

    option_data: dict = {}
    choices_source = None

    def get_context(self, name, value, attrs):
        if self.choices_source is not None:
            self.choices, self.option_data = self.choices_source()
        return super().get_context(name, value, attrs)

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        option["attrs"].update(self.option_data.get(str(value), {}))
        return option


class ShipmentChoiceField(forms.ModelMultipleChoiceField):
    """出荷を選ぶ欄。字は「出荷 出荷番号・得意先・品番 品名・数量 n・出荷日 日付・納品書 納品書番号」(区切りは「・」。目視の所見 11-05。名前は R5 の V113)。"""

    def label_from_instance(self, obj):
        line = obj.order_line
        qty = f"{obj.qty.normalize():f}"
        note_no = obj.delivery_note.note_no if obj.delivery_note else ""
        text = (
            f"出荷 {obj.shipment_no}・{line.order.customer.name}・{line.item.part_no} {line.item.name}"
            f"・数量 {qty}・出荷日 {obj.shipped_at:%Y-%m-%d}"
        )
        return f"{text}・納品書 {note_no}" if note_no else text


class InvoiceForm(SubmitLabelMixin, forms.Form):
    """請求書を作る form。

    出荷は表の行 (rows) で描き、印を付けた行の数と税抜の金額の合計を、合計の枠 (line_totals) と保存のボタンの字に出す。
    行の金額は保存と同じ式 (services.shipment_amount)。得意先を選んでいるときは、その得意先の行だけを数える (画面の JS が隠す行を数えない)。
    """

    submit_label_plain = "請求書を作る"

    customer = forms.ModelChoiceField(label="得意先", queryset=_Partner.objects.none(), widget=forms.Select)
    # 補助の欄 (service に渡さない)。選択肢は描く時に作る (_period_choices)。CharField なので選択肢に無い値でも欄の誤りにしない
    # (選択肢が変わった後の送信で保存が止まると、頼まれていない断る処理になるため)。
    period = forms.CharField(
        label="締め期間",
        required=False,
        widget=PeriodSelect,
        help_text="選ぶと、その得意先のその締め期間の出荷に印が付き、発行日に締日が入ります。印は後から手で足し外しできます。",
    )
    issued_on = forms.DateField(
        label="発行日",
        initial=timezone.localdate,
        widget=IsoDateInput(attrs={"class": "input w-full"}),
        help_text="請求書に載る日付です。支払期日は発行日でなく、締め期間の終わり (都度の得意先は納品の日) と得意先の支払の決まりから決まります。",
    )
    shipments = ShipmentChoiceField(
        label="出荷",
        queryset=billable_shipments().none(),
        widget=ShipmentCheckboxes(attrs={"class": "checkbox"}),
        help_text="納品書の出た出荷のうち、まだ請求書に入っていない物だけが並びます。",
    )

    def __init__(self, *args, hide_money=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.hide_money = hide_money
        self._rows = None
        candidates = billable_shipments()
        self.fields["customer"].queryset = _Partner.objects.filter(
            pk__in=candidates.values("order_line__order__customer_id")
        ).order_by("code")
        self.fields["shipments"].queryset = candidates.select_related(
            "order_line__order__customer", "order_line__item", "delivery_note"
        ).order_by("shipped_at", "shipment_no")
        # 合計の枠と保存のボタンの字の JS (seizou-submit-label.js) が、印を付けた行だけを数える印 (全部の checkbox に写る)。
        self.fields["shipments"].widget.attrs["data-label-pick"] = True
        self.fields["period"].widget.choices_source = self._period_choices

    def _period_choices(self):
        """締め期間の欄の (選択肢, 選択肢ごとの印)。得意先 × 締め期間で、字は「2026-10-01〜2026-10-31 (5 件)」。

        請求の仕方が都度の得意先は入れない。値は「得意先の pk:期間の字」(同じ期間の字の得意先が 2 つあっても値が重ならない)。
        並びは得意先のコード・期間の初め。出荷は表の行 (rows) と同じ読みを使う。
        """
        groups = {}
        for row in self.rows:
            shipment = row["shipment"]
            period = shipment_period(shipment)
            if not period:
                continue
            customer = shipment.order_line.order.customer
            key = (customer.code, period, customer.pk)
            groups[key] = groups.get(key, 0) + 1
        choices = [("", "—")]
        option_data = {}
        for (_code, period, customer_pk), count in sorted(groups.items()):
            value = f"{customer_pk}:{period}"
            choices.append((value, f"{period} ({count} 件)"))
            option_data[value] = {"data-partner": customer_pk, "data-period": period}
        return choices, option_data

    @property
    def head_fields(self):
        """表の上の欄 (得意先・締め期間・発行日と、土台が足す会社ごとの項目)。出荷の欄は表に描くので除く。"""
        return [field for field in self.visible_fields() if field.name != "shipments"]

    @property
    def rows(self):
        """出荷の表の行。1 行 = {box・shipment・checked・qty_text・amount・amount_text}。

        最初に読んだ時に作って持つ (土台の get_form が __init__ の後に欄の widget を触るので、__init__ の中で作らない)。
        box は checkbox の subwidget (テンプレートが {{ row.box.tag }} で描く)。qty_text と amount_text は JS が読む素の数の字。
        amount は行の得意先の端数の扱いで 1 円に丸めた金額 (services.shipment_amount)。金額の閲覧が無い人は None。
        """
        if self._rows is None:
            rows = []
            for box in self["shipments"].subwidgets:
                shipment = box.data["value"].instance
                amount = None if self.hide_money else shipment_amount(shipment.order_line.order.customer, shipment)
                rows.append(
                    {
                        "box": box,
                        "shipment": shipment,
                        "checked": bool(box.data["selected"]),
                        "qty_text": f"{shipment.qty.normalize():f}",
                        "amount": amount,
                        "amount_text": None if amount is None else f"{amount:f}",
                    }
                )
            self._rows = rows
        return self._rows

    def _chosen_customer(self) -> str:
        """選んだ得意先の pk の字。送った form は送った値、開いた form は初期値 (?customer=)。選んでいなければ空の字。"""
        value = self.data.get("customer") if self.is_bound else self.initial.get("customer")
        value = getattr(value, "pk", value)
        return "" if value is None else str(value)

    def line_totals(self) -> dict:
        """合計の枠 (core/_line_totals.html) の字。印を付けた行のうち、得意先を選んでいればその得意先の行だけを数える。

        画面の JS (seizou-submit-label.js) が隠れた行を数えないのと同じ数え方。金額は 1 円に丸めた行の金額の合計
        (core の line_totals は数量 × 単価を小数 2 桁で丸めるので使わない)。
        """
        chosen = self._chosen_customer()
        picked = [
            row
            for row in self.rows
            if row["checked"] and (not chosen or str(row["shipment"].order_line.order.customer_id) == chosen)
        ]
        qty = sum((row["shipment"].qty for row in picked), Decimal("0"))
        amount = None if self.hide_money else sum((row["amount"] for row in picked), Decimal("0"))
        return {
            "rows": len(picked),
            "qty": label_number(qty),
            "amount": None if amount is None else label_number(amount),
            "show_amount": not self.hide_money,
        }

    def submit_label_templates(self) -> list:
        if self.hide_money:
            return ["{rows} 行の請求書を作る"]
        return ["{rows} 行・税抜 ¥{amount} の請求書を作る", "{rows} 行の請求書を作る"]

    def submit_label_values(self) -> dict:
        totals = self.line_totals()
        if not totals["rows"]:
            return {}
        return {"rows": str(totals["rows"]), "amount": totals["amount"]}

    def service_kwargs(self):
        data = self.cleaned_data
        return {
            "customer": data["customer"],
            "shipments": list(data["shipments"]),
            "issued_on": data["issued_on"],
        }


class InvoiceCancelForm(forms.Form):
    """請求書を取り消す form。欄は無い。保存のボタンは「IV-000001 を取り消す」(土台が対象を instance= で渡す。N138)。"""
    submit_label = "取り消す"
    danger = True  # 実行のボタンを削除の確かめと同じ赤 (btn btn-error) にする (core/_form_card.html。初見テスト R5 の U125。候補 C24)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        if instance is not None:
            self.submit_label = f"{instance.invoice_no} を取り消す"

    def service_kwargs(self):
        return {}


class InvoiceReissueForm(forms.Form):
    """請求書を作り直す form。欄は「発行日」だけ。保存のボタンは「IV-000001 を取り消して作り直す」(土台が対象を instance= で渡す)。

    発行日の初めの値は、作り直す請求書の期間の終わり (締日)。それが今日より後なら今日 (請求書の新規登録で締め期間を選んだときと同じ決め)。
    """

    submit_label = "作り直す"
    danger = True  # 実行のボタンを取消と同じ赤 (btn btn-error) にする (前の請求書の取消は元に戻せないため。請求書の取消の form と同じ印)

    issued_on = forms.DateField(
        label="発行日",
        widget=IsoDateInput(attrs={"class": "input w-full"}),
        help_text="新しい請求書に載る日付です。初めは締め期間の終わり (今日より後なら今日) が入ります。",
    )

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        self.fields["issued_on"].initial = today
        if instance is not None:
            self.submit_label = f"{instance.invoice_no} を取り消して作り直す"
            if instance.period_to is not None and instance.period_to < today:
                self.fields["issued_on"].initial = instance.period_to

    def service_kwargs(self):
        return {"issued_on": self.cleaned_data["issued_on"]}


class PaymentForm(SubmitLabelMixin, forms.Form):
    """入金を記録する form。請求書は欄に持たない (土台が URL の pk の請求書を record_payment に渡す)。

    保存のボタンは「¥12,000 を入金として記録する」(金額を入れる前は「入金として記録する」。N138)。
    金額は使う人が自分で入れる字なので、金額の閲覧が無い人にも隠さない (発注の合計は form が掛けて出す字なので隠す)。
    """

    submit_label_plain = "入金として記録する"

    received_on = forms.DateField(
        label="入金日",
        initial=timezone.localdate,
        widget=IsoDateInput(attrs={"class": "input w-full"}),
    )
    amount = forms.DecimalField(
        label="金額",
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        # 数でない字 (「8800円」) は直し方の分かる字にする (初見テスト R5 の V37。候補 C25 の 1)。空の字は入力の土台がまとめて「金額を入れてください。」にする。
        error_messages={
            "min_value": "入金の金額は 0 より大きい数にしてください。",
            "invalid": "金額は 8800 のように数字で入れてください。",
        },
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
    danger = True  # 実行のボタンを削除の確かめと同じ赤 (btn btn-error) にする (core/_form_card.html。初見テスト R5 の U125。候補 C24)

    def service_kwargs(self):
        return {}
