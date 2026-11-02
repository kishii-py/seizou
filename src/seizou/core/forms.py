"""入力の form の土台。

ServiceModelForm: ModelForm だが、表に関わる判断 (一意など) は form で見ず services に任せる (R13 の線)。
form が見るのは欄の型と必須だけ。services の決まりの違反は日本語の文の ValueError か RuleError で返り、
画面の土台が赤の知らせにする。
整数の欄は、表の検査 (MinValueValidator・MaxValueValidator) から下限と上限を取り、欄の min・max と
form の検査にそろえる (例: 締日は 1〜31。誤りの文は「1 以上」「31 以下」)。
"""

from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator

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
