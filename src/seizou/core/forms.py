"""入力の form の土台。

ServiceModelForm: ModelForm だが、表に関わる判断 (一意など) は form で見ず services に任せる (R13 の線)。
form が見るのは欄の型と必須だけ。services の決まりの違反は日本語の文の ValueError か RuleError で返り、
画面の土台が赤の知らせにする。
整数の欄は、表の検査 (MinValueValidator・MaxValueValidator) から下限と上限を取り、欄の min・max と
form の検査にそろえる (例: 締日は 1〜31。誤りの文は「1 以上」「31 以下」)。
SettingForm: 選べる設定 1 つの入力 (設定の画面の「編集」)。値を確かめて整えるのは core の services。
"""

from django import forms
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator

from seizou.core import conf
from seizou.core.models import Company

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
                choices=[(v, f"{words.get(v, v)} ({v})") for v in conf.CHOICES[key]],
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
            choices = [(m, f"{words.get(m, m)} ({m})") for m in conf.ACCESS_MODES]
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
