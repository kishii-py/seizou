"""masters の入力の form。保存は form でなく services が行う (土台が ModelForm の save を呼ぶことは無い)。

form に service_kwargs() が無いので、土台は cleaned_data を data= (会社ごとの項目は ext=) に分けて
services.create_item(data, user, ext=ext) / update_item(item, data, user, ext=ext) を呼ぶ (01 の 10.4)。
締日と支払日の min・max (1〜31) は土台の ServiceModelForm が表の検査から付ける。ここは欄の下の案内だけ。
"""

import re
from decimal import Decimal

from django import forms
from django.utils import timezone

from seizou.core import csvio
from seizou.core.forms import ServiceModelForm
from seizou.core.widgets import IsoDateInput, RemoteSelect

from .models import Item, ItemPrice, Partner, PartnerItem, Process
from .models import MONTH_END_DAY
from .models import company_defaults
from .models import ItemDrawingVersion
from .models import Holiday


#: 使用中の欄の下の案内 (取引先と部品で同じ字。N135)
ACTIVE_HELP = "外すと使用停止にします。受注と発注の候補に出なくなり、今までの受注と発注の記録はそのまま残ります。"

#: 取引先の端数の 2 欄の案内。{current} は会社の既定の今の値 (会社の行の選択肢の字。初見テスト 5 回目の V119)
ROUNDING_HELP = "納品書と請求書の金額の端数 (四捨五入・切上・切捨)。空なら会社の既定 (今は {current}) に従います。"
TAX_HELP = "消費税の端数 (トータル・各行四捨五入・各行切捨)。空なら会社の既定 (今は {current}) に従います。"


def _active_on_edit_only(form):
    """使用中の欄 (外すと使用停止。N135) は編集の画面だけに出す。新規登録は使用中で作る (表の既定)。

    表の列は editable=False (CSV の見出しに出さない) なので、ModelForm は値を写さない。編集の初期値はここで写す。
    """
    if form.instance.pk is None:
        form.fields.pop("active", None)
    else:
        form.fields["active"].initial = form.instance.active


class ItemForm(ServiceModelForm):
    active = forms.BooleanField(label="使用中", required=False, help_text=ACTIVE_HELP)

    #: 土台 (入力の土台の get_form) が会社ごとの項目を足した後も、この欄を最後に置く。
    #: 使用中の行が会社ごとの項目を押し下げず、編集の並びが新規登録 + 最後に使用中になるように (初見テスト 5 回目の V35)。
    last_fields = ("active",)

    class Meta:
        model = Item
        fields = ["part_no", "name", "drawing_no", "drawing", "unit", "standard_price", "note"]
        # 自社品番の欄の下の説明 (初見テスト R5 の V87。C19-2 = a: 見出しは変えず、欄の下に説明)。
        # 意図: 欄の「自社品番」とゴールの「品番」が同じ物かを、社内の品番と取引先の品番の違いで答える。
        # 表の help_text には書かない (AlterField の migration が要るため)。「?」の「入れる物」の表も同じ字を読む。
        help_texts = {
            "part_no": "社内で部品を呼ぶ品番です (会社の中で同じ品番は 1 つだけ)。得意先や協力企業が別の品番で呼ぶときは、その対応が部品の詳細の「取引先」のタブに並び、「取引先を足す」でも足せます。",
        }

    def __init__(self, *args, hide_money=False, **kwargs):
        super().__init__(*args, **kwargs)
        _active_on_edit_only(self)
        if hide_money:
            # 金額の閲覧が無い人 (権限の判定の on) には標準単価の欄を出さない。土台は cleaned_data を渡し、
            # update_item は渡した鍵だけを直すので、標準単価は保存済みの値のまま。
            self.fields.pop("standard_price", None)


class PartnerForm(ServiceModelForm):
    active = forms.BooleanField(label="使用中", required=False, help_text=ACTIVE_HELP)

    #: 行の頭 (幅 640 以上の 2 列の左) から始める欄 (入力のカード core/_form_card.html が読む)。
    #: 締日 → 支払の月 → 支払日の順を崩さずに、「支払の月」と「支払日」を同じ行の左右に並べる (初見テスト 5 回目の V42)。
    row_start_fields = ("payment_month",)
    #: 会社ごとの項目を足した後も最後に置く欄 (部品の ItemForm とそろえる。V35)
    last_fields = ("active",)

    class Meta:
        model = Partner
        fields = [
            "code",
            "name",
            "short_name",
            "role",
            "billing_method",
            "closing_day",
            "payment_month",
            "payment_day",
            "rounding",
            "tax_mode",
            "note",
        ]
        help_texts = {
            "billing_method": "締日でまとめる = 締日ごとに 1 枚の請求書を作ります。都度 = 納品書ごとに請求書を作れます (現金払いなど)。",
            "closing_day": "1〜31。月末締めは 31",
            "payment_month": (
                "翌月末払いなら「翌月」と支払日「31」、翌月 25 日払いなら「翌月」と支払日「25」。"
                "「当日」は、締日でまとめるなら締日、都度なら納品の日に払う形です (現金払いなど。支払日は使いません)。"
            ),
            "payment_day": "1〜31。月末は 31",
            "rounding": "納品書と請求書の金額の端数 (四捨五入・切上・切捨)。空なら会社の既定に従います。",
            "tax_mode": "消費税の端数 (トータル・各行四捨五入・各行切捨)。空なら会社の既定に従います。",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _active_on_edit_only(self)
        # 請求の仕方の欄の無い送信 (欄を足す前の形) は、今の値のまま (新規は既定の締日でまとめる) として読む。
        # 画面の選ぶ欄は空を出さないので、画面から送るときは変わらない。
        key = self.add_prefix("billing_method")
        if self.is_bound and key not in self.data:
            data = self.data.copy()
            data[key] = self.instance.billing_method
            self.data = data
        # 端数の 2 欄の案内に会社の既定の今の値を出す (V119)。会社の行は読むだけ (無ければ表の既定の字)。
        # 「?」の説明書の「入れる物」の表は form を作らずに Meta.help_texts を読むので、今の値の無い字のまま出る。
        company = company_defaults()
        self.fields["rounding"].help_text = ROUNDING_HELP.format(current=company.get_rounding_display())
        self.fields["tax_mode"].help_text = TAX_HELP.format(current=company.get_tax_mode_display())


#: 工程の使用中の欄の下の案内 (N140)
PROCESS_ACTIVE_HELP = "外すと使用停止にします。指示書の発行の候補に出ず、部品の標準の工程の並びからも写しません。今までの指示書の工程はそのまま残ります。"


class ProcessForm(ServiceModelForm):
    """工程を入れる・直す。協力企業に出すか (is_outsourced) は欄に出さない (入れるときは偽、直すときは今の値のまま)。"""

    active = forms.BooleanField(label="使用中", required=False, help_text=PROCESS_ACTIVE_HELP)

    #: 会社ごとの項目を足した後も最後に置く欄 (部品の ItemForm とそろえる。V35)
    last_fields = ("active",)

    class Meta:
        model = Process
        fields = ["code", "name", "sort_order", "use_in_plan"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _active_on_edit_only(self)


# ---------------------------------------------------------------- 部品の取引先 (決め C19-1・C98-1)

#: 部品の取引先の 2 欄の下の説明 (追加と編集で同じ字)
PARTNER_ITEM_HELP = {
    "partner_part_no": "相手がこの部品を呼ぶ品番。分からなければ空のまま足せます。空の行は、受け入れと読み取りの品番のつなぎには使いません。",
    "partner_item_name": "相手がこの部品を呼ぶ品名 (なくてもよい)。",
}


class PartnerItemForm(ServiceModelForm):
    """部品の取引先の追加。部品の詳細からも取引先の詳細からも同じ入力で開く (開いた側の ?item= か ?partner= が初めの値)。

    欄の queryset は表の全部 (使用停止の部品と取引先の詳細から開いても、初めの値が選んだまま出て検査も通る)。
    字を打って探す候補は候補の口のまま使用中だけ。保存は土台が cleaned_data を data に渡す (services.add_partner_item)。
    """

    class Meta:
        model = PartnerItem
        fields = ["item", "partner", "partner_part_no", "partner_item_name"]
        labels = {"item": "部品", "partner": "取引先"}
        widgets = {"item": RemoteSelect("masters:item_select"), "partner": RemoteSelect("masters:partner_select")}
        help_texts = PARTNER_ITEM_HELP

    def validate_constraints(self):
        # 一意は services が見る (core/forms.py の ServiceModelForm.validate_unique と同じ訳)。
        # 訳: Django の ModelForm は validate_unique の後に validate_constraints も呼ぶので、止めないと、同じ取引先をもう一度空の品番で
        # 足す保存が services の緑の知らせの前に止まり、同じ取引先の同じ品番の 2 件目も services の字でなく Django の既定の字の赤になるため。
        return


class PartnerItemUpdateForm(ServiceModelForm):
    """部品の取引先の編集。直すのは取引先の品番と品名だけ (部品と取引先は読むだけの欄で出す。違うときは消して足し直す)。

    Django は form の外の欄 (部品と取引先) を含む表の制約を見ないので、validate_constraints の上書きは要らない。
    """

    item_text = forms.CharField(label="部品", required=False, disabled=True)
    partner_text = forms.CharField(label="取引先", required=False, disabled=True)

    #: 読むだけの 2 欄を頭に置く (ModelForm は表の欄を先に並べるため)
    field_order = ["item_text", "partner_text"]

    class Meta:
        model = PartnerItem
        fields = ["partner_part_no", "partner_item_name"]
        help_texts = PARTNER_ITEM_HELP

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk is not None:
            self.fields["item_text"].initial = f"{self.instance.item.part_no} {self.instance.item.name}"
            self.fields["partner_text"].initial = self.instance.partner.name

    def service_kwargs(self):
        # 読むだけの 2 欄は services に渡さない (update_partner_item の鍵は品番と品名だけ)。
        return {
            "data": {
                "partner_part_no": self.cleaned_data.get("partner_part_no", ""),
                "partner_item_name": self.cleaned_data.get("partner_item_name", ""),
            }
        }


class PartnerItemDeleteForm(forms.Form):
    """部品の取引先の削除の確かめ。欄は無い (土台が delete_partner_item(対象, user=…) を呼ぶ)。"""

    submit_label = "削除"
    danger = True  # 実行のボタンを削除の確かめの赤 (btn btn-error) にする (core/_form_card.html)

    def service_kwargs(self) -> dict:
        return {}


# ---------------------------------------------------------------- 単価の行 (単価表。決め PRICE-0〜3・C98-3)

#: 部品が見つからないときの欄の外の誤り (添付ファイルの付ける先が無いときと同じ形)
MESSAGE_PRICE_ITEM_NOT_FOUND = "単価を足す部品が見つかりません。部品の詳細の取引先のタブの「単価を足す」から開き直してください。"
#: 取引先の選ぶ欄の空の字 (空 = 共通)
COMMON_PRICE_LABEL = "共通 (どの取引先にも同じ単価)"


class _PartnerItemChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        partner = obj.partner.short_name or obj.partner.name
        return f"{partner} {obj.partner_part_no}" if obj.partner_part_no else f"{partner} (品番なし)"


class ItemPriceForm(ServiceModelForm):
    """単価の行の追加と編集。部品の詳細の取引先の行か共通の「単価を足す」から開き、その部品の取引先の中からだけ選ぶ (決め PRICE-2)。

    部品は足すときだけ隠した欄 (?item=<pk> の初めの値)。直すときは部品の欄を外す (部品は変えない)。
    単価と数量の下限は 0 より大きい (欄の下限。決め C98-3)。金額の閲覧が無い人 (hide_money) には単価の欄を出さない。
    """

    item_text = forms.CharField(label="部品", required=False, disabled=True)
    unit_price = forms.DecimalField(
        label="単価",
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        error_messages={"min_value": "単価は 0 より大きい数にしてください。"},
    )
    min_qty = forms.DecimalField(
        label="数量の下限",
        required=False,
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        error_messages={"min_value": "数量の下限は 0 より大きい数にしてください。数量によらないときは空にします。"},
        help_text="この数量から効く単価です。帯の終わりは、次の行の下限の 1 つ手前です。空なら数量によらない単価です。",
    )

    #: 読むだけの部品の欄を頭に置く (ModelForm は表の欄を先に並べるため)
    field_order = ["item_text"]

    class Meta:
        model = ItemPrice
        fields = [
            "item",
            "partner_item",
            "kind",
            "process",
            "min_qty",
            "unit_price",
            "price_basis",
            "start_date",
            "quote_no",
            "quote_date",
            "quote_expires",
            "note",
        ]
        field_classes = {"partner_item": _PartnerItemChoiceField}
        widgets = {
            "item": forms.HiddenInput,
            "start_date": IsoDateInput(),
            "quote_date": IsoDateInput(),
            "quote_expires": IsoDateInput(),
        }
        labels = {"partner_item": "取引先"}
        # 「?」の説明書の「入れる物」の表は form を作らずに base_fields を読むので、今の値に依らない字だけを置く。
        help_texts = {
            "partner_item": "空なら共通 (どの取引先にも同じ単価)。選べるのは、この部品の取引先のタブにある取引先だけです。",
            "kind": "売りは受注の単価、協力企業への発注は発注の単価です。",
            "process": "協力企業への発注の単価のときに、協力企業に出す工程を選びます。空なら工程によらない単価です。売りの単価では残しません。",
            "price_basis": "「部品の単位」は 1 つあたりの単価、「式」は数量を掛けない一式の金額です。",
            "start_date": (
                "空ならいつでも効きます。単価を改定するときは、この行を直さず、新しい適用開始日の行を足します。"
                "同じ取引先・売りか発注か・工程・数量の下限・適用開始日の行は 1 つだけです。"
            ),
            "quote_no": "見積書の番号。見積書のファイルは、取引先の行の「見積書を付ける」で付けます。",
            "quote_expires": "空なら期限なし。過ぎると、この行に「期限切れ」の印が付きます。",
        }

    def __init__(self, *args, hide_money=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.price_item = self._price_item()
        if self.instance.pk is not None:
            # 直すときは部品の欄を外す。ModelForm は検査のときに送られた値を instance に写すので、隠した欄を残すと、
            # 送られた別の部品の番号で単価の行の部品が書き換わるため。
            self.fields.pop("item", None)
        if self.price_item is not None:
            self.fields["item_text"].initial = f"{self.price_item.part_no} {self.price_item.name}"
            self.fields["partner_item"].queryset = self.price_item.partner_items.select_related("partner").order_by("pk")
        else:
            self.fields["partner_item"].queryset = PartnerItem.objects.none()
        self.fields["partner_item"].empty_label = COMMON_PRICE_LABEL
        keep = self.instance.process_id if self.instance.pk is not None else None
        self.fields["process"].queryset = _active_outsourced_processes(keep)
        self.fields["process"].required = False
        if hide_money:
            # 金額の閲覧が無い人 (判定の on) には単価の欄を出さない (部品の編集の標準単価と同じ形)。保存は金額の門が断る。
            self.fields.pop("unit_price", None)

    def _price_item(self):
        """単価の行の部品。直すときは今の行の部品、足すときは送られた値か初めの値 (数の字のときだけ引く)。無ければ None。"""
        if self.instance.pk is not None:
            return self.instance.item
        source = self.data if self.is_bound else self.initial
        raw = source.get(self.add_prefix("item") if self.is_bound else "item")
        if isinstance(raw, Item):
            return raw
        if raw is None or not str(raw).isdecimal():
            return None
        return Item.objects.filter(pk=int(raw)).first()

    def clean(self):
        cleaned = super().clean()
        if self.price_item is None:
            raise forms.ValidationError(MESSAGE_PRICE_ITEM_NOT_FOUND)
        return cleaned

    def validate_constraints(self):
        # 一意は services が見る (PartnerItemForm と同じ)。訳: 止めないと、同じ条件の行の保存が services の字 (本人の決めの字) でなく
        # Django の既定の字「制約 “masters_itemprice_same_band_unique” に違反しています。」の赤で止まるため。
        return

    def service_kwargs(self):
        data = {name: value for name, value in self.cleaned_data.items() if name != "item_text"}
        return {"data": data}


def _active_outsourced_processes(keep):
    """単価の行の「協力企業の工程」の選択肢: 使用中で協力企業に出す工程 (keep は直す行の今の工程。使用停止でも残す)。"""
    from .services import active_processes

    return active_processes(keep=keep).filter(is_outsourced=True)


class ItemPriceDeleteForm(forms.Form):
    """単価の行の削除の確かめ。欄は無い (土台が delete_item_price(対象, user=…) を呼ぶ)。"""

    submit_label = "削除"
    danger = True  # 実行のボタンを削除の確かめの赤 (btn btn-error) にする (core/_form_card.html)

    def service_kwargs(self) -> dict:
        return {}


# ---------------------------------------------------------------- CSV 取り込み (05 の 8 節)

#: 支払日の字の月の部分 (画面の字, 値)。長い字から合わせる (「翌々月」を「翌月」と読み違えない)。
_MONTH_WORDS = tuple(
    sorted(((str(label), value) for value, label in Partner.PAYMENT_MONTH_CHOICES), key=lambda pair: -len(pair[0]))
)
_DAY_TEXT = re.compile(r"(\d+)\s*日?")


def _text_of(raw):
    return "" if raw is None else str(raw).strip()


def _day_digits(text):
    """日の字 (「20」「20 日」「20日」「月末」) を数の字にする。読めなければ None。"""
    if text == "月末":
        return str(MONTH_END_DAY)
    match = _DAY_TEXT.fullmatch(text)
    return match.group(1) if match else None


def read_closing_day(field, raw, present):
    """締日の字を読む。空は空、「20」「20 日」は 20、「31」「月末」は 31。1〜31 の外れは表の検査が誤りにする。

    一覧の CSV の書き出しは締日を画面の字 (Partner.get_closing_day_display) で出すので、それを読み戻す。
    """
    text = _text_of(raw)
    digits = _day_digits(text) if text else ""
    if digits is None:
        raise ValueError(f"{field.verbose_name}「{text}」の形が違います。「20」「20 日」「月末」のように書いてください。")
    return {field.name: csvio.to_value(field, digits)}


def read_payment_day(field, raw, present):
    """支払日の字を読む。present は CSV にある列の名前の集合。

    支払の月の列が無いとき (一覧の書き出しの形。Partner.get_payment_day_display の字) は、
    「翌月 25 日」「翌月末」「当月末」「25 日」「月末」「翌月」から支払の月と支払日の 2 つを読む。
    支払の月の列があるとき (examples の形) は、日だけ (「25」「25 日」「31」「月末」) を読み、月の字があれば誤り。
    """
    text = _text_of(raw)
    if "payment_month" in present:
        if any(word in text for word, _value in _MONTH_WORDS):
            raise ValueError(
                f"{field.verbose_name}「{text}」に月の字があります。"
                "支払の月の列があるときは、支払日には日だけ (「25」「月末」) を書いてください。"
            )
        digits = _day_digits(text) if text else ""
        if digits is None:
            raise ValueError(f"{field.verbose_name}「{text}」の形が違います。「25」「25 日」「月末」のように書いてください。")
        return {field.name: csvio.to_value(field, digits)}

    month, rest = "", text
    for word, value in _MONTH_WORDS:
        if text.startswith(word):
            month, rest = value, text[len(word) :].strip()
            break
    if not rest:
        digits = ""
    elif month and rest == "末":
        digits = str(MONTH_END_DAY)
    else:
        digits = _day_digits(rest)
    if digits is None:
        raise ValueError(
            f"{field.verbose_name}「{text}」の形が違います。「翌月 25 日」「翌月末」「25」のように書いてください。"
        )
    return {"payment_month": month, field.name: csvio.to_value(field, digits)}


class CsvImportForm(forms.Form):
    """CSV 取り込みの form の共通の親。子は下の属性を決める。

    clean() がファイルを読み、行ごとに字を値に直して確かめる。誤りは (行の番号, 欄 = 見出しの字, 理由) で
    import_errors に集め、1 件でもあれば form を通さない (1 件も入れない)。通れば cleaned_data["rows"] に
    services.import_* に渡す行を置き、import_counts (入れる・更新する・飛ばした の数) を置く。表には書かない。
    """

    submit_label = "取り込む"

    #: 取り込む表 (子が決める)
    model = None
    #: 同じ行かどうかを決める鍵の列の名前 (自社品番 = part_no・取引先コード = code)
    key_name = ""
    #: 無ければ誤りにする見出し
    required_headers = ()
    #: テンプレートが「一覧に戻る」と「見本の CSV をダウンロード」の link を作る URL の名前
    list_url_name = ""
    sample_url_name = ""
    #: 列の名前 → 字を読む関数 (field, 字, CSV にある列の名前の集合) → {列の名前: 値}。無い列は csvio.to_value。
    cell_readers = {}
    #: 読まない見出し (見出しの対応に入れず、値も読まない)。子が足す (一覧の読むだけの列)
    skip_headers = csvio.SKIP_HEADERS
    #: 読まない列の名前 (値があっても入れず、字の誤りも見ない)。子の __init__ が決める (金額の閲覧の無い人の標準単価)
    unread_fields = ()

    file = forms.FileField(label="CSV のファイル")
    encoding = forms.ChoiceField(
        label="文字コード",
        choices=csvio.ENCODINGS,
        initial=csvio.DEFAULT_ENCODING,
        help_text=(
            "Excel で「CSV (コンマ区切り)」で保存したファイルは Shift_JIS、「CSV UTF-8」で保存したファイルと"
            "一覧の「CSV」のボタンで落としたファイルは UTF-8 を選びます。"
        ),
        error_messages={"required": "文字コードを選んでください。"},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.import_errors = []
        self.import_counts = None
        self.header_hint = "・".join(csvio.header_labels(self.model))
        #: CSV にあったが読まなかった見出し (知らせの 1 文に使う)
        self.unread_headers = []

    def clean(self):
        cleaned = super().clean()
        upload = cleaned.get("file")
        encoding = cleaned.get("encoding")
        if upload is None or not encoding:
            return cleaned
        errors = []
        rows, skipped = [], 0
        upload.seek(0)
        try:
            text = csvio.decode(upload.read(), encoding)
        except ValueError as exc:
            errors.append((None, "文字コード", str(exc)))
        else:
            try:
                _headers, columns, ext_columns, table = csvio.read_table(
                    text, self.model, required=self.required_headers, skip=self.skip_headers
                )
            except ValueError as exc:
                errors.append((None, "", str(exc)))
            else:
                # 読まない列は見出しの対応から外す (前の見出し FORMER_HEADERS も同じ列として外す)。_read_rows は対応に無い見出しの字を読まない。
                self.unread_headers = [header for header, field in columns.items() if field.name in self.unread_fields]
                columns = {header: field for header, field in columns.items() if field.name not in self.unread_fields}
                rows, skipped = self._read_rows(table, columns, ext_columns, errors)
        if errors:
            self.import_errors = errors
            raise forms.ValidationError(
                f"CSV に誤りが {len(errors)} 件あります。1 件も入れていません。"
                "下の表を見て直してから、もう一度選んでください。"
            )
        cleaned["rows"] = rows
        self.import_counts = self._counts(rows, skipped)
        return cleaned

    def _read_rows(self, table, columns, ext_columns, errors):
        """行ごとに字を値に直す。誤りは errors に足す。返す物は (行の並び, 飛ばした空の行の数)。"""
        present = {field.name for field in columns.values()}
        key_header = next((h for h, field in columns.items() if field.name == self.key_name), "")
        first_line = {}
        rows, skipped = [], 0
        for line, cells in table:
            if csvio.is_blank_row(cells.values()):
                skipped += 1
                continue
            data, ext = {}, {}
            for header, raw in cells.items():
                try:
                    if header in columns:
                        field = columns[header]
                        reader = self.cell_readers.get(field.name)
                        values = reader(field, raw, present) if reader else {field.name: csvio.to_value(field, raw)}
                        data = {**data, **values}
                    elif header in ext_columns:
                        column = ext_columns[header]
                        ext[column.name] = csvio.to_ext_value(column, raw)
                except ValueError as exc:
                    errors.append((line, header, str(exc)))
            key = data.get(self.key_name)
            if key and key in first_line:
                errors.append((line, key_header, f"{key} は {first_line[key]} 行目にもあります。1 行にまとめてください。"))
            elif key:
                first_line[key] = line
            rows.append({"line": line, "data": data, "ext": ext or None})
        return rows, skipped

    def _counts(self, rows, skipped):
        """入れる・更新する・飛ばした の数 (読むだけ)。"""
        existing = set(self.model.objects.values_list(self.key_name, flat=True))
        updating = sum(1 for row in rows if row["data"][self.key_name] in existing)
        return {"入れる": len(rows) - updating, "更新する": updating, "飛ばした": skipped}

    def service_kwargs(self):
        return {"rows": self.cleaned_data["rows"]}


class ItemCsvImportForm(CsvImportForm):
    model = Item
    key_name = "part_no"
    required_headers = ("自社品番", "品名")
    list_url_name = "masters:item_list"
    sample_url_name = "masters:item_import_sample"

    def __init__(self, *args, hide_money=False, **kwargs):
        super().__init__(*args, **kwargs)
        if hide_money:
            # 金額の閲覧が無い人 (権限の判定の on) は標準単価の列を読まない (値があっても変えない。精査 A2 の B3。
            # 部品の編集の画面の ItemForm が欄を外すのと同じ考え)。土台は __init__ に hide_money の引数がある form にだけ渡す。
            self.unread_fields = ("standard_price",)

    def service_kwargs(self):
        kwargs = super().service_kwargs()
        if self.unread_headers:
            kwargs["price_skipped"] = True
        return kwargs


class PartnerCsvImportForm(CsvImportForm):
    model = Partner
    key_name = "code"
    required_headers = ("取引先コード", "名前", "取引先の種類")
    list_url_name = "masters:partner_list"
    sample_url_name = "masters:partner_import_sample"
    cell_readers = {"closing_day": read_closing_day, "payment_day": read_payment_day}
    #: 取引先の一覧の読むだけの列「注文の入口」(一覧の CSV の書き出しに出る。core の dashboard.annotate_order_inlet の列) は読まない。
    #: 一覧で落とした CSV をそのまま入れ直せるように。
    skip_headers = (*csvio.SKIP_HEADERS, "注文の入口")


# ---------------------------------------------------------------- 図面を版に戻す (第 3 波)


class _VersionChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        saved = timezone.localtime(obj.created_at) if timezone.is_aware(obj.created_at) else obj.created_at
        text = f"版 {obj.version_no} ({saved:%Y-%m-%d %H:%M})"
        return f"{text} {obj.note}" if obj.note else text


class DrawingRestoreForm(forms.Form):
    """図面を版に戻す確認の form。選べる版はこの部品の版だけ (新しい順)。

    土台は instance (部品) を渡し、services.restore_drawing_version(item, user=…, version=…, note=…) を呼ぶ。
    """

    version = _VersionChoiceField(label="戻す版", queryset=ItemDrawingVersion.objects.none(), empty_label=None)
    note = forms.CharField(
        label="備考",
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="戻す前の今の図面を版として残すときの備考になります。",
    )

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        if instance is not None:
            self.fields["version"].queryset = instance.drawing_versions.order_by("-version_no")

    def service_kwargs(self):
        return {"version": self.cleaned_data["version"], "note": self.cleaned_data["note"]}


# ---------------------------------------------------------------- 会社の休日

class HolidayForm(ServiceModelForm):
    """休日を 1 日足す。日付の一意は services が見る (ServiceModelForm は一意を見ない)。登録した人は欄に出さない。"""

    class Meta:
        model = Holiday
        fields = ["date", "name", "kind"]
        widgets = {"date": IsoDateInput()}
        # required の文は、空の欄の下に出る。名前の文は services.create_holiday と同じ字 (文に % を書かない)。
        error_messages = {
            "date": {"required": "休日の日付を入れてください。"},
            "name": {"required": "休日の名前を入れてください。"},
            "kind": {"required": "種類 (祝日か会社の休み) を選んでください。"},
        }


class HolidayDeleteForm(forms.Form):
    """休日の削除の確認。欄は無い (土台が delete_holiday(対象, user=…) を呼ぶ)。"""

    def service_kwargs(self) -> dict:
        return {}
