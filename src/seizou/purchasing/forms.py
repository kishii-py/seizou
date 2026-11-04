"""purchasing の入力の form。保存は form でなく services が行う (土台が form の save を呼ぶことは無い)。

- 発注を入れる form は、発注の項目の欄と、明細の行の formset (prefix "lines") を持つ。
  is_valid() は両方を確かめ、service_kwargs() が create_purchase_order の引数
  (partner・lines・order_date・note と、会社ごとの項目があれば ext) を作る。
- 送り直す form は欄が無い。service_kwargs() は空の辞書 (土台が resend_purchase_order(発注, user=...) を呼ぶ)。
- 取り消す form は理由の欄 1 つ。
- ほかの部品の model は import せず、外部キーの先を related_model で引く (読むだけ)。
- 取引先と部品を選ぶ欄は Django の標準の Select。取引先の候補は取引先の種類が協力企業か両方の物だけ。
- 欄の見た目の class は固定の字で書く (土台の base.html の注記にある物だけ。CSS に入れるため)。
- 発注を入れる form は、初期値の from_line (受注の明細の pk。注残の盤の「発注する」が GET で渡す) があれば、
  その明細の部品・残の数・単位・納期を最初の明細の行の初期値にする。orders の model は import せず apps.get_model で引く。
  line (盤で選んだ明細の pk の繰り返し) があれば、選んだ明細を同じ形で順に埋める (N124)。
"""

from django import forms
from django.apps import apps
from django.utils import timezone

from seizou.core import fields as company_fields
from seizou.core.widgets import RemoteSelect

from . import services
from .models import PurchaseOrder, PurchaseOrderLine

LINE_KEYS = ("item", "qty", "unit", "unit_price", "due_date", "note")


def _date_input():
    return forms.DateInput(attrs={"type": "date", "class": "input w-full"}, format="%Y-%m-%d")


class PurchaseOrderLineForm(forms.Form):
    """明細の 1 行。何も入れていない行は formset が飛ばす。"""

    item = forms.ModelChoiceField(
        label="部品", queryset=None, widget=forms.Select(attrs={"class": "select w-full"})
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
        help_text="空なら部品の単位を写します。",
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


def _order_line(raw):
    """受注の明細の pk の字から明細を引く (部品も一緒に読む)。読めない字・無い pk・orders の無い会社では None。"""
    if raw is None:
        return None
    try:
        pk = int(str(raw))
    except (TypeError, ValueError):
        return None
    try:
        line_model = apps.get_model("orders", "OrderLine")
    except LookupError:
        return None
    return line_model._default_manager.select_related("item").filter(pk=pk).first()


def _order_lines(raw):
    """GET の line (字か字の list。土台の get_initial が同じ鍵の繰り返しを list にする) から受注の明細を選んだ順に引く。読めない字は飛ばす。"""
    values = raw if isinstance(raw, list) else ([] if raw is None else [raw])
    lines = []
    for value in values:
        line = _order_line(value)
        if line is not None and all(line.pk != picked.pk for picked in lines):
            lines.append(line)
    return lines


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
        # 注残の盤の「発注する」から来たとき (GET の from_line)、その明細を最初の行の初期値にする。
        # 単価は入れない (協力企業の単価は受注の単価と別で、利用者が打つ)。無い pk・読めない字は初期値なし。
        # 盤の「選んだ行の操作」の「発注する」から来たとき (GET の line の繰り返し)、選んだ明細を順に埋める。
        # 取消の受注と残の無い明細は飛ばす。from_line と同じ明細は 1 行にする (N124)。
        from seizou.orders.services import remaining_qty

        self.from_line = _order_line(self.initial.get("from_line"))
        picked = [(self.from_line, remaining_qty(self.from_line))] if self.from_line is not None else []
        for line in _order_lines(self.initial.get("line")):
            if line.order.status == "cancelled" or any(line.pk == done.pk for done, _ in picked):
                continue
            rest = remaining_qty(line)
            if rest > 0:
                picked.append((line, rest))
        line_initial = [
            {"item": line.item_id, "qty": rest, "unit": line.unit, "due_date": line.due_date} for line, rest in picked
        ] or None
        self.lines = PurchaseOrderLineFormSet(
            data=self.data if self.is_bound else None, initial=line_initial, prefix="lines"
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
    submit_label = "送り直す"

    def service_kwargs(self):
        return {}


class CancelForm(forms.Form):
    """取り消す form。理由は備考の末尾に「取消の理由: …」で残る。"""
    submit_label = "取り消す"

    reason = forms.CharField(
        label="取消の理由", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3})
    )

    def service_kwargs(self):
        return {"reason": self.cleaned_data["reason"]}


def _purchase_line(raw):
    """発注の明細の pk の字から明細を引く。読めない字・1 未満・無い pk は None (画面は 200 のまま)。"""
    if raw is None:
        return None
    try:
        pk = int(str(raw))
    except (TypeError, ValueError):
        return None
    if pk < 1:
        return None
    return PurchaseOrderLine.objects.filter(pk=pk).first()


class ReceiptForm(forms.Form):
    """受入の form。発注の明細は字を打って探す欄 (候補は受入の残がある明細だけ)。

    欄の値として受けるのは全部の明細で、取消の発注の明細は services が日本語の文で断る (出荷の form と同じ考え)。
    初期値の line (発注の詳細の「受入を登録」が GET の ?line=<pk> で渡す) があれば、その明細の受入の残を数量の初期値にする
    (受入の残は purchasing.services.remaining_qty。受注の明細の残 = orders の同じ名前の関数とは別)。
    """

    line = forms.ModelChoiceField(
        label="発注の明細",
        queryset=PurchaseOrderLine.objects.select_related("purchase_order", "item"),
        widget=RemoteSelect("purchasing:purchaseorderline_select"),
        empty_label="選んでください",
    )
    received_on = forms.DateField(label="受入日", initial=timezone.localdate, widget=_date_input())
    qty = forms.DecimalField(
        label="数量",
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "input w-full", "step": "any"}),
    )
    note = forms.CharField(
        label="備考",
        max_length=500,
        required=False,
        widget=forms.TextInput(attrs={"class": "input w-full"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        line = _purchase_line(self.initial.get("line"))
        if line is not None:
            rest = services.remaining_qty(line)
            if rest > 0:
                self.initial.setdefault("qty", rest)

    def service_kwargs(self):
        data = self.cleaned_data
        return {"line": data["line"], "qty": data["qty"], "received_on": data["received_on"], "note": data["note"]}
