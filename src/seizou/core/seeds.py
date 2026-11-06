"""YAML と CSV の字 → 値の決まりの置き場 (公開)。

部品の名前を知らない。作り物のデータの読み込み (seizou.core.demo) と CSV の取り込み (seizou.core.csvio) が
同じ関数を使う (決まりを 2 か所に書かない)。ほかの部品を import しない (model と会社ごとの項目の列は引数で受ける)。

- is_blank・fields_by_label・coerce・coerce_ext: 1 つの値 (CSV の字か YAML の値) を列の型の値にする。
  誤りは日本語の ValueError (呼ぶ側がファイルと位置を付ける)。
"""

from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation

from django.db import models

TRUE_WORDS = frozenset({"true", "1", "はい", "yes"})
FALSE_WORDS = frozenset({"false", "0", "いいえ", "no"})


def is_blank(raw):
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


#: 画面の字 (verbose_name) を直す前の見出し → 列の名前。種の CSV がこの字のままでも読む。
FORMER_HEADERS = {
    "masters.Item": {"標準単価": "standard_price"},
    "masters.Partner": {"小計の計算方法": "rounding", "消費税の計算方法": "tax_mode"},
}


def fields_by_label(model):
    """見出し (verbose_name) → 列。主キーは除く。前の見出し (FORMER_HEADERS) でも引ける。"""
    fields = {f.verbose_name: f for f in model._meta.concrete_fields if not f.primary_key}
    by_name = {f.name: f for f in fields.values()}
    for header, name in FORMER_HEADERS.get(model._meta.label, {}).items():
        if name in by_name:
            fields.setdefault(header, by_name[name])
    return fields


def _choice_value(field, raw):
    text = str(raw).strip()
    by_label = {str(label): value for value, label in field.flatchoices}
    if text in by_label:
        return by_label[text]
    if text in {value for value, _label in field.flatchoices}:
        return text
    raise ValueError(f"{field.verbose_name}「{text}」は使えません。{'・'.join(by_label)} のどれかにしてください。")


def coerce(field, raw):
    """種の 1 つの値 (CSV の字か YAML の値) を、列の型の値にする。空は、空を許す列なら空 (数と日付は None)。"""
    if isinstance(field, models.BooleanField):
        if isinstance(raw, bool):
            return raw
        if is_blank(raw):
            return field.default if field.has_default() else False
        word = str(raw).strip().lower()
        if word in TRUE_WORDS:
            return True
        if word in FALSE_WORDS:
            return False
        raise ValueError(f"{field.verbose_name}「{raw}」は はい か いいえ にしてください。")
    if is_blank(raw):
        if field.null:
            return None
        if field.blank:
            return ""
        raise ValueError(f"{field.verbose_name}が空です。値を入れてください。")
    if field.choices:
        return _choice_value(field, raw)
    try:
        if isinstance(field, models.DecimalField):
            return Decimal(str(raw).strip())
        if isinstance(field, models.IntegerField):
            return int(str(raw).strip())
        if isinstance(field, models.DateField):
            if isinstance(raw, datetime.date):
                return raw
            return datetime.date.fromisoformat(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field.verbose_name}「{raw}」の形が違います。") from None
    text = str(raw).strip()
    limit = getattr(field, "max_length", None)
    if limit and len(text) > limit:
        raise ValueError(f"{field.verbose_name}は {limit} 字までです ({len(text)} 字ありました)。")
    return text


def coerce_ext(column, raw):
    """会社ごとの項目 (登録の列) の値。型は登録の型 (text・number・date・choice・bool)。"""
    if column.type == "bool":
        return coerce(models.BooleanField(column.label), raw)
    if column.type == "number":
        return coerce(models.DecimalField(column.label, null=True, blank=True), raw)
    if column.type == "date":
        return coerce(models.DateField(column.label, null=True, blank=True), raw)
    return "" if is_blank(raw) else str(raw).strip()
