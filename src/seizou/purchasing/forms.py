"""purchasing の入力の form。保存は form でなく services が行う (土台が form の save を呼ぶことは無い)。

- 発注を入れる form は、発注の項目の欄と、明細の行の formset (prefix "lines") を持つ。
  is_valid() は両方を確かめ、service_kwargs() が create_purchase_order の引数
  (partner・lines・order_date・note と、会社ごとの項目があれば ext) を作る。
- 送り直す form は欄が無い。service_kwargs() は空の辞書 (土台が resend_purchase_order(発注, user=...) を呼ぶ)。
- 取り消す form は理由の欄 1 つ。
- ほかの部品の model は import せず、外部キーの先を related_model で引く (読むだけ)。
- 取引先と部品を選ぶ欄は字を打って探す欄 (RemoteSelect)。取引先の候補の口は masters の取引先の口に role=supplier (協力企業か両方の物だけ)。
  部品の候補の口は purchasing の部品の口 (purchasing:item_select) で、単位だけを返し標準単価を返さない (協力企業への単価は利用者が打つ。C01-1 = a)。
  送られた値は、どちらも __init__ の queryset (使用中だけ。取引先は協力企業か両方) が確かめる。
- 欄の見た目の class は固定の字で書く (土台の base.html の注記にある物だけ。CSS に入れるため)。
- 発注を入れる form は、初期値の from_line (受注の明細の pk。注残の盤の「発注する」が GET で渡す) があれば、
  その明細の部品・残の数・単位・納期を最初の明細の行の初期値にする。orders の model は import せず apps.get_model で引く。
  単位の初期値は受注の明細の数量単位コード (単位の欄は一覧から選ぶ欄。コードの無い受注の明細は空で開く)。
  line (盤で選んだ明細の pk の繰り返し) があれば、選んだ明細を同じ形で順に埋める (N124)。
- 発注・受入・取り消すの保存のボタンの字は、押した結果の数と金額と対象を書く (N138。字を組むのは core の SubmitLabelMixin)。
  金額の閲覧が無い人 (土台が hide_money=True を渡す) には、発注のボタンに金額を書かない。
- 仕入明細を作る form (SelfInvoiceForm。候補 C111) は請求書の新規登録 (billing.forms.InvoiceForm) と同じ形: 取引先・発行日の欄の下に受入の表の行 (rows)、
  印を付けた行の数と税抜の合計を合計の枠 (line_totals) と保存のボタンの字に出し、行の checkbox の data-partner でテンプレートの JS が取引先で行を絞る。
  候補は services.self_invoice_receipts() (取消でない仕入明細に入っていない、seizou どうしで送った発注の受入)。金額は受入の金額そのまま。
"""

from decimal import Decimal

from django import forms
from django.apps import apps
from django.utils import timezone

from seizou.core import edi_codes
from seizou.core import fields as company_fields
from seizou.core.forms import DefaultChoiceField, PlainNumberInput, SubmitLabelMixin, label_number, line_totals, line_unit
from seizou.core.views import use_decimal_input
from seizou.core.widgets import IsoDateInput, RemoteSelect
from seizou.masters.services import PRICE_BASIS_CHOICES, PRICE_BASIS_LUMP, PRICE_BASIS_UNIT, active_items, active_partners
from seizou.masters.services import active_processes, with_default_process
from seizou.masters.services import UNLISTED_LINE_UNIT, quantity_units

from . import services
from .models import PurchaseOrder, PurchaseOrderLine, _qty_text
from .models import Receipt

LINE_KEYS = ("item", "process", "qty", "unit", "price_basis", "unit_price", "quote_no", "due_date", "note")


def _date_input():
    return IsoDateInput(attrs={"class": "input w-full"})


class PurchaseOrderLineForm(forms.Form):
    """明細の 1 行。何も入れていない行は formset が飛ばす。"""

    item = forms.ModelChoiceField(
        label="部品", queryset=None, widget=RemoteSelect("purchasing:item_select", attrs={"class": "w-full"})
    )
    # 協力企業に出す工程 (決め PRICE-5。空でもよい)。選択肢は使用中で協力企業に出す工程だけ。協力企業に出す工程の無い会社では欄を外す (C100)。
    process = forms.ModelChoiceField(
        label="工程",
        queryset=None,
        required=False,
        empty_label="指定しない",
        widget=forms.Select(attrs={"class": "select w-full"}),
        help_text="協力企業に出す工程。",
    )
    qty = forms.DecimalField(
        label="数量",
        max_digits=12,
        decimal_places=2,
        widget=PlainNumberInput(attrs={"class": "input w-full", "step": "any", "data-label-qty": True}),
    )
    # 単位は数量単位の一覧から選ぶ (字を打って探す欄。作者の決め)。送る値は一覧のコード、clean_unit が一覧の名前の字にする。
    unit = forms.ModelChoiceField(
        label="単位",
        queryset=None,
        required=False,
        widget=RemoteSelect("masters:quantityunit_select", attrs={"class": "w-full"}),
        help_text="空なら部品の単位を写します。",
    )
    # 単価の単位 (決め PRICE-1b)。「式」は数量を掛けずに単価を金額にする。値と字は部品マスターの単価表と同じ。
    # data-label-lump は、合計の枠と保存のボタンの字の JS (seizou-submit-label.js) が式の行を数量倍しない印。
    price_basis = DefaultChoiceField(
        label="単価の単位",
        choices=PRICE_BASIS_CHOICES,
        default=PRICE_BASIS_UNIT,
        widget=forms.Select(attrs={"class": "select w-full", "data-label-lump": PRICE_BASIS_LUMP}),
    )
    unit_price = forms.DecimalField(
        label="単価",
        max_digits=14,
        decimal_places=2,
        widget=PlainNumberInput(attrs={"class": "input w-full", "step": "any", "data-label-price": True}),
    )
    # 単価表から写した見積の番号 (決め PRICE-0)。表の列にせず、単価の枡の中に置く (purchasing/_purchaseorder_card.html)。
    quote_no = forms.CharField(
        label="見積の番号",
        max_length=35,
        required=False,
        widget=forms.TextInput(attrs={"class": "input input-sm w-full", "placeholder": "見積の番号", "aria-label": "見積の番号"}),
    )
    due_date = forms.DateField(label="納期", required=False, widget=_date_input())
    note = forms.CharField(
        label="備考",
        max_length=500,
        required=False,
        widget=forms.TextInput(attrs={"class": "input w-full"}),
    )

    def __init__(self, *args, processes=None, has_process=None, **kwargs):
        super().__init__(*args, **kwargs)
        # 部品は使用中だけ (N135。発注は新しく作るだけの form)
        self.fields["item"].queryset = active_items()
        self.fields["unit"].queryset = quantity_units()
        # 工程の選択肢と「協力企業に出す工程があるか」は、発注の form が form_kwargs で渡す (行の数だけ引かない。C100)
        if processes is None:
            processes = active_processes().filter(is_outsourced=True)
        if has_process is None:
            has_process = processes.exists()
        if has_process:
            self.fields["process"].queryset = processes
        else:
            self.fields.pop("process")
        # 数量と単価を字の欄にする (受注の明細と土台と同じ。初期値は詳細と同じ字で出し、「1,200」も通す。初見テスト 5 回目 a-flagged-12)。
        for field in self.fields.values():
            use_decimal_input(field)

    def clean_unit(self):
        """選んだ一覧の行の名前の字。選ばなければ空の字 (services が部品の単位とコードを写す)。"""
        chosen = self.cleaned_data.get("unit")
        return chosen.name if chosen is not None else ""

    def clean(self):
        """行の検査。部品の単位が数量単位の一覧に無い字で、単位を選んでいなければ、単位の欄の誤りにする (何も入れていない行は formset が検査しない)。

        訳: 一覧に無い単位の字は、相手の会社に個として読まれるおそれがあるので、明細には入れない (選び直してもらう)。出所 = 作者の決め・中小企業共通 EDI 本編 7.3.2 (4) 数量単位コード。
        変えてよい条件 = 一覧に無い字も明細に入れてよいと決めたとき。
        """
        data = super().clean()
        item = data.get("item")
        if not data.get("unit") and not self.has_error("unit") and getattr(item, "unit_unlisted", False):
            self.add_error("unit", UNLISTED_LINE_UNIT.format(unit=item.unit))
        return data


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


class PurchaseOrderForm(SubmitLabelMixin, forms.Form):
    """発注を入れる form。保存と同時に送るのは services (create_purchase_order) の中。

    保存のボタンは「3 行・合計 ¥45,000 で発注する」(単価の無い行があれば「3 行で発注する」。金額の閲覧が無い人も「3 行で発注する」。N138)。
    """

    submit_label_plain = "発注する"

    partner = forms.ModelChoiceField(
        label="取引先", queryset=None, widget=RemoteSelect("masters:partner_select", query={"role": "supplier"})
    )
    order_date = forms.DateField(label="発注日", initial=timezone.localdate, widget=_date_input())
    note = forms.CharField(
        label="備考", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args, hide_money=False, **kwargs):
        self.hide_money = hide_money
        super().__init__(*args, **kwargs)
        partner_model = PurchaseOrder._meta.get_field("partner").related_model
        self.fields["partner"].queryset = active_partners().filter(
            role__in=(partner_model.ROLE_SUBCONTRACTOR, partner_model.ROLE_BOTH)
        )
        # 注残の盤の「発注する」から来たとき (GET の from_line)、その明細を最初の行の初期値にする。
        # 単価は入れない (協力企業の単価は受注の単価と別で、利用者が打つ)。無い pk・読めない字は初期値なし。
        # 単位は受注の明細の数量単位コード (選ぶ欄は一覧のコードで描く。コードの無い受注の明細は空で開く)。
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
        # 発注の明細の工程の最初の値 (決め PRICE-5): 選んだ明細の部品の分を 1 回の問い合わせで引く (盤は何行も選べるので行ごとに引かない。C100)。
        defaults = {}
        if picked:
            items = with_default_process(active_items()).filter(pk__in=[line.item_id for line, _rest in picked])
            defaults = {row.pk: row.default_process for row in items}
        line_initial = [
            {"item": line.item_id, "process": defaults.get(line.item_id), "qty": rest, "unit": line.unit_code or None, "due_date": line.due_date}
            for line, rest in picked
        ] or None
        # 協力企業に出す工程の選択肢と、あるか (テンプレートが「工程」の見出しの列を出すかに読む)
        processes = active_processes().filter(is_outsourced=True)
        self.has_process = processes.exists()
        self.lines = PurchaseOrderLineFormSet(
            data=self.data if self.is_bound else None,
            initial=line_initial,
            prefix="lines",
            form_kwargs={"processes": processes, "has_process": self.has_process},
        )

    def submit_label_templates(self) -> list:
        if self.hide_money:
            return ["{rows} 行で発注する"]
        return ["{rows} 行・合計 ¥{amount} で発注する", "{rows} 行で発注する"]

    def line_pairs(self):
        """合計の枠とボタンの字に数える明細の (数量, 単価)。開いた形は初期値の行 (盤で選んだ明細。単価は空)、送った形は入れた行の通った値。
        送って止まった形で数量の通らなかった行があれば None (分からない)。"""
        pairs = []
        for row in self.lines.forms:
            if self.is_bound:
                if not row.has_changed():
                    continue
                values = getattr(row, "cleaned_data", None) or {}
                if values.get("qty") is None:
                    return None
            else:
                values = row.initial or {}
                if values.get("qty") is None:
                    continue
            pairs.append((values["qty"], values.get("unit_price"), values.get("price_basis") == PRICE_BASIS_LUMP))
        return pairs

    def line_totals(self) -> dict:
        return line_totals(self.line_pairs(), show_amount=not self.hide_money)

    def submit_label_values(self) -> dict:
        totals = self.line_totals()
        if not totals["rows"]:
            return {}
        return {"rows": str(totals["rows"]), "amount": totals["amount"]}

    def is_valid(self):
        # 発注の欄と明細の行の両方を確かめ、どちらの誤りも画面に出す。
        header_ok = super().is_valid()
        lines_ok = self.lines.is_valid()
        return header_ok and lines_ok

    def service_kwargs(self):
        data, ext = company_fields.split_ext(self.cleaned_data)
        lines = [
            # 工程の欄を外した会社 (協力企業に出す工程が無い) では、無い欄の鍵を services に渡さない (C100)
            {key: row.cleaned_data[key] for key in LINE_KEYS if key in row.cleaned_data}
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


#: 変更の form で、金額の閲覧が無い人が行を足したときの欄の誤り (単価が無いと新しい明細を送れないため。受注の直しの form と同じ形)
CHANGE_NEW_LINE_WITHOUT_MONEY = "単価を入れられないので、行を足せません。行を足すときは、金額を見られる人に頼んでください。"


class PurchaseOrderChangeLineForm(forms.Form):
    """送った発注の変更の明細の 1 行。line は既にある明細を指す隠しの欄 (足す行は空)。

    既にある明細の行は部品と単位を直せない (欄を出さず、字で出す。部品を変えるときは取り消して行を足す)。
    相手が出荷済み (purchasing.services.known_shipped_qty が 0 より大きい) の行は単価の欄と消す印を出さない (欄の横に「出荷済み s」)。
    金額の閲覧が無い人には単価の欄を出さない (既にある明細の単価は今のまま送る)。
    """

    line = forms.ModelChoiceField(queryset=PurchaseOrderLine.objects.none(), required=False, widget=forms.HiddenInput)
    item = forms.ModelChoiceField(
        label="部品", queryset=None, required=False, widget=RemoteSelect("purchasing:item_select", attrs={"class": "w-full"})
    )
    qty = forms.DecimalField(
        label="数量",
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=Decimal("0.01"),
        error_messages={"min_value": "数量は 0 より大きい数にしてください。"},
        widget=PlainNumberInput(attrs={"class": "input w-full", "step": "any"}),
    )
    unit = forms.CharField(label="単位", max_length=10, required=False, widget=forms.TextInput(attrs={"class": "input w-full"}))
    unit_price = forms.DecimalField(
        label="単価",
        max_digits=14,
        decimal_places=2,
        required=False,
        min_value=Decimal("0.01"),
        error_messages={"min_value": "単価は 0 より大きい数にしてください。"},
        widget=PlainNumberInput(attrs={"class": "input w-full", "step": "any"}),
    )
    due_date = forms.DateField(label="納期", required=False, widget=_date_input())
    note = forms.CharField(label="備考", max_length=500, required=False, widget=forms.TextInput(attrs={"class": "input w-full"}))

    def __init__(self, *args, purchase_order=None, hide_money=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.hide_money = hide_money
        for field in self.fields.values():
            use_decimal_input(field)
        self.fields["item"].queryset = active_items()
        if purchase_order is not None:
            self.fields["line"].queryset = purchase_order.lines.exclude(line_status_code=edi_codes.LINE_DELETED)
        self.existing = self.initial.get("line_obj")
        self.shipped = services.known_shipped_qty(self.existing) if self.existing is not None else Decimal("0")
        self.shipped_text = _qty_text(self.shipped)
        if self.existing is not None:
            self.fields.pop("item")
            self.fields.pop("unit")
        if hide_money or self.shipped > 0:
            self.fields.pop("unit_price")

    def clean(self):
        data = super().clean()
        if data.get("DELETE"):
            return data
        if self.existing is not None:
            if data.get("qty") is None and "qty" not in self.errors:
                self.add_error("qty", "数量を入れてください。")
            return data
        if not self.has_changed():
            return data
        if self.hide_money:
            raise forms.ValidationError(CHANGE_NEW_LINE_WITHOUT_MONEY)
        for name, message in (("item", "部品を選んでください。"), ("qty", "数量を入れてください。"), ("unit_price", "単価を入れてください。")):
            if data.get(name) is None and name not in self.errors:
                self.add_error(name, message)
        return data

    def line_dict(self) -> dict | None:
        """services.change_purchase_order に渡す明細の辞書。消す印の行と、何も入れていない足す行は None。"""
        data = getattr(self, "cleaned_data", None) or {}
        if not data or data.get("DELETE"):
            return None
        if self.existing is not None:
            line = {"line": self.existing, "qty": data["qty"], "due_date": data.get("due_date"), "note": data.get("note", "")}
            if "unit_price" in self.fields:
                line["unit_price"] = data["unit_price"]
            return line
        if not self.has_changed():
            return None
        line = {key: data.get(key) if key != "note" else data.get(key, "") for key in LINE_KEYS}
        # 変更の画面には単価の単位の欄と見積の番号の欄が無い (C96 は C99・C100 より前の形)。
        # 足す行の単価の単位は部品の単位 (unit = 既定)、見積の番号は空の字 (表の既定。None は保存で止まる)。
        if line.get("price_basis") is None:
            line["price_basis"] = PRICE_BASIS_UNIT
        if line.get("quote_no") is None:
            line["quote_no"] = ""
        return line


class PurchaseOrderChangeLineFormSet(forms.BaseFormSet):
    """変更の明細の formset。相手が出荷済みの行には消す印を出さない (取り消せないため)。"""

    def add_fields(self, form, index):
        super().add_fields(form, index)
        if getattr(form, "shipped", 0) > 0:
            form.fields.pop("DELETE", None)


class PurchaseOrderChangeForm(SubmitLabelMixin, forms.Form):
    """送った発注の変更の form (備考と明細の formset)。保存と同時に第 n 版の変更として送るのは services (change_purchase_order) の中。

    形は受注の直しの form (orders.forms.OrderForm と OrderLineForm) にそろえる: 既にある明細は隠しの欄 line で指し、消す印 (DELETE) で取り消す。
    取り消した明細 (明細状態コード 2) は form に出さない。協力企業と発注日は直せない (字で出す)。
    保存のボタンは「第 n 版の変更を送る」(n は今の版 + 1)。金額の閲覧が無い人は単価の欄が無く、行も足せない。
    """

    note = forms.CharField(label="備考", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, data=None, *args, instance=None, hide_money=False, **kwargs):
        super().__init__(data, *args, **kwargs)
        self.instance = instance
        self.hide_money = hide_money
        if instance is None:
            self.lines = PurchaseOrderChangeLineFormSet(data=data, prefix="lines")
            return
        self.submit_label_plain = f"第 {instance.revision_no + 1} 版の変更を送る"
        if not self.is_bound:
            self.initial.setdefault("note", instance.note)
        rows = list(
            services.annotate_received(instance.lines.exclude(line_status_code=edi_codes.LINE_DELETED).select_related("item"))
            .order_by("line_no")
        )
        formset_class = forms.formset_factory(
            PurchaseOrderChangeLineForm, formset=PurchaseOrderChangeLineFormSet, extra=0 if hide_money else 1, can_delete=True
        )
        self.lines = formset_class(
            data=data,
            prefix="lines",
            initial=[self.line_initial(row) for row in rows],
            form_kwargs={"purchase_order": instance, "hide_money": hide_money},
        )

    @staticmethod
    def line_initial(row) -> dict:
        return {
            "line": row.pk,
            "line_obj": row,
            "qty": row.qty,
            "unit_price": row.unit_price,
            "due_date": row.due_date,
            "note": row.note,
        }

    def is_valid(self):
        header = super().is_valid()
        rows = self.lines.is_valid()
        return header and rows

    def service_kwargs(self):
        lines = [line for line in (row.line_dict() for row in self.lines.forms) if line is not None]
        return {"data": {"note": self.cleaned_data.get("note", "")}, "lines": lines}


class SendForm(forms.Form):
    """送り直す form。欄は無い。"""
    submit_label = "送り直す"

    def service_kwargs(self):
        return {}


class CancelForm(forms.Form):
    """取り消す form。理由は備考の末尾に「取消の理由: …」で残る。保存のボタンは「PO-000001 を取り消す」(N138)。"""
    submit_label = "取り消す"
    danger = True  # 実行のボタンを削除の確かめと同じ赤 (btn btn-error) にする (core/_form_card.html。初見テスト R5 の U125。候補 C24)

    reason = forms.CharField(
        label="取消の理由", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3})
    )

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        if instance is not None:
            self.submit_label = f"{instance.po_no} を取り消す"

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


def _old_revision_notice(line):
    """明細のまだ受け入れていない出荷の知らせ (services.notice_rests) のうち、今の版より前の版の注文への知らせの版の一番小さい値。無ければ None。"""
    current = line.purchase_order.revision_no
    if current < 2:
        return None
    olds = [
        entry["notice"].order_revision_no
        for entry in services.notice_rests(line)
        if entry["rest"] > 0 and getattr(entry["notice"], "order_revision_no", current) < current
    ]
    return min(olds) if olds else None


#: 受入の入力で発注の明細が決まっていないときの、数量の欄の下の字 (purchasing/receipt-form.js も同じ字を出す)
NO_LINE_HELP = "発注の明細を選ぶと、受入の残が出ます。"


class ReceiptForm(SubmitLabelMixin, forms.Form):
    """受入の form。発注の明細は字を打って探す欄 (候補は受入の残がある明細だけ)。

    欄の値として受けるのは全部の明細で、取消の発注の明細は services が日本語の文で断る (出荷の form と同じ考え)。
    初期値の line (発注の詳細の「受入を登録」が GET の ?line=<pk> で渡す) があれば、その明細の受入の残を数量の初期値にする
    (受入の残は purchasing.services.remaining_qty。受注の明細の残 = orders の同じ名前の関数とは別)。
    数量の欄の下に、選んだ明細の受入の残を出す (数を比べて断ることはしない。初見テスト R5 の候補 C64)。
    保存のボタンは「5 個を受け入れる」(明細の決まっている形。決まっていなければ「受け入れる」。N138)。
    """

    submit_label_plain = "受け入れる"

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
        widget=PlainNumberInput(attrs={"class": "input w-full", "step": "any"}),
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
        rest = None
        if line is not None:
            rest = services.remaining_qty(line)
            if rest > 0:
                self.initial.setdefault("qty", rest)
        # 保存のボタンの字 (N138): 描いたときの明細の単位で「5 個を受け入れる」。明細を選び直したら JS が「受け入れる」に戻す。
        self.label_line = _purchase_line(self.data.get("line")) if self.is_bound else line
        # 数量の欄の下に、同じ明細 (送った値か ?line= の初期値) の受入の残を出す (出荷の入力の「残り n」と同じ置き場。C64)。
        # 0 やマイナスもそのまま出す (比べない・断らない)。?qty= (出荷の知らせの残り) で数量が入っていても、欄の下は明細の受入の残。
        # help_text は欄の宣言でなくここで置く (説明書の「入れる物」の表は宣言 = base_fields を読むので、字を変えない)。
        if self.label_line is None:
            self.fields["qty"].help_text = NO_LINE_HELP
        else:
            if self.is_bound:
                rest = services.remaining_qty(self.label_line)
            self.fields["qty"].help_text = f"受入の残 {_qty_text(rest)}"
        self.fields["qty"].widget.attrs["data-label-qty"] = True
        if self.label_line is not None:
            self.fields["line"].widget.attrs["data-label-line"] = str(self.label_line.pk)
            # まだ受け入れていない出荷の知らせに前の版の注文への知らせがあれば、明細の欄の下に印を出す (止めない。押す手は増えない)。
            # 明細を選び直しても、この字は描いた時の明細の物のまま (purchasing/receipt-form.js は数量の欄の下だけを書き替える)。
            old = _old_revision_notice(self.label_line)
            if old is not None:
                self.fields["line"].help_text = f"第 {old} 版の注文への出荷の知らせです (今は第 {self.label_line.purchase_order.revision_no} 版)。"

    def submit_label_templates(self) -> list:
        if self.label_line is None:
            return []
        unit = line_unit(self.label_line)
        return [f"{{qty}} {unit}を受け入れる" if unit else "{qty} を受け入れる"]

    def submit_label_values(self) -> dict:
        return {"qty": label_number(self.submit_value("qty"))}

    def service_kwargs(self):
        data = self.cleaned_data
        return {"line": data["line"], "qty": data["qty"], "received_on": data["received_on"], "note": data["note"]}


# --- 仕入明細 (候補 C111) ---

_Partner = PurchaseOrder._meta.get_field("partner").related_model


class ReceiptCheckboxes(forms.CheckboxSelectMultiple):
    """受入の checkbox。行ごとに data-partner (受入の発注の取引先の pk) を付ける (テンプレートの JS が取引先の欄で行を絞る)。"""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-partner"] = instance.line.purchase_order.partner_id
        return option

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        # 外の枠 (div) には checkbox の見た目を付けない (Django の雛形は widget の class を外の枠にも付けるため)。
        context["widget"]["attrs"].pop("class", None)
        return context


class ReceiptChoiceField(forms.ModelMultipleChoiceField):
    """受入を選ぶ欄。字は「受入 受入日・発注番号-行番号・品番 品名・数量 n」(区切りは「・」。請求書の出荷の欄と同じ形)。"""

    def label_from_instance(self, obj):
        line = obj.line
        return (
            f"受入 {obj.received_on:%Y-%m-%d}・{line.purchase_order.po_no}-{line.line_no}"
            f"・{line.item.part_no} {line.item.name}・数量 {_qty_text(obj.qty)}"
        )


class SelfInvoiceForm(SubmitLabelMixin, forms.Form):
    """仕入明細を作る form (請求書を作る form と同じ形)。

    受入は表の行 (rows) で描き、印を付けた行の数と税抜の金額の合計を、合計の枠 (line_totals) と保存のボタンの字に出す。
    行の金額は受入の金額そのまま (保存と同じ値)。取引先を選んでいるときは、その取引先の行だけを数える (画面の JS が隠す行を数えない)。
    """

    submit_label_plain = "仕入明細を作る"

    partner = forms.ModelChoiceField(label="取引先", queryset=_Partner.objects.none(), widget=forms.Select)
    issued_on = forms.DateField(
        label="発行日",
        initial=timezone.localdate,
        widget=IsoDateInput(attrs={"class": "input w-full"}),
        help_text="仕入明細に載る日付です。",
    )
    receipts = ReceiptChoiceField(
        label="受入",
        queryset=Receipt.objects.none(),
        widget=ReceiptCheckboxes(attrs={"class": "checkbox"}),
        help_text="seizou どうしで注文を送った発注の受入のうち、まだ仕入明細に入っていない物だけが並びます。",
    )

    def __init__(self, *args, hide_money=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.hide_money = hide_money
        self._rows = None
        candidates = services.self_invoice_receipts()
        self.fields["partner"].queryset = _Partner.objects.filter(
            pk__in=candidates.values("line__purchase_order__partner_id")
        ).order_by("code")
        self.fields["receipts"].queryset = candidates.select_related(
            "line__purchase_order__partner", "line__item"
        )
        # 合計の枠と保存のボタンの字の JS (seizou-submit-label.js) が、印を付けた行だけを数える印 (全部の checkbox に写る)。
        self.fields["receipts"].widget.attrs["data-label-pick"] = True

    @property
    def head_fields(self):
        """表の上の欄 (取引先・発行日と、土台が足す会社ごとの項目)。受入の欄は表に描くので除く。"""
        return [field for field in self.visible_fields() if field.name != "receipts"]

    @property
    def rows(self):
        """受入の表の行。1 行 = {box・receipt・checked・qty_text・amount・amount_text}。

        最初に読んだ時に作って持つ (土台の get_form が __init__ の後に欄の widget を触るので、__init__ の中で作らない)。
        box は checkbox の subwidget。qty_text と amount_text は JS が読む素の数の字。amount は受入の金額 (金額の閲覧が無い人は None)。
        """
        if self._rows is None:
            rows = []
            for box in self["receipts"].subwidgets:
                receipt = box.data["value"].instance
                amount = None if self.hide_money else receipt.amount
                rows.append(
                    {
                        "box": box,
                        "receipt": receipt,
                        "checked": bool(box.data["selected"]),
                        "qty_text": f"{receipt.qty.normalize():f}",
                        "amount": amount,
                        "amount_text": None if amount is None else f"{amount:f}",
                    }
                )
            self._rows = rows
        return self._rows

    def _chosen_partner(self) -> str:
        """選んだ取引先の pk の字。送った form は送った値、開いた form は初期値。選んでいなければ空の字。"""
        value = self.data.get("partner") if self.is_bound else self.initial.get("partner")
        value = getattr(value, "pk", value)
        return "" if value is None else str(value)

    def line_totals(self) -> dict:
        """合計の枠 (core/_line_totals.html) の字。印を付けた行のうち、取引先を選んでいればその取引先の行だけを数える。"""
        chosen = self._chosen_partner()
        picked = [
            row
            for row in self.rows
            if row["checked"] and (not chosen or str(row["receipt"].line.purchase_order.partner_id) == chosen)
        ]
        qty = sum((row["receipt"].qty for row in picked), Decimal("0"))
        amount = None if self.hide_money else sum((row["amount"] for row in picked), Decimal("0"))
        return {
            "rows": len(picked),
            "qty": label_number(qty),
            "amount": None if amount is None else label_number(amount),
            "show_amount": not self.hide_money,
        }

    def submit_label_templates(self) -> list:
        if self.hide_money:
            return ["{rows} 行の仕入明細を作る"]
        return ["{rows} 行・税抜 ¥{amount} の仕入明細を作る", "{rows} 行の仕入明細を作る"]

    def submit_label_values(self) -> dict:
        totals = self.line_totals()
        if not totals["rows"]:
            return {}
        return {"rows": str(totals["rows"]), "amount": totals["amount"]}

    def service_kwargs(self):
        data = self.cleaned_data
        return {
            "partner": data["partner"],
            "receipts": list(data["receipts"] or []),
            "issued_on": data["issued_on"],
        }


class SelfInvoiceCancelForm(forms.Form):
    """仕入明細を取り消す form。欄は無い。保存のボタンは「SI-000001 を取り消す」(土台が対象を instance= で渡す。請求書の取消と同じ形)。"""

    submit_label = "取り消す"
    danger = True  # 実行のボタンを削除の確かめと同じ赤 (btn btn-error) にする (core/_form_card.html)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        if instance is not None:
            self.submit_label = f"{instance.self_invoice_no} を取り消す"

    def service_kwargs(self):
        return {}
