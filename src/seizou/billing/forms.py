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
  出し切らずに終えた一式の受注の明細 (services.unbilled_lumps。決め PRICE-1d) は、一式の節の表の行 (lump_rows) で描く (知らせるだけで欄は無い)。
- 取り消す form は欄が無い。service_kwargs() は空の辞書 (土台が cancel_invoice(請求書, user=...) を呼ぶ)。
- 作り直す form は「発行日」だけ。service_kwargs() は {"issued_on": …} (土台が reissue_invoice(請求書, user=..., issued_on=...) を呼ぶ)。
- ほかの部品の model は import しない。外部キーの先を related_model で引く。
- 入金の form: 入金日・金額・入金の方法・備考。請求書は土台が URL の pk で引き、record_payment の 1 つ目の引数に渡す (欄に持たない)。
  金額の 0 以下は欄の横で断る (services でも断る)。入金を取り消す form は欄が無い (土台が cancel_payment(入金, user=...) を呼ぶ)。
- 入金と請求書の取消の保存のボタンの字は、押した結果の金額と対象を書く (N138。字を組むのは core の SubmitLabelMixin)。
"""

import unicodedata
from decimal import Decimal, InvalidOperation

from django import forms
from django.db.models import Q
from django.utils import timezone

from seizou.core.forms import PlainNumberInput, SubmitLabelMixin, label_number
from seizou.core.templatetags.seizou_tags import comma
from seizou.core.widgets import IsoDateInput
from seizou.masters.services import PRICE_BASIS_LUMP

from .models import Invoice
from .models import Payment
from .services import (
    billable_shipments,
    closing_period,
    lump_facts,
    lumps_shipped_after,
    shipment_amount,
    unbilled_lumps,
)

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


def plain_amount(text):
    """欄に打った金額の字を Decimal にする (全角を半角に・桁の区切りの「,」を外す)。空か数として読めなければ None。"""
    value = unicodedata.normalize("NFKC", str(text or "")).replace(",", "").strip()
    if not value:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _plain(value) -> str:
    """素の数の字 (JS が読む data- の値。区切りを付けず、小数の末尾の 0 を落とす)。"""
    return f"{value.normalize():f}"


class LumpFieldsMixin:
    """一式の表の欄を足す form の部品 (請求書の新規登録 InvoiceForm と作り直し InvoiceReissueForm。決め PRICE-1d)。

    出し切らずに終えた一式の受注の明細 (services.unbilled_lumps) ごとに、金額の閲覧のある人にだけ欄を 2 つ足す:
    lump-<受注の明細の pk>-pick (載せる印) と lump-<pk>-amount (請求する金額。1 円の整数で、0 より大きい数 = 作者の決め C107-3)。
    載せる印があって金額が空の行は、金額の欄の誤り「金額を入れてください。」(欄の形の誤り)。
    clean の後の _picked_lumps = (受注の明細, 金額) の組の並び (services の lumps に渡す)。
    """

    def _add_lump_fields(self, lumps):
        self._lumps = list(lumps)
        self._picked_lumps = []
        if self.hide_money:
            return
        for line in self._lumps:
            ref = f"受注 {line.order.order_no}-{line.line_no}"
            self.fields[f"lump-{line.pk}-pick"] = forms.BooleanField(
                label=f"{ref} の一式を載せる",
                required=False,
                widget=forms.CheckboxInput(attrs={"class": "checkbox", "data-label-pick": True}),
            )
            self.fields[f"lump-{line.pk}-amount"] = forms.DecimalField(
                label=f"{ref} の請求する金額",
                required=False,
                max_digits=14,
                decimal_places=0,
                min_value=Decimal("1"),
                error_messages={
                    "min_value": "一式の金額は 0 より大きい数にしてください。",
                    "invalid": "一式の金額は 16667 のように 1 円単位の数字で入れてください。",
                    "max_decimal_places": "一式の金額は 16667 のように 1 円単位の数字で入れてください。",
                },
                widget=PlainNumberInput(
                    attrs={"class": "input w-32", "step": "1", "inputmode": "numeric", "data-lump-amount": True}
                ),
            )

    def _lump_field_names(self):
        return {name for name in self.fields if name.startswith("lump-")}

    def _clean_lumps(self, cleaned) -> int:
        """載せる印の付いた一式の行を (受注の明細, 金額) の組にして _picked_lumps に持つ。印の付いた行の数 (誤りの行も含む) を返す。"""
        picked, marked = [], 0
        if self.hide_money:
            self._picked_lumps = picked
            return marked
        for line in self._lumps:
            if not cleaned.get(f"lump-{line.pk}-pick"):
                continue
            marked += 1
            name = f"lump-{line.pk}-amount"
            amount = cleaned.get(name)
            if amount is None:
                if name not in self.errors:
                    self.add_error(name, "金額を入れてください。")
                continue
            picked.append((line, amount))
        self._picked_lumps = picked
        return marked

    def _lump_marked(self, line) -> bool:
        """送った form で、一式の行に載せる印が付いているか (描き直しと合計の数え方が読む)。"""
        if not self.is_bound or self.hide_money:
            return False
        return self[f"lump-{line.pk}-pick"].value() in (True, "on", "true", "True", "1")

    def _lump_typed_amount(self, line):
        """送った form の、一式の行の金額の欄の値 (数として読めなければ None)。"""
        if not self.is_bound or self.hide_money:
            return None
        return plain_amount(self.data.get(self.add_prefix(f"lump-{line.pk}-amount")))


class InvoiceForm(LumpFieldsMixin, SubmitLabelMixin, forms.Form):
    """請求書を作る form。

    出荷は表の行 (rows) で描き、印を付けた行の数と税抜の金額の合計を、合計の枠 (line_totals) と保存のボタンの字に出す。
    行の金額は保存と同じ式 (services.shipment_amount)。得意先を選んでいるときは、その得意先の行だけを数える (画面の JS が隠す行を数えない)。
    一式の表の行 (lump_rows) には、金額の閲覧のある人にだけ「載せる」の印と「請求する金額」の欄を置く (LumpFieldsMixin)。
    載せる印の付いた一式の行も 1 行として数える (数量は出荷の合計、金額は入れた金額)。出荷の欄は必須でなく、出荷も載せる印の付いた一式の行も
    無いときだけ出荷の欄の誤りにする (今の必須の止めを置き換えるだけ)。
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
        required=False,
        widget=ShipmentCheckboxes(attrs={"class": "checkbox"}),
        help_text="納品書の出た出荷のうち、まだ請求書に入っていない物だけが並びます。",
    )

    def __init__(self, *args, hide_money=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.hide_money = hide_money
        self._rows = None
        self._lump_rows = None
        candidates = billable_shipments()
        # 一式の表の明細は 1 回だけ読む (lump_rows と得意先の候補が同じ並びを使う)。
        self._add_lump_fields(unbilled_lumps())
        # 一式の行の後に出荷が続いた明細 (読むだけの行。欄は無く、得意先の候補にも足さない = 作者の決め C107-7)。
        self._lumps_after = lumps_shipped_after()
        chosen = Q(pk__in=candidates.values("order_line__order__customer_id"))
        if not self.hide_money and self._lumps:
            # 請求できる出荷が無く、出し切らずに終えた一式だけが残った得意先も選べるようにする (一式の行だけの請求書を作るため)。
            chosen |= Q(pk__in={line.order.customer_id for line in self._lumps})
        self.fields["customer"].queryset = _Partner.objects.filter(chosen).order_by("code")
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
        """表の上の欄 (得意先・締め期間・発行日と、土台が足す会社ごとの項目)。出荷の欄と一式の表の欄は表に描くので除く。"""
        lump_names = self._lump_field_names()
        return [field for field in self.visible_fields() if field.name != "shipments" and field.name not in lump_names]

    @property
    def rows(self):
        """出荷の表の行。1 行 = {box・shipment・checked・qty_text・amount・amount_text・lump}。

        最初に読んだ時に作って持つ (土台の get_form が __init__ の後に欄の widget を触るので、__init__ の中で作らない)。
        box は checkbox の subwidget (テンプレートが {{ row.box.tag }} で描く)。qty_text と amount_text は JS が読む素の数の字。
        amount は行の得意先の端数の扱いで 1 円に丸めた金額 (services.shipment_amount)。金額の閲覧が無い人は None。
        lump = 受注の明細の単価の単位が式 (金額の枡に「(一式)」を添える。一式の途中の出荷は 0。決め PRICE-1c)。
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
                        "lump": shipment.order_line.price_basis == PRICE_BASIS_LUMP,
                    }
                )
            self._rows = rows
        return self._rows

    @property
    def lump_rows(self):
        """一式の節の表の行 (出し切らずに終えた一式の受注の明細。services.unbilled_lumps の並びのまま)。

        1 行 = {line (受注の明細)・partner (得意先の pk)・qty_text (数量の字)・shipped_text (出荷の合計の字)・unit (明細の単位)・
        cancelled (受注が取消か)・full_amount (一式の全額)・shipped_amount (出した分)・shipped_qty (出荷の合計)・
        pick (載せる印の欄)・amount (請求する金額の欄)・qty_plain (出荷の合計の素の数の字)・amount_plain (送った金額の素の数の字)・
        full_plain・shipped_plain (「全額」「出した分」のボタンが欄に入れる素の数の字)・after (偽)}。金額と欄は金額の閲覧が無い人は None
        (金額を見られない人に金額を見せず、決めさせない)。得意先を選んでいても全部を描き、テンプレートの JS が data-partner で絞る (出荷の表と同じ)。
        その後に、一式の行の後に出荷が続いた明細 (services.lumps_shipped_after。作者の決め C107-7) の行を足す: after が真・invoice (一式の行の請求書)・
        billed_qty_text (一式の行の数量の字)。金額・欄・「請求しない」は無い (読むだけ)。line_totals と service_kwargs は欄のある行だけを数える。
        最初に読んだ時に作って持つ (rows と同じ)。
        """
        if self._lump_rows is None:
            rows = []
            for line in self._lumps:
                facts = lump_facts(line)
                money = not self.hide_money
                typed = self._lump_typed_amount(line)
                rows.append(
                    {
                        "line": line,
                        "partner": line.order.customer_id,
                        "qty_text": comma(line.qty),
                        "shipped_text": comma(facts["shipped_qty"]),
                        "shipped_qty": facts["shipped_qty"],
                        "unit": line.unit,
                        "cancelled": line.order.status == line.order.STATUS_CANCELLED,
                        "full_amount": facts["full_amount"] if money else None,
                        "shipped_amount": facts["shipped_amount"] if money else None,
                        "pick": self[f"lump-{line.pk}-pick"] if money else None,
                        "amount": self[f"lump-{line.pk}-amount"] if money else None,
                        "qty_plain": _plain(facts["shipped_qty"]),
                        "amount_plain": _plain(typed) if typed is not None and typed > 0 else "",
                        "full_plain": _plain(facts["full_amount"]) if money else "",
                        "shipped_plain": _plain(facts["shipped_amount"]) if money else "",
                        "after": False,
                    }
                )
            for line, billed in self._lumps_after:
                facts = lump_facts(line)
                rows.append(
                    {
                        "line": line,
                        "partner": line.order.customer_id,
                        "qty_text": comma(line.qty),
                        "shipped_text": comma(facts["shipped_qty"]),
                        "shipped_qty": facts["shipped_qty"],
                        "unit": line.unit,
                        "cancelled": line.order.status == line.order.STATUS_CANCELLED,
                        "full_amount": None,
                        "shipped_amount": None,
                        "pick": None,
                        "amount": None,
                        "qty_plain": "",
                        "amount_plain": "",
                        "full_plain": "",
                        "shipped_plain": "",
                        "after": True,
                        "invoice": billed.invoice,
                        "billed_qty_text": comma(billed.qty),
                    }
                )
            self._lump_rows = rows
        return self._lump_rows

    @property
    def lump_has_after(self) -> bool:
        """一式の表に「一式の行の後に出荷あり」の行があるか (黄の 1 行の頭の字を選ぶ)。"""
        return any(row["after"] for row in self.lump_rows)

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
        # 載せる印の付いた一式の行 (得意先を選んでいれば、その得意先の行だけ)。数量は出荷の合計、金額は入れた金額 (読めなければ金額を出さない)。
        lumps = [
            row
            for row in self.lump_rows
            if not row["after"] and self._lump_marked(row["line"]) and (not chosen or str(row["partner"]) == chosen)
        ]
        qty = sum((row["shipment"].qty for row in picked), Decimal("0")) + sum(
            (row["shipped_qty"] for row in lumps), Decimal("0")
        )
        amount = None if self.hide_money else sum((row["amount"] for row in picked), Decimal("0"))
        if amount is not None:
            for row in lumps:
                typed = self._lump_typed_amount(row["line"])
                if typed is None:
                    amount = None
                    break
                amount += typed
        return {
            "rows": len(picked) + len(lumps),
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

    def clean(self):
        cleaned = super().clean()
        marked = self._clean_lumps(cleaned)
        if not cleaned.get("shipments") and not marked and "shipments" not in self.errors:
            self.add_error("shipments", "出荷か一式の行を 1 つ以上選んでください。")
        return cleaned

    def service_kwargs(self):
        data = self.cleaned_data
        return {
            "customer": data["customer"],
            "shipments": list(data["shipments"] or []),
            "issued_on": data["issued_on"],
            "lumps": list(self._picked_lumps),
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


class InvoiceReissueForm(LumpFieldsMixin, forms.Form):
    """請求書を作り直す form。欄は「発行日」と、出し切らずに終えた一式の欄。保存のボタンは「IV-000001 を取り消して作り直す」(土台が対象を instance= で渡す)。

    発行日の初めの値は、作り直す請求書の期間の終わり (締日)。それが今日より後なら今日 (請求書の新規登録で締め期間を選んだときと同じ決め)。
    一式の欄 (LumpFieldsMixin) は、その得意先の出し切らずに終えた一式の受注の明細 (services.unbilled_lumps(得意先)) ごとに、金額の閲覧のある人にだけ置く
    (1 期間 1 枚で止まった一式の行を、作り直しの画面から載せるため。取り消す請求書に入っている一式の行は、開いた時はまだ発行の請求書に入っているので並ばない)。
    欄は土台の入力のカード (core/_form_card.html) の格子に並ぶ (載せる印と請求する金額。金額の欄の下に一式の金額と出した分の字)。
    """

    submit_label = "作り直す"
    danger = True  # 実行のボタンを取消と同じ赤 (btn btn-error) にする (前の請求書の取消は元に戻せないため。請求書の取消の form と同じ印)

    issued_on = forms.DateField(
        label="発行日",
        widget=IsoDateInput(attrs={"class": "input w-full"}),
        help_text="新しい請求書に載る日付です。初めは締め期間の終わり (今日より後なら今日) が入ります。",
    )

    def __init__(self, *args, instance=None, hide_money=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.hide_money = hide_money
        today = timezone.localdate()
        self.fields["issued_on"].initial = today
        if instance is not None:
            self.submit_label = f"{instance.invoice_no} を取り消して作り直す"
            if instance.period_to is not None and instance.period_to < today:
                self.fields["issued_on"].initial = instance.period_to
        customer_id = getattr(instance, "customer_id", None)
        self._add_lump_fields(unbilled_lumps(customer_id) if customer_id is not None else [])
        if not self.hide_money:
            for line in self._lumps:
                facts = lump_facts(line)
                self.fields[f"lump-{line.pk}-pick"].label = (
                    f"受注 {line.order.order_no}-{line.line_no} ({line.item}) の一式を載せる"
                )
                self.fields[f"lump-{line.pk}-amount"].help_text = (
                    f"出荷 {comma(facts['shipped_qty'])} {line.unit}・一式の金額 ¥{comma(facts['full_amount'])}"
                    f"・出した分 ¥{comma(facts['shipped_amount'])}"
                )

    def clean(self):
        cleaned = super().clean()
        self._clean_lumps(cleaned)
        return cleaned

    def service_kwargs(self):
        return {"issued_on": self.cleaned_data["issued_on"], "lumps": list(self._picked_lumps)}


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


class ShipmentPaymentForm(PaymentForm):
    """請求書の外で受け取った入金を出荷に記録する form (billing.services.record_outside_payment)。欄は PaymentForm と同じ。
    出荷は欄に持たない (土台が URL の pk の出荷を渡す)。

    開いた form は、入金の方法の初めの値を現金、金額の初めの値を出荷の金額 (services.shipment_amount。金額の閲覧の無い人には入れない) にする。
    保存のボタンは「¥1,375 を出荷 SH-000001 の入金として記録する」(金額を入れる前は「出荷 SH-000001 の入金として記録する」)。
    """

    def __init__(self, *args, instance=None, hide_money=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["method"].initial = Payment.METHOD_CASH
        self.shipment = instance
        if instance is None:
            return
        line = instance.order_line
        self.submit_label_plain = f"出荷 {instance.shipment_no} の入金として記録する"
        amount = None if hide_money else shipment_amount(line.order.customer, instance)
        if amount is not None:
            self.fields["amount"].initial = amount
        facts = f"出荷 {instance.shipment_no} (受注 {line.order.order_no}-{line.line_no}・{line.item.part_no}・{comma(instance.qty)} {line.unit})"
        money = "" if amount is None else f"の金額は ¥{comma(amount)} です。"
        self.fields["amount"].help_text = f"{facts}{money if money else 'の入金です。'}受け取った金額を入れます。"

    def submit_label_templates(self) -> list:
        if self.shipment is None:
            return super().submit_label_templates()
        return [f"¥{{amount}} を出荷 {self.shipment.shipment_no} の入金として記録する"]


class PaymentCancelForm(forms.Form):
    """入金を取り消す form。欄は無い。"""
    submit_label = "取り消す"
    danger = True  # 実行のボタンを削除の確かめと同じ赤 (btn btn-error) にする (core/_form_card.html。初見テスト R5 の U125。候補 C24)

    def service_kwargs(self):
        return {}
