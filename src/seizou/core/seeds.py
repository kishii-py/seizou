"""YAML と CSV の字 → 値の決まりの置き場 (公開)。

部品の名前を知らない。作り物のデータの読み込み (seizou.core.demo) と CSV の取り込み (seizou.core.csvio) が
同じ関数を使う (決まりを 2 か所に書かない)。ほかの部品を import しない (model と会社ごとの項目の列は引数で受ける)。

- is_blank・fields_by_label・coerce・coerce_ext: 1 つの値 (CSV の字か YAML の値) を列の型の値にする。
  誤りは日本語の ValueError (呼ぶ側がファイルと位置を付ける)。
- guard・read・check_keys・decimal・date・text: 部品の種 (部品の demo_seed.py) の YAML の読み方。
  guard が services と変換の誤りに、ファイルと「N 件目」を付ける。
  yaml は read の中でだけ読む (CSV の取り込みが yaml を読まない)。
- decimal_fits: 外から来た数の字を読んだ Decimal が、表の列に入り得る桁 (指数) の中か。
"""

from __future__ import annotations

import datetime
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation

from django.db import models

from seizou.core.exceptions import RuleViolation

#: 一覧の CSV の書き出しが真偽に使う字 (core.csvio.to_text)。取り込みが読む字の組に入れ、書き出しと取り込みで言葉をそろえる (決め 11-06 22:0x)
TRUE_TEXT = "はい"
FALSE_TEXT = "いいえ"
TRUE_WORDS = frozenset({"true", "1", TRUE_TEXT, "yes"})
FALSE_WORDS = frozenset({"false", "0", FALSE_TEXT, "no"})


def is_blank(raw):
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


#: 外から来た数の整数の部分の桁の上限。表の数と金額の列で一番広い整数の桁 (connect の Exchange.amount の
#: max_digits=20・decimal_places=4 = 整数 16 桁)。これより大きい数は、どの列にも入らない
DECIMAL_MAX_INT_DIGITS = 16
#: 外から来た数の小数の部分の桁の上限。表の列は小数 4 桁まで。末尾の 0 を書いた字 (480.000000) も受けるように余りを持たせる
DECIMAL_MAX_PLACES = 10


def decimal_fits(number) -> bool:
    """number が有限の Decimal で、整数の部分が DECIMAL_MAX_INT_DIGITS 桁まで・小数の部分が DECIMAL_MAX_PLACES 桁までか。

    指数 (adjusted と as_tuple の exponent) だけを見るので、桁の大きな数でも字にせずにすぐ答える (normalize と f"{n:f}" を呼ばない)。
    外から来た数の字 (AI の読み取りの答え・GET の値) を Decimal にした後、字にする前と比べる前に呼ぶ。
    訳: 「1E+999999999」のような短い字は Decimal には読めるが、小数点の形の字にすると 10 億桁になり、1 回の要求や
    1 枚の注文書で記憶を使い切るため、表の列に入り得る桁の外の数を受けない。
    出所 = Claude Security の走査の候補 (11-07 の 17・30)。桁は表の列の max_digits と decimal_places から決めた AI の案。
    変えてよい条件 = 表に、もっと広い桁の数と金額の列を足す時 (その列の桁に合わせて広げる)。
    """
    if not isinstance(number, Decimal) or not number.is_finite():
        return False
    return number.adjusted() < DECIMAL_MAX_INT_DIGITS and number.as_tuple().exponent >= -DECIMAL_MAX_PLACES


#: 画面の字 (verbose_name) を直す前の見出し → 列の名前。種の CSV がこの字のままでも読む。
FORMER_HEADERS = {
    "masters.Item": {"標準単価": "standard_price"},
    "masters.Partner": {"小計の計算方法": "rounding", "消費税の計算方法": "tax_mode"},
}

#: 消費税の端数の扱いの前の字と値 → 消費税の端数の向きの値 (決め LAW-2・C95-3)。
#: 明細ごとに丸める 2 つをやめてトータルだけにしたので、前の CSV と種のこの字は、トータルとこの向きに読み替える。
#: core と masters の migration (0005_company_tax_mode_total・0013_partner_tax_mode_total) が表の値を移したときと同じ対応。
FORMER_TAX_MODES = {"各行四捨五入": "round", "line_round": "round", "各行切捨": "floor", "line_floor": "floor"}


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


# --- 部品の種の YAML の読み方 (billing・connect・stock の demo_seed.py が同じ物を写していた) ---


@contextmanager
def guard(name, number):
    """services と変換の誤りに、ファイルと位置を付けて ValueError に上げ直す (巻き戻しは呼んだ側の atomic)。"""
    try:
        yield
    except (ValueError, RuleViolation) as exc:
        raise ValueError(f"{name} の {number} 件目: {exc}") from exc


def read(path, root_key):
    """YAML を読み、根の鍵の下の並びを返す。"""
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name} を YAML として読めません: {exc}") from exc
    if not isinstance(data, dict) or root_key not in data:
        raise ValueError(f"{path.name} に「{root_key}」の並びがありません。")
    entries = data[root_key] or []
    if not isinstance(entries, list):
        raise ValueError(f"{path.name} の「{root_key}」は並びで書いてください。")
    return entries


def check_keys(entry, allowed, what):
    if not isinstance(entry, dict):
        raise ValueError(f"{what}の形が違います (鍵と値の並びではありません)。")
    unknown = [key for key in entry if key not in allowed]
    if unknown:
        raise ValueError(f"{what}の鍵「{'」「'.join(unknown)}」が分かりません。")


def decimal(raw, label):
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label}「{raw}」を数として読めません。") from exc
    if not value.is_finite():
        raise ValueError(f"{label}「{raw}」を数として読めません。")
    return value


def date(raw, label):
    if isinstance(raw, datetime.date):
        return raw
    try:
        return datetime.date.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{label}「{raw}」を日付 (YYYY-MM-DD) として読めません。") from exc


def text(raw):
    return "" if raw is None else str(raw).strip()
