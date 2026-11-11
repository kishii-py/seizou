"""入力の form の土台。

ServiceModelForm: ModelForm だが、表に関わる判断 (一意など) は form で見ず services に任せる (R13 の線)。
form が見るのは欄の型と必須だけ。services の決まりの違反は日本語の文の ValueError か RuleError で返り、
画面の土台が赤の知らせにする。
整数の欄は、表の検査 (MinValueValidator・MaxValueValidator) から下限と上限を取り、欄の min・max と
form の検査にそろえる (例: 締日は 1〜31。誤りの文は「1 以上」「31 以下」)。
SettingForm: 選べる設定 1 つの入力 (設定の画面の「編集」)。値を確かめて整えるのは core の services。
SubmitLabelMixin: 保存のボタンの字に、押した結果の対象と数と金額を書く口 (開発の記録 docs/nights/20261104/N138.md)。字を組むのは部品の form、
入力のたびに埋め直すのは static の js/seizou-submit-label.js (同じ型を同じ決まりで埋める)。
line_totals: 明細の合計の枠 (core/_line_totals.html) と保存のボタンの字の元 (開発の記録 docs/nights/20261105/N149.md)。
"""

import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import forms
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator

from seizou.core import conf, display, services
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
#: 口 (columns) を開けた複数の checkbox の外の枠の格子 (幅 640 以上で 2 列、1280 以上で 3 列。行の間は 1 列のときのまま)
CHECKBOX_COLUMNS_CSS = "grid gap-x-6 sm:grid-cols-2 xl:grid-cols-3"
#: 選択肢に群がある複数の checkbox の外の枠 (群ごとの枠を幅 640 以上で 2 列に並べる。間は入力のカードの格子と同じ)
CHECKBOX_GROUPS_CSS = "grid gap-x-6 gap-y-4 sm:grid-cols-2"


class Checkboxes(forms.CheckboxSelectMultiple):
    """複数の checkbox。外の枠 (div) には checkbox の見た目を付けない (Django の雛形は外の枠にも class を付けるため)。

    選択肢に群があるときは、外の枠に CHECKBOX_GROUPS_CSS を付け、群ごとの枠を幅の広い画面で横に並べる (群が先。作者の決め)。
    群が無く columns=True の欄だけ、外の枠に格子 (CHECKBOX_COLUMNS_CSS) を付け、幅の広い画面で 2〜3 列に並べる (作者の決め)。
    どちらでもない欄は 1 列 (外の枠に class を付けない)。
    """

    def __init__(self, attrs=None, choices=(), columns=False):
        super().__init__(attrs, choices)
        self.columns = columns

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["attrs"].pop("class", None)
        if any(group for group, _options, _index in context["widget"]["optgroups"]):
            context["widget"]["attrs"]["class"] = CHECKBOX_GROUPS_CSS
        elif self.columns:
            context["widget"]["attrs"]["class"] = CHECKBOX_COLUMNS_CSS
        return context


#: 丸の選択の 1 つずつの丸の見た目 (daisyUI。表示の設定のカードの丸と同じ)
RADIO_CSS = "radio"


class Radios(forms.RadioSelect):
    """丸の選択。選択肢ごとに、選ぶと何が起きるかの 1 文 (notes = {送る値: 文}) を名前の下に小さな字で出す。

    文の無い値は名前だけ。1 つの選択肢は core/_radio_option.html で描く (Django の radio_option の代わり)。
    外の枠 (div) には丸の見た目を付けない (Checkboxes と同じ訳)。Django の雛形 (multiple_input.html) は
    widget の class を外の枠 <div id="id_<鍵>"> にも付けるので、付けたままだと外の枠が大きな丸になる。
    class を渡さないと土台の get_form (core/views/ の widget_css) が字の欄の見た目を付けるので、
    RADIO_CSS を渡したうえで外の枠からだけ外す。1 つずつの丸には create_option が class を写す (option_inherits_attrs)。
    """

    option_template_name = "core/_radio_option.html"

    def __init__(self, attrs=None, choices=(), notes=None):
        super().__init__(attrs, choices)
        self.notes = dict(notes or {})

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        option["note"] = self.notes.get(str(value), "")
        return option

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["attrs"].pop("class", None)
        return context


#: 設定の変更の画面の丸の選択で、選べる値の名前の下に出す、選ぶと何が起きるかの 1 文 ({鍵: {送る値: 文}})。
#: 字は RULES.md のポカよけの節の段落 (知らせる・止める) と docs/settings.md の「選べる設定」の表から写し、新しい言葉を作らない。
#: 写せる字の無い値は持たない (名前だけ出す)。知らせの出る場所は書かない (作者の決めで変わるため)。
#: 真偽の送る値は "true" / "false"。ポカよけの 3 つの鍵は同じ 3 つの文を使う。
#: 設定の画面の SETTING_LABELS には入れない (値の字は設定の画面の表の「今の値」にも出るので、長い文を入れると表が崩れる)。
_CHECK_MODE_NOTES = {
    "warn": "黄色の知らせと「このまま保存」のボタンが出て、もう一度押すと保存される。",
    "stop": "赤の知らせが出て、保存されない。",
    "off": "知らせを出さずに保存する。",
}
CHOICE_NOTES = {
    "over_shipment": _CHECK_MODE_NOTES,
    "over_workorder": _CHECK_MODE_NOTES,
    "over_payment": _CHECK_MODE_NOTES,
    "progress_staff": _CHECK_MODE_NOTES,
    "billed_shipment": _CHECK_MODE_NOTES,
    "order_form": {"single": "画面が短く、番号が部品と 1 対 1 になる。"},
    "production_plan": {"false": "メニューに「生産計画」が出ない。"},
}


class SettingForm(forms.Form):
    """選べる設定 key 1 つの入力。欄は鍵の種類で決まる。

    選べる値 (conf.CHOICES) と真偽は丸の選択 (Radios。名前の下に CHOICE_NOTES の 1 文)、権限の判定 (access_control) は 3 つの select、
    メールの送り方 (mail) は 7 つの欄 (暗号化の方式は丸の選択。欄の名前は頭に mail_ を付ける)、
    使わない注文の項目 (hidden_fields) は複数の checkbox、ほか (model の名前・主色・業種) は字。
    主色と業種は空で既定に戻せるので任意 (OPTIONAL_KEYS)。字の欄の注記 (help_text) は、設定の画面の表で名前の下に出す
    2 行目と同じ字 (settings_screen.setting_note。「# と 16 進の 6 桁」は値を打つ所で要るため。作者の決め)。
    欄の初期値は今の値 (conf.setting)。値を確かめて整えるのは services の validate_setting (form は欄の型だけ)。
    選択肢の字は日本語だけ (設定の画面の値の字と同じ)。送る値 (丸と option の value) は英字の値のまま (真偽は "true" / "false" で送り、form が真偽に直す)。
    """

    #: hidden_fields の欄の名前。鍵の名前のままだと、テンプレートの form.hidden_fields が隠しの欄の並びでなく
    #: この欄を指し、checkbox が 2 度出る (2 度目は見えない所に残り、外した check も送られる) ため別の名前にする
    HIDDEN_FIELDS_FIELD = "names"

    #: 空で保存すると既定に戻る字の設定 (主色 = 土台の色・業種 = seizou)。services の validate_setting が空を通す鍵と同じ
    #: (core/services.py の brand_color と industry の枝)。欄は「任意」にする
    OPTIONAL_KEYS = ("brand_color", "industry")

    def __init__(self, *args, key, **kwargs):
        super().__init__(*args, **kwargs)
        # 見出しの日本語は設定の画面の表から読む。module の頭で読むと settings_screen → forms の輪になるので、ここで読む
        from seizou.core.settings_screen import MAIL_SECURITY_LABELS, SETTING_LABELS, SUB_LABELS, hideable_fields, setting_note

        self.key = key
        label, words = SETTING_LABELS.get(key, (key, {}))
        current = conf.setting(key)
        if key in conf.CHOICES:
            self.fields[key] = forms.ChoiceField(
                label=label,
                choices=[(v, words.get(v, v)) for v in conf.CHOICES[key]],
                initial=current,
                widget=Radios(attrs={"class": RADIO_CSS}, notes=CHOICE_NOTES.get(key, {})),
            )
        elif isinstance(conf.DEFAULTS[key], bool):
            # 初期値は字で渡す (真偽のまま渡すと str(True) = "True" になり、送る値の "true" と合わず、どの丸も選ばれない)
            self.fields[key] = forms.TypedChoiceField(
                label=label,
                choices=[("true", words[True]), ("false", words[False])],
                coerce=lambda text: text == "true",
                initial="true" if current else "false",
                widget=Radios(attrs={"class": RADIO_CSS}, notes=CHOICE_NOTES.get(key, {})),
            )
        elif key == "access_control":
            choices = [(m, words.get(m, m)) for m in conf.ACCESS_MODES]
            for kind in conf.DEFAULTS["access_control"]:
                self.fields[kind] = forms.ChoiceField(
                    label=SUB_LABELS.get(kind, kind), choices=choices, initial=conf.access_control(kind)
                )
        elif key == "mail":
            # 欄の名前は頭に mail_ を付ける (土台が渡す user などの名前と字を重ねないため)。初期値は今の送り方。
            # 丸の注 (notes) は Radios に直に渡す (CHOICE_NOTES は CHOICES と真偽の鍵だけの表)
            # サーバー・ポート・暗号化の方式は .env だけで決める (conf.MAIL_ENV)。画面では読むだけの欄 (disabled) にし、送られた値は使わない
            values = conf.mail_settings()
            self.fields["mail_host"] = forms.CharField(
                label="サーバー",
                max_length=253,
                required=False,
                disabled=True,
                initial=values["host"],
                help_text="置き場の .env の EMAIL_HOST= の行で決めます (画面では変えません)。例 smtp.example.jp。空ならメールを送りません。",
            )
            self.fields["mail_port"] = forms.IntegerField(
                label="ポート",
                min_value=1,
                max_value=65535,
                required=False,
                disabled=True,
                initial=values["port"],
                help_text="置き場の .env の EMAIL_PORT= の行で決めます (無ければ 587)。STARTTLS は 587、SSL/TLS は 465 のことが多い。",
            )
            self.fields["mail_security"] = forms.ChoiceField(
                label="暗号化の方式",
                choices=[(v, MAIL_SECURITY_LABELS[v]) for v in conf.MAIL_SECURITY],
                required=False,
                disabled=True,
                initial=values["security"],
                help_text="置き場の .env の EMAIL_SECURITY= の行 (starttls・ssl・none) で決めます (無ければ starttls)。",
                widget=Radios(
                    attrs={"class": RADIO_CSS},
                    notes={
                        "starttls": "つないだ後に暗号化する (ポート 587 など)。",
                        "ssl": "初めから暗号化する (ポート 465 など)。",
                        "none": "暗号化しない。パスワードと本文がそのまま流れるおそれがあるので、社内のサーバーだけで使う。",
                    },
                ),
            )
            self.fields["mail_user"] = forms.CharField(
                label="アカウント",
                max_length=254,
                required=False,
                initial=values["user"],
                help_text="サーバーにログインする名前 (メールアドレスのことが多い)。パスワードはここに入れず、置き場の .env に EMAIL_HOST_PASSWORD= の行で置きます。",
            )
            self.fields["mail_from"] = forms.EmailField(
                label="差出人",
                max_length=254,
                required=False,
                initial=values["from"],
                help_text="知らせのメールの差出人のアドレス。「試しに送る」もこのアドレスへ送ります。",
            )
            self.fields["mail_from_name"] = forms.CharField(
                label="差出人の名前",
                max_length=100,
                required=False,
                initial=values["from_name"],
                help_text="メールの差出人の欄に、アドレスと一緒に出す名前 (例 会社名)。空ならアドレスだけを出します。",
            )
            self.fields["mail_site_url"] = forms.CharField(
                label="seizou の URL の頭",
                max_length=200,
                required=False,
                initial=values["site_url"],
                help_text="メールのリンクの頭。人がこの seizou を開く URL の頭です (例 https://seizou.example.jp)。空ならリンクを付けません。",
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
            self.fields[key] = forms.CharField(
                label=label,
                max_length=100,
                initial=current,
                required=key not in self.OPTIONAL_KEYS,
                help_text=setting_note(key),
            )

    def value(self):
        """services に渡す値 (真偽はそのまま、権限の判定は 3 つの欄の辞書、メールの送り方は .env で決めない 4 つの欄の辞書、使わない項目は並び、字はそのまま)。"""
        data = self.cleaned_data
        if self.key == "access_control":
            return {kind: data[kind] for kind in conf.DEFAULTS["access_control"]}
        if self.key == "mail":
            # 欄の名前から頭の mail_ を外した辞書。.env だけで決める鍵 (conf.MAIL_ENV) は渡さない (services が既定で埋める)。
            # 値を整えるのは services (form は欄の型だけ)
            return {name: data[f"mail_{name}"] for name in conf.MAIL_KEYS if name not in conf.MAIL_ENV}
        if self.key == "hidden_fields":
            return list(data[self.HIDDEN_FIELDS_FIELD])
        return data[self.key]

    def service_kwargs(self):
        return {"key": self.key, "value": self.value()}


class DisplaySettingsForm(forms.Form):
    """表示の設定 (人ごとの見た目) の入力。今は上の帯の色 (core.display.HEADER_COLORS) の 1 つだけ。決め 11-05 21:2x。開発の記録 docs/nights/20261105/N167.md

    初期値は本人の今の値 (画面の get_initial)。値を確かめて保存するのは services の set_user_preference (form は選べる名前だけ)。
    """

    header_color = forms.ChoiceField(
        label="ヘッダーの色",
        choices=list(display.HEADER_COLORS.items()),
        widget=forms.RadioSelect(attrs={"class": "radio"}),
    )

    def service_kwargs(self):
        return {"key": display.HEADER_COLOR, "value": self.cleaned_data["header_color"]}


class CompanyForm(ServiceModelForm):
    """会社の情報 (住所・電話・振込先・登録番号・ロゴ) の入力。値を確かめて保存するのは services の update_company (form は欄の型と字数だけ)。

    登録番号の形は clean_registration_no が見る (欄の下の赤い字)。
    """

    # ロゴは絵だけ (Pillow で絵かを確かめる)。SVG と HTML は上げたファイルの口がダウンロードにして <img> に出ないので受けない
    logo = forms.ImageField(
        label="ロゴ",
        required=False,
        validators=[FileExtensionValidator(["png", "jpg", "jpeg"])],
        help_text="PNG か JPEG の絵。納品書と請求書の会社の欄の頭に出る。消すときは「クリア」に check を入れて保存。",
    )

    class Meta:
        model = Company
        fields = ("address", "phone", "bank_account", "registration_no", "logo")
        help_texts = {
            "address": "納品書と請求書の会社の欄に出す。郵便番号から 1 行で (例: 〒000-0000 見本県架空市見本町 1-2-3)。",
            "phone": "例: 00-0000-0000",
            "bank_account": "請求書だけに出す。銀行・支店・口座の種類・番号・名義を 1 行で (例: 作り物銀行 見本支店 普通 0000000 名義 ミホン)。",
            "registration_no": "インボイス (適格請求書) の登録番号。T と 13 桁の数字を続けて入れる (例: T0000000000000)。請求書だけに、会社の名前の下に出す。登録していなければ空のまま (請求書に行を出さない)。",
        }

    def clean_registration_no(self):
        """登録番号の形を確かめる (services.check_registration_no と同じ 1 か所)。形が違えば欄の下の赤い字。

        訳: 請求書に形の違う登録番号を刷らないよう、保存の前に欄で止めるため。出所 = 法令 (インボイス制度の登録番号の形。作者の決め)。
        変えてよい条件 = 登録番号の形が法令で変わったとき。
        """
        value = self.cleaned_data.get("registration_no", "")
        try:
            services.check_registration_no(value)
        except ValueError:
            raise forms.ValidationError("T と 13 桁の数字で入れてください。") from None
        return value


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


class DefaultChoiceField(forms.ChoiceField):
    """選ぶ欄。送られなかった (空の) ときは default の値とみなす (clean と has_changed の両方)。

    明細の行の formset は、何も変えていない行 (has_changed が偽) を飛ばすので、送られなかった欄を初めの値と同じとみなし、
    空の足す行を「変えた行」にしない。受注と発注の明細の「単価の単位」が使う。core は部品の値を持たないので、default は部品の form が渡す。
    """

    def __init__(self, *args, default, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("initial", default)
        self.default = default
        super().__init__(*args, **kwargs)

    def clean(self, value):
        return super().clean(value) or self.default

    def has_changed(self, initial, data):
        return (data or self.default) != (initial or self.default)


def line_totals(pairs, *, show_amount=True) -> dict:
    """明細の合計の枠 (core/_line_totals.html) と保存のボタンの字の元 (開発の記録 docs/nights/20261105/N149.md。作者の決め)。

    pairs は (数量, 単価) か (数量, 単価, 一式か) の組の並び (単価は None でよい)。一式か が真の行は数量を掛けずに単価を金額に足す
    (受注と発注の明細の単価の単位が式の行。core は部品を import しないので、一式かどうかは部品の form が決めて渡す)。
    form は数えない行 (空・消す印) を外してから渡し、
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
    for entry in pairs:
        raw_qty, raw_price = entry[0], entry[1]
        lump = len(entry) > 2 and bool(entry[2])
        count = label_decimal(raw_qty)
        if count is None:
            return unknown
        rows += 1
        qty += count
        price = label_decimal(raw_price)
        if price is None:
            priced = False
        else:
            amount += price if lump else count * price
    return {
        "rows": rows,
        "qty": label_number(qty),
        "amount": label_number(amount) if show_amount and priced else None,
        "show_amount": show_amount,
    }


class SubmitLabelMixin:
    """保存のボタンの字に、押した結果の対象と数と金額を書く口 (開発の記録 docs/nights/20261104/N138.md。決め 11-04 23:0x「ボタンに金額を入れて、押す前の迷いを減らす」)。

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
