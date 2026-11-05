"""shipping の入力の form。保存は form でなく services が行う (土台が form の save を呼ぶことは無い)。

土台は service_kwargs() の辞書を検査の層の data に使い、同じ辞書で services の関数を呼ぶ (01 の 10.4)。
出荷の入力の鍵 order_line と qty は、検査の層の受注より多い出荷のポカよけが読む。
画面の側では数を比べない (止めるか知らせるかは会社の設定に従って検査の層が決める)。
ほかの部品の model は import せず、外部キーの先として引く (読むだけ)。
一括出荷の form (ShipmentBulkForm) は、注文残の盤で選んだ明細の行ごとに数量と「この行を出さない」の欄を持ち、
service_kwargs() の lines (受注の明細と数量の組) を検査の層の一括出荷の R01 と services.ship_lines に渡す (N124)。
出荷と一括出荷と納品書の取消の保存のボタンの字は、押した結果の数と対象を書く (N138。字を組むのは core の SubmitLabelMixin)。
"""

from django import forms
from django.db.models import Q
from django.utils import timezone

from seizou.core.forms import SubmitLabelMixin, label_decimal, label_number, line_totals, line_unit
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


class ShipmentForm(SubmitLabelMixin, forms.Form):
    """出荷の入力。受注の明細の候補は候補の口 (orders:orderline_select) が残のある明細だけを返す。

    欄の値として受けるのは全部の明細 (取消の受注の明細も)。取消の受注の明細は services が R05 で断る
    (画面で先に断らない)。
    保存のボタンは「10 個を出荷する」(明細の決まっている形。決まっていなければ「出荷する」。N138)。
    """

    submit_label_plain = "出荷する"

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
        # 保存のボタンの字 (N138): 描いたときの明細の単位で「10 個を出荷する」。明細を選び直したら JS が「出荷する」に戻す (data-label-line)。
        self.label_line = line
        self.fields["qty"].widget.attrs["data-label-qty"] = True
        if line is not None:
            self.fields["order_line"].widget.attrs["data-label-line"] = str(line.pk)

    def submit_label_templates(self) -> list:
        if self.label_line is None:
            return []
        unit = line_unit(self.label_line)
        return [f"{{qty}} {unit}を出荷する" if unit else "{qty} を出荷する"]

    def submit_label_values(self) -> dict:
        return {"qty": label_number(self.submit_value("qty"))}

    def service_kwargs(self) -> dict:
        data = self.cleaned_data
        return {
            "order_line": data["order_line"],
            "qty": data["qty"],
            "shipped_at": data["shipped_at"],
            "note": data.get("note", ""),
        }


#: 一括出荷で出す行が 1 つも無いときの誤りの文
NO_BULK_LINES = "出荷する明細がありません。生産管理ボードで行を選び直してください。"
#: 一括出荷の行ごとの欄の名前の頭 (数量と「この行を出さない」)
BULK_QTY_PREFIX = "qty_"
BULK_SKIP_PREFIX = "skip_"


class ShipmentBulkForm(SubmitLabelMixin, forms.Form):
    """一括出荷の入力 (注文残の盤で選んだ明細を、同じ出荷日でまとめて出荷する。N124)。

    明細の pk は line の繰り返し。開いたときは土台の get_initial が写した字か字の list (?line=<pk>&line=<pk>)、
    送ったときは行ごとの隠しの欄 line (並びは選んだ順。数でない字は飛ばし、重なりは 1 つにする)。
    行ごとの欄は qty_<pk> (数量。初期値は残の数) と skip_<pk> (この行を出さない)。rows がテンプレートの表の行。
    開いたときは、取消の受注と残の無い明細を行に出さず、その数を dropped に持つ。送ったときは送られた行をそのまま
    services に渡す (開いた後に取消になった明細は services が R05 で断る。画面で先に断らない)。
    数を残と比べない (受注より多い出荷は、検査の層の一括出荷の R01 が会社の設定に従って見る)。
    保存のボタンは「2 行・合計 30 個を出荷する」(行の単位が違えば「2 行を出荷する」。N138)。
    """

    submit_label_plain = "出荷する"

    shipped_at = forms.DateField(
        label="出荷日",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        error_messages=SHIPPED_AT_ERRORS,
    )
    note = forms.CharField(label="備考", max_length=500, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound:
            getlist = getattr(self.data, "getlist", None)
            raw = getlist("line") if getlist is not None else self.data.get("line")
        else:
            raw = self.initial.get("line")
        values = raw if isinstance(raw, (list, tuple)) else ([] if raw is None else [raw])
        pks = []
        for value in values:
            text = str(value).strip()
            if text.isdigit() and int(text) not in pks:
                pks.append(int(text))
        found = {
            line.pk: line
            for line in _OrderLine.objects.select_related("order__customer", "item").filter(pk__in=pks)
        }
        self.rows = []
        self.dropped = 0
        for pk in pks:
            line = found.get(pk)
            if line is None:
                continue
            remaining = remaining_qty(line)
            if not self.is_bound and (line.order.status == "cancelled" or remaining <= 0):
                self.dropped += 1
                continue
            self.fields[f"{BULK_QTY_PREFIX}{pk}"] = forms.DecimalField(
                label="数量", max_digits=12, decimal_places=2, initial=remaining
            )
            self.fields[f"{BULK_SKIP_PREFIX}{pk}"] = forms.BooleanField(label="この行を出さない", required=False)
            # 保存のボタンの字を JS が埋め直す印 (N138。同じ行の数量と「この行を出さない」)。
            self.fields[f"{BULK_QTY_PREFIX}{pk}"].widget.attrs["data-label-qty"] = True
            self.fields[f"{BULK_SKIP_PREFIX}{pk}"].widget.attrs["data-label-skip"] = True
            self.rows.append(
                {
                    "line": line,
                    "remaining": remaining,
                    "qty": self[f"{BULK_QTY_PREFIX}{pk}"],
                    "skip": self[f"{BULK_SKIP_PREFIX}{pk}"],
                }
            )

    @property
    def head_fields(self):
        """表の上の欄 (出荷日・備考と、土台が足す会社ごとの項目)。行ごとの欄は表に描くので除く。"""
        return [
            field
            for field in self.visible_fields()
            if not field.name.startswith((BULK_QTY_PREFIX, BULK_SKIP_PREFIX))
        ]

    def submit_label_templates(self) -> list:
        if not self.rows:
            return []
        units = {line_unit(row["line"]) for row in self.rows}
        if len(units) == 1 and "" not in units:
            return [f"{{rows}} 行・合計 {{qty}} {units.pop()}を出荷する", "{rows} 行を出荷する"]
        return ["{rows} 行を出荷する"]

    def line_pairs(self):
        """合計の枠とボタンの字に数える明細の (数量, 単価)。「この行を出さない」の行は数えない。単価は無い (None)。
        数量の分からない行があれば None (分からない)。"""
        pairs = []
        for row in self.rows:
            pk = row["line"].pk
            if self.submit_value(f"{BULK_SKIP_PREFIX}{pk}"):
                continue
            qty = self.submit_value(f"{BULK_QTY_PREFIX}{pk}")
            if label_decimal(qty) is None:
                return None
            pairs.append((qty, None))
        return pairs

    def line_totals(self) -> dict:
        return line_totals(self.line_pairs(), show_amount=False)

    def submit_label_values(self) -> dict:
        totals = self.line_totals()
        if not totals["rows"]:
            return {}
        return {"rows": str(totals["rows"]), "qty": totals["qty"]}

    def _picked(self, cleaned):
        return [row for row in self.rows if not cleaned.get(f"{BULK_SKIP_PREFIX}{row['line'].pk}")]

    def clean(self):
        cleaned = super().clean()
        if not self._picked(cleaned):
            raise forms.ValidationError(NO_BULK_LINES)
        return cleaned

    def service_kwargs(self) -> dict:
        data = self.cleaned_data
        return {
            "lines": [(row["line"], data[f"{BULK_QTY_PREFIX}{row['line'].pk}"]) for row in self._picked(data)],
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
    """納品書を取り消す form。欄は無い。保存のボタンは「DN-000001 を取り消す」(土台が対象を instance= で渡す。N138)。"""
    submit_label = "取り消す"

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        if instance is not None:
            self.submit_label = f"{instance.note_no} を取り消す"

    def service_kwargs(self) -> dict:
        return {}
