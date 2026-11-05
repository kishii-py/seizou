"""入力の form の土台。

ServiceModelForm: ModelForm だが、表に関わる判断 (一意など) は form で見ず services に任せる (R13 の線)。
form が見るのは欄の型と必須だけ。services の決まりの違反は日本語の文の ValueError か RuleError で返り、
画面の土台が赤の知らせにする。
整数の欄は、表の検査 (MinValueValidator・MaxValueValidator) から下限と上限を取り、欄の min・max と
form の検査にそろえる (例: 締日は 1〜31。誤りの文は「1 以上」「31 以下」)。
SettingForm: 選べる設定 1 つの入力 (設定の画面の「編集」)。値を確かめて整えるのは core の services。
SubmitLabelMixin: 保存のボタンの字に、押した結果の対象と数と金額を書く口 (N138)。字を組むのは部品の form、
入力のたびに埋め直すのは static の js/seizou-submit-label.js (同じ型を同じ決まりで埋める)。
line_totals: 明細の合計の枠 (core/_line_totals.html) と保存のボタンの字の元 (N149)。
"""

import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import forms
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator

from seizou.core import conf
from seizou.core.models import Company
from seizou.core.templatetags.seizou_tags import comma
from seizou.core.widgets import normalize_number

#: 選ぶ欄の空の選択肢の字 (core の apps.ready が Django の既定の字をこれに置き換える)
EMPTY_LABEL = "選んでください"


def model_limits(model_field) -> tuple:
    """表の項目の検査から (下限, 上限) を取る。無ければ None。"""
    low = high = None
    for validator in getattr(model_field, "validators", ()):
        if isinstance(validator, MinValueValidator):
            low = validator.limit_value if low is None else max(low, validator.limit_value)
        elif isinstance(validator, MaxValueValidator):
            high = validator.limit_value if high is None else min(high, validator.limit_value)
    return low, high


def apply_integer_limits(field, low, high) -> None:
    """整数の欄の下限と上限を、form の検査と欄の min・max の両方にそろえる (前の下限と上限の検査は外す)。"""
    if low is None and high is None:
        return
    field.validators = [v for v in field.validators if not isinstance(v, (MinValueValidator, MaxValueValidator))]
    if low is not None:
        field.min_value = low
        field.validators.append(MinValueValidator(low))
        field.widget.attrs["min"] = low
    if high is not None:
        field.max_value = high
        field.validators.append(MaxValueValidator(high))
        field.widget.attrs["max"] = high


class ServiceModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        plain_number_widgets(self)
        model = self._meta.model
        for name, field in self.fields.items():
            if not isinstance(field, forms.IntegerField):
                continue
            try:
                model_field = model._meta.get_field(name)
            except Exception:  # noqa: BLE001 - 表に無い欄 (form だけの欄) は見ない
                continue
            apply_integer_limits(field, *model_limits(model_field))

    def validate_unique(self):
        # 一意は services が見る (同じ文を 2 か所に持たない)。
        return


class PlainNumberInput(forms.NumberInput):
    """数の欄。初期値の Decimal が整数なら小数の 0 を出さない (「10.00」でなく「10」。動画の確かめの所見 11-05)。

    使う人が打った字 (bound の値) はそのまま。整数でない Decimal は Django のまま (12.50 は 12.50)。
    """

    def format_value(self, value):
        if isinstance(value, Decimal) and value == value.to_integral_value():
            return str(int(value))
        return super().format_value(value)


def plain_number_widgets(form: forms.BaseForm) -> None:
    """form の DecimalField のうち、Django が付けた素の NumberInput を PlainNumberInput に替える (attrs は引き継ぐ)。"""
    for field in form.fields.values():
        if isinstance(field, forms.DecimalField) and type(field.widget) is forms.NumberInput:
            field.widget = PlainNumberInput(attrs=field.widget.attrs)


#: 複数の checkbox の 1 つずつの見た目 (daisyUI)
CHECKBOX_CSS = "checkbox checkbox-sm"


class Checkboxes(forms.CheckboxSelectMultiple):
    """複数の checkbox。外の枠 (div) には checkbox の見た目を付けない (Django の雛形は外の枠にも class を付けるため)。"""

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["attrs"].pop("class", None)
        return context


class SettingForm(forms.Form):
    """選べる設定 key 1 つの入力。欄は鍵の種類で決まる。

    選べる値 (conf.CHOICES) は select、真偽は checkbox、権限の判定 (access_control) は 3 つの select、
    使わない注文の項目 (hidden_fields) は複数の checkbox、ほか (model の名前) は字。
    欄の初期値は今の値 (conf.setting)。値を確かめて整えるのは services の validate_setting (form は欄の型だけ)。
    選択肢の字は日本語だけ (設定の画面の値の字と同じ)。送る値 (option の value) は英字の値のまま。
    """

    #: hidden_fields の欄の名前。鍵の名前のままだと、テンプレートの form.hidden_fields が隠しの欄の並びでなく
    #: この欄を指し、checkbox が 2 度出る (2 度目は見えない所に残り、外した check も送られる) ため別の名前にする
    HIDDEN_FIELDS_FIELD = "names"

    def __init__(self, *args, key, **kwargs):
        super().__init__(*args, **kwargs)
        # 見出しの日本語は設定の画面の表から読む。module の頭で読むと settings_screen → forms の輪になるので、ここで読む
        from seizou.core.settings_screen import SETTING_LABELS, SUB_LABELS, hideable_fields

        self.key = key
        label, words = SETTING_LABELS.get(key, (key, {}))
        current = conf.setting(key)
        if key in conf.CHOICES:
            self.fields[key] = forms.ChoiceField(
                label=label,
                choices=[(v, words.get(v, v)) for v in conf.CHOICES[key]],
                initial=current,
            )
        elif isinstance(conf.DEFAULTS[key], bool):
            self.fields[key] = forms.BooleanField(
                label=f"{label}: {words[True]}",
                required=False,
                initial=current,
                help_text=f"check を外すと「{words[False]}」。",
            )
        elif key == "access_control":
            choices = [(m, words.get(m, m)) for m in conf.ACCESS_MODES]
            for kind in conf.DEFAULTS["access_control"]:
                self.fields[kind] = forms.ChoiceField(
                    label=SUB_LABELS.get(kind, kind), choices=choices, initial=conf.access_control(kind)
                )
        elif key == "hidden_fields":
            self.fields[self.HIDDEN_FIELDS_FIELD] = forms.MultipleChoiceField(
                label=label,
                required=False,
                choices=hideable_fields(),
                widget=Checkboxes(attrs={"class": CHECKBOX_CSS}),
                initial=list(current or ()),
                help_text="check を入れた項目を受注の入力から外す。決まりが見る項目 (得意先・部品・数量・単価・受注日) は隠せない。",
            )
        else:
            self.fields[key] = forms.CharField(label=label, max_length=100, initial=current)

    def value(self):
        """services に渡す値 (真偽はそのまま、権限の判定は 3 つの欄の辞書、使わない項目は並び、字はそのまま)。"""
        data = self.cleaned_data
        if self.key == "access_control":
            return {kind: data[kind] for kind in conf.DEFAULTS["access_control"]}
        if self.key == "hidden_fields":
            return list(data[self.HIDDEN_FIELDS_FIELD])
        return data[self.key]

    def service_kwargs(self):
        return {"key": self.key, "value": self.value()}


class CompanyForm(ServiceModelForm):
    """会社の情報 (住所・電話・振込先・ロゴ) の入力。値を確かめて保存するのは services の update_company (form は欄の型と字数だけ)。"""

    # ロゴは絵だけ (Pillow で絵かを確かめる)。SVG と HTML は上げたファイルの口がダウンロードにして <img> に出ないので受けない
    logo = forms.ImageField(
        label="ロゴ",
        required=False,
        validators=[FileExtensionValidator(["png", "jpg", "jpeg"])],
        help_text="PNG か JPEG の絵。納品書と請求書の会社の欄の頭に出る。消すときは「クリア」に check を入れて保存。",
    )

    class Meta:
        model = Company
        fields = ("address", "phone", "bank_account", "logo")
        help_texts = {
            "address": "納品書と請求書の会社の欄に出す。郵便番号から 1 行で (例: 〒000-0000 見本県架空市見本町 1-2-3)。",
            "phone": "例: 00-0000-0000",
            "bank_account": "請求書だけに出す。銀行・支店・口座の種類・番号・名義を 1 行で (例: 作り物銀行 見本支店 普通 0000000 名義 ミホン)。",
        }


#: 保存のボタンの字の型の置き場 (行の数・数量の合計・金額の合計。js/seizou-submit-label.js と同じ 3 つ)
SUBMIT_LABEL_SLOT = re.compile(r"\{(rows|qty|amount)\}")


def label_decimal(value):
    """ボタンの字に使う数の値。全角と桁の区切りをそろえて Decimal にする。空・数でない字・bool は None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(normalize_number(str(value)))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def label_number(value):
    """ボタンの字に書く数。小数 2 桁で四捨五入し、3 桁ごとに区切る (45000.00 → 45,000)。数でなければ None。"""
    number = label_decimal(value)
    if number is None:
        return None
    try:
        return comma(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except InvalidOperation:  # 桁が多すぎる数 (例 1e999999)
        return None


def line_unit(line) -> str:
    """明細の単位 (明細の単位が空なら部品の単位)。どちらも空なら空の字。"""
    return getattr(line, "unit", "") or getattr(getattr(line, "item", None), "unit", "") or ""


def line_totals(pairs, *, show_amount=True) -> dict:
    """明細の合計の枠 (core/_line_totals.html) と保存のボタンの字の元 (N149。案 5-A)。

    pairs は (数量, 単価) の組の並び (単価は None でよい)。form は数えない行 (空・消す印) を外してから渡し、
    送って止まった形で数量の通らなかった行があれば None を渡す (「分からない」)。
    返す物: rows = 行の数 (int。分からなければ None)、qty = 数量の合計の字 (label_number) か None、
    amount = 金額の合計の字か None (show_amount が偽・単価の空か数でない行がある・分からない)、show_amount = 金額の組を描くか。
    丸めは label_number (小数 2 桁の四捨五入 = 保存の前の見込み。保存の金額は services が取引先の端数の扱いで 1 円に丸める)。
    JS (static/js/seizou-submit-label.js の values) と同じ数え方。
    """
    unknown = {"rows": None, "qty": None, "amount": None, "show_amount": show_amount}
    if pairs is None:
        return unknown
    rows, qty, amount, priced = 0, Decimal("0"), Decimal("0"), True
    for raw_qty, raw_price in pairs:
        count = label_decimal(raw_qty)
        if count is None:
            return unknown
        rows += 1
        qty += count
        price = label_decimal(raw_price)
        if price is None:
            priced = False
        else:
            amount += count * price
    return {
        "rows": rows,
        "qty": label_number(qty),
        "amount": label_number(amount) if show_amount and priced else None,
        "show_amount": show_amount,
    }


class SubmitLabelMixin:
    """保存のボタンの字に、押した結果の対象と数と金額を書く口 (N138。決め 11-04 23:0x「ボタンに金額を入れて、押す前の迷いを減らす」)。

    - submit_label_plain: 数の分からないときの字 (例「出荷する」)。入力のカードの「必須」の案内もこの字を引く。
    - submit_label_templates(): 字の型の並び。{rows} (行の数)・{qty} (数量の合計)・{amount} (金額の合計) の置き場を、
      分かる値で全部埋められる最初の型を使う。どれも埋められなければ submit_label_plain。
    - submit_label_values(): 型を埋める値の字 (分かる物だけ)。開いた form は初期値、送った form は通った値から組む。
    - submit_label: 描くときのボタンの字。submit_label_template: ボタンの data-submit-label に置く型の並び (JSON)。
      js/seizou-submit-label.js が入力のたびに同じ型を埋め直す (JS が無ければ描いたときの字のまま)。型が無ければ空。
    """

    submit_label_plain = "保存"

    def submit_label_templates(self) -> list:
        return []

    def submit_label_values(self) -> dict:
        return {}

    def submit_value(self, name):
        """欄の値。送った form は通った値 (cleaned_data) だけ、開いた form は初期値 (GET の字を含む)。"""
        if self.is_bound:
            return getattr(self, "cleaned_data", {}).get(name)
        value = self.initial.get(name)
        if value is None and name in self.fields:
            value = self.fields[name].initial
        return value() if callable(value) else value

    @property
    def submit_label(self) -> str:
        values = {key: value for key, value in self.submit_label_values().items() if value}
        for template in self.submit_label_templates():
            if all(name in values for name in SUBMIT_LABEL_SLOT.findall(template)):
                return SUBMIT_LABEL_SLOT.sub(lambda match: values[match.group(1)], template)
        return self.submit_label_plain

    @property
    def submit_label_template(self) -> str:
        templates = self.submit_label_templates()
        return json.dumps(templates, ensure_ascii=False) if templates else ""
