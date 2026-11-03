"""orders の入力の form。保存は form でなく services が行う (土台が ModelForm の save を呼ぶことは無い)。

OrderForm: 受注の項目と、明細の行の formset (lines) を 1 つに持つ。is_valid() で両方を見て、
service_kwargs() が services.create_order / update_order の引数を作る (01 の 10.4 の lines の辞書の並び)。
既にある明細は order_line の鍵 (この受注の OrderLine) で指す。検査の層の R04 もこの鍵を読む。

選べる設定 (seizou.core.conf):
- order_form が single のときは明細を 1 行に固定する (表は同じ。01 の 9 節)。
- hidden_fields に名前が並んだ欄を外す。決まりが見る欄 (得意先・品目・数量・単価) と、
  services が要る受注日は外さない。外した欄の鍵は services に渡さない (直すときは今の値のまま)。
"""

from decimal import Decimal

from django import forms

from seizou.core import fields as company_fields
from seizou.core.conf import setting
from seizou.core.forms import ServiceModelForm
from seizou.core.views import widget_css
from seizou.core.widgets import RemoteSelect

from .models import Order, OrderLine

# 品目の表は、明細の外部キーの先として引く (masters の models を import しない)。
Item = OrderLine._meta.get_field("item").related_model

#: hidden_fields に並んでも外さない欄
KEPT_FIELDS = frozenset({"customer", "item", "qty", "unit_price", "order_date"})
#: 明細の行から services に渡す鍵
LINE_KEYS = ("item", "qty", "unit", "unit_price", "due_date", "note")
#: 直すときに data に入れる受注の鍵
ORDER_KEYS = ("customer", "order_date", "customer_order_no", "note")
#: 明細の行の formset の prefix (テンプレートと行を足す JS が使う)
LINES_PREFIX = "lines"
#: 選ぶ欄の空の選択肢の字 (選ぶ欄の JS が載る前と、JS が無いときに見える)
EMPTY_LABEL = "選んでください"
#: 金額の閲覧が無い人 (権限の判定の on) が、直す画面で明細の行を足したときの欄の外の誤り
NEW_LINE_WITHOUT_MONEY = "金額の閲覧の権限が無い方は、明細の行を足せません。足すときはアカウント管理の担当者に頼んでください。"


def hidden_names() -> set[str]:
    """使わない項目の設定のうち、外してよい欄の名前。"""
    return set(setting("hidden_fields") or ()) - KEPT_FIELDS


def _date_input():
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class OrderLineForm(forms.Form):
    """明細の 1 行。order_line は既にある明細を指す隠しの欄 (足す行は空)。"""

    order_line = forms.ModelChoiceField(
        queryset=OrderLine.objects.none(), required=False, widget=forms.HiddenInput
    )
    item = forms.ModelChoiceField(
        label="部品",
        queryset=Item.objects.all(),
        widget=RemoteSelect("masters:item_select"),
        empty_label=EMPTY_LABEL,
    )
    # 0 以下は保存の前に欄の横で断る (R02 の決まりは services にもあり、こちらは入力の誤りの案内。初見テスト 2 回目の所見)
    qty = forms.DecimalField(
        label="数量",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        error_messages={"min_value": "数量は 0 より大きい数にしてください。"},
    )
    unit = forms.CharField(label="単位", max_length=10, required=False)
    unit_price = forms.DecimalField(
        label="単価",
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        error_messages={"min_value": "単価は 0 より大きい数にしてください。"},
    )
    due_date = forms.DateField(label="納期", required=False, widget=_date_input())
    note = forms.CharField(label="備考", max_length=500, required=False)

    def __init__(self, *args, order=None, hide_money=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.order = order
        self.hide_money = hide_money
        if order is not None:
            # 指せるのはこの受注の明細だけ (ほかの受注の明細の番号が来たら欄の誤り)。
            self.fields["order_line"].queryset = OrderLine.objects.filter(order=order)
        for name in hidden_names():
            self.fields.pop(name, None)
        if hide_money:
            # 金額の閲覧が無い人には単価の欄を出さない。line() は欄に無い鍵を渡さないので、既にある明細の単価は今のまま。
            self.fields.pop("unit_price", None)
        # 入力の土台は外側の form の欄にだけ見た目の class を付けるので、行の欄には同じ関数で付ける。
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", widget_css(field.widget))

    def clean(self):
        data = super().clean()
        # 単価の欄の無い人が直す画面で行を足すと、services が新しい明細の単価を要るので、ここで断る (消す印の行は除く)。
        if self.hide_money and self.order is not None and not data.get("DELETE"):
            if data.get("order_line") is None and self.has_changed():
                raise forms.ValidationError(NEW_LINE_WITHOUT_MONEY)
        return data

    def line(self) -> dict | None:
        """services に渡す明細の辞書。消す印の行と空の行は None。"""
        data = getattr(self, "cleaned_data", None) or {}
        if not data or data.get("DELETE"):
            return None
        existing = data.get("order_line")
        if existing is None and not self.has_changed():
            return None
        line = {key: data[key] for key in LINE_KEYS if key in data}
        if existing is not None:
            line["order_line"] = existing
        elif not line.get("unit"):
            # 足す明細の単位が空なら鍵を渡さず、services に品目の単位を写させる。
            line.pop("unit", None)
        return line


class OrderForm(ServiceModelForm):
    """受注の項目と明細の行。入れる画面と直す画面で同じ form を使う (入れる画面には備考を置かない)。"""

    class Meta:
        model = Order
        fields = ["customer", "order_date", "customer_order_no", "note"]
        widgets = {
            "customer": RemoteSelect("masters:partner_select"),
            "order_date": _date_input(),
        }

    def __init__(self, data=None, files=None, *args, instance=None, hide_money=False, **kwargs):
        super().__init__(data, files, *args, instance=instance, **kwargs)
        self.creating = instance is None
        self.fields["customer"].empty_label = EMPTY_LABEL
        if self.creating:
            # create_order に note の引数が無いので、入れる画面に備考の欄を置かない。
            self.fields.pop("note")
        for name in hidden_names():
            self.fields.pop(name, None)
        self.single = setting("order_form") == "single"
        rows = [] if self.creating else list(instance.lines.order_by("line_no"))
        formset_class = forms.formset_factory(
            OrderLineForm,
            extra=(0 if rows else 1) if self.single else 1,
            can_delete=not self.single,
            max_num=1 if self.single else 1000,
        )
        self.lines = formset_class(
            data=data,
            prefix=LINES_PREFIX,
            initial=[self.line_initial(row) for row in rows],
            form_kwargs={"order": None if self.creating else instance, "hide_money": hide_money},
        )

    @staticmethod
    def line_initial(row) -> dict:
        return {
            "order_line": row.pk,
            "item": row.item_id,
            "qty": row.qty,
            "unit": row.unit,
            "unit_price": row.unit_price,
            "due_date": row.due_date,
            "note": row.note,
        }

    def is_valid(self):
        header = super().is_valid()
        rows = self.lines.is_valid()
        return header and rows

    def line_dicts(self) -> list[dict]:
        return [line for line in (row.line() for row in self.lines.forms) if line is not None]

    def service_kwargs(self) -> dict:
        data, ext = company_fields.split_ext(self.cleaned_data)
        if self.creating:
            kwargs = {
                "customer": data["customer"],
                "lines": self.line_dicts(),
                "order_date": data["order_date"],
                "customer_order_no": data.get("customer_order_no", ""),
            }
        else:
            kwargs = {
                "data": {key: data[key] for key in ORDER_KEYS if key in data},
                "lines": self.line_dicts(),
            }
        if ext is not None:
            kwargs["ext"] = ext
        return kwargs


class OrderCancelForm(forms.Form):
    """受注の取消。理由は備考の末尾に足される (services.cancel_order)。"""

    reason = forms.CharField(
        label="取消の理由",
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="書くと、受注の備考の末尾に「取消の理由: …」として残ります。",
    )

    def service_kwargs(self) -> dict:
        return {"reason": self.cleaned_data["reason"]}
