"""core の公開する関数 (01 の 10.3)。

- company(): 会社の設定 (Company) の 1 行。無ければ既定で作る。
- next_number(kind): 会社ごとの連番。kind は "order"・"workorder"・"shipment"・"delivery_note"・"invoice"・"purchase_order"。
  形は選べる設定 order_number で決まる (seq = 連番 / ym_seq = 年月 + 連番。月ごとに 1 から)。
  トランザクションの中でだけ呼べる (外で呼ぶと RuntimeError)。行をロックして増やすので、同時に呼んでも重ならない。
- validate_setting(key, value): 選べる設定の値を確かめて、整えた値を返す。誤りは日本語の ValueError (知らない鍵は KeyError)。
- set_setting(key, value, user): 設定の画面からの上書き (SettingOverride) を書く。clear_setting(key, user) は消す。
  どちらも書いた後に seizou.core.conf の控えを捨て、logging の "seizou.settings" に INFO の 1 行を書く (履歴の表は持たない)。
- update_company(company, data, user): 会社の情報 (住所・電話・振込先・ロゴ) を直す。設定の画面の「会社の情報」と、答えの YAML の company (命令 seizou new) が呼ぶ。
"""

from __future__ import annotations

import logging

from django.db import connection, transaction
from django.utils import timezone

from seizou.core import conf
from seizou.core.conf import setting
from seizou.core.models import Company, NumberSequence, SettingOverride

logger = logging.getLogger("seizou.settings")

PREFIXES = {
    "order": "SO",
    "workorder": "WO",
    "shipment": "SH",
    "delivery_note": "DN",
    "invoice": "IV",
    "purchase_order": "PO",
}


def company() -> Company:
    """会社の設定の 1 行。無ければ既定の 1 行を作って返す。"""
    row = Company.objects.order_by("pk").first()
    if row is None:
        row = Company.objects.create(name="会社")
    return row


def next_number(kind: str) -> str:
    """kind の次の番号を返す。トランザクションの外で呼ぶと RuntimeError。"""
    if kind not in PREFIXES:
        raise KeyError(f"知らない連番の種類: {kind}")
    if not connection.in_atomic_block:
        raise RuntimeError("next_number はトランザクション (transaction.atomic) の中で呼ぶ")
    form = setting("order_number")
    if form == "ym_seq":
        period = timezone.localdate().strftime("%y%m")
    else:
        period = ""
    row, _created = NumberSequence.objects.select_for_update().get_or_create(
        kind=kind, period=period, defaults={"last": 0}
    )
    row.last += 1
    row.save(update_fields=["last"])
    prefix = PREFIXES[kind]
    if period:
        return f"{prefix}-{period}-{row.last:04d}"
    return f"{prefix}-{row.last:06d}"


#: 字で持つ設定 (model の名前)。空でない 100 字まで
_TEXT_SETTINGS = ("assistant_model", "intake_model", "feedback_model")  # feedback_model は N59 の前提 (本体が先に入れた)
_TEXT_MAX = 100

#: 業種 (industry) の字数の上限 (左上の会社の名前の下に出す)
_INDUSTRY_MAX = 40


def validate_setting(key, value):
    """選べる設定 key の値 value を確かめて、整えた値を返す。

    知らない鍵は KeyError。画面から変えない鍵 (conf.LOCKED_KEYS) と、形の合わない値は日本語の ValueError。
    主色 (brand_color) と業種 (industry) は、空の字を既定 (設定なし) として通す (点検 seizou_doctor が既定の値も確かめるため)。
    """
    if key not in conf.DEFAULTS:
        raise KeyError(f"知らない設定の鍵: {key}")
    if key in conf.LOCKED_KEYS:
        raise ValueError(f"{key} は画面からは変えられません。答えの YAML の settings に書いてください。")
    if key in conf.CHOICES:
        if not isinstance(value, str) or value not in conf.CHOICES[key]:
            raise ValueError(f"{key} の値は {' / '.join(conf.CHOICES[key])} のどれかにしてください (入れた値: {value!r})。")
        return value
    if isinstance(conf.DEFAULTS[key], bool):
        if not isinstance(value, bool):
            raise ValueError(f"{key} の値は真偽 (true / false) にしてください (入れた値: {value!r})。")
        return value
    if key == "access_control":
        kinds = tuple(conf.DEFAULTS["access_control"])
        if (
            not isinstance(value, dict)
            or set(value) != set(kinds)
            or any(not isinstance(value[kind], str) or value[kind] not in conf.ACCESS_MODES for kind in kinds)
        ):
            raise ValueError(
                "access_control は department・write・money の 3 つの鍵を持つ辞書で、"
                f"値はそれぞれ off / log / on にしてください (入れた値: {value!r})。"
            )
        return {kind: value[kind] for kind in kinds}
    if key == "brand_color":
        text = value.strip() if isinstance(value, str) else None
        if text is None or (text and not conf.BRAND_COLOR_PATTERN.fullmatch(text)):
            raise ValueError(f"brand_color は # と 16 進の 6 桁 (例 #15803d) にしてください。空なら土台の色です (入れた値: {value!r})。")
        return text.lower()
    if key == "industry":
        text = value.strip() if isinstance(value, str) else None
        if text is None or len(text) > _INDUSTRY_MAX:
            raise ValueError(f"industry (業種) は {_INDUSTRY_MAX} 字までの字にしてください。空なら左上は seizou です (入れた値: {value!r})。")
        return text
    if key in _TEXT_SETTINGS:
        text = value.strip() if isinstance(value, str) else ""
        if not text or len(text) > _TEXT_MAX:
            raise ValueError(f"{key} は空でない {_TEXT_MAX} 字までの字にしてください。")
        return text
    if key == "hidden_fields":
        if not isinstance(value, (list, tuple)) or any(not isinstance(name, str) for name in value):
            raise ValueError(f"hidden_fields は項目の名前の並びにしてください (入れた値: {value!r})。")
        names = []
        for name in value:
            name = name.strip()
            if name and name not in names:
                names.append(name)
        return names
    raise ValueError(f"{key} は画面からは変えられません。")


def _user_name(user) -> str:
    name = user.get_username() if user is not None and hasattr(user, "get_username") else ""
    return name or "(不明)"


def set_setting(key, value, user):
    """選べる設定 key を画面の値 value で上書きし、上書きの行 (SettingOverride) を返す。"""
    value = validate_setting(key, value)
    before = conf.setting(key)
    with transaction.atomic():
        row, _created = SettingOverride.objects.update_or_create(
            key=key,
            defaults={"value": value, "updated_by": user if getattr(user, "pk", None) is not None else None},
        )
    # 同じ要求のこの後の読みが新しい値を読むよう、控えを捨てる (別の process は CACHE_SECONDS 秒で読み直す)
    conf.reset_cache()
    logger.info("設定を変えた: %s %r → %r (%s)", key, before, value, _user_name(user))
    return row


def clear_setting(key, user):
    """選べる設定 key の画面の上書きを消し、答え (SEIZOU) か既定の値に戻す。上書きが無くても誤りにしない。"""
    if key not in conf.DEFAULTS:
        raise KeyError(f"知らない設定の鍵: {key}")
    before = conf.setting(key)
    with transaction.atomic():
        SettingOverride.objects.filter(key=key).delete()
    conf.reset_cache()
    logger.info("設定を戻した: %s %r → %r (%s)", key, before, conf.setting(key), _user_name(user))


#: 会社の情報の画面と答えの YAML の company が書く列 (画面の欄の順)
COMPANY_INFO_FIELDS = ("address", "phone", "bank_account", "logo")


def update_company(company, data, user):
    """会社の 1 行の会社の情報を data の値で直し、直した行 (表から取り直した物) を返す。

    data の鍵は COMPANY_INFO_FIELDS の中だけ (ほかの鍵は KeyError。会社名は答えの YAML の name で決まり、ここでは変えない)。
    字の値は前後の空白を落とし、列の字数を超えると日本語の ValueError (表に書く前)。
    transaction.atomic の中で会社の行を select_for_update で取り直し、取り直した行と比べて変わった列だけを書く
    (変わった列が無ければ書かない)。画面の ModelForm は検証の中で対象の物の値を先に書き換えるので、渡された物とは比べない。
    書いた後に logging の "seizou.settings" に INFO の 1 行 (変えた列の見出しだけ。値は書かない = 振込先の番号を記録に残さない)。
    logo は、新しく上げたファイル (UploadedFile) なら company/ の下に "logo" + 拡張子 (小文字) で置き、False (「クリア」) なら
    今のファイルを外す。ほかの値 (今のファイル・None) は触らない。差し替えた・外した古いファイルは置き場に残す
    (部品の図面の masters.services.update_item と同じ。消すのは会期後)。形 (PNG か JPEG) は form が見る。
    """
    from pathlib import PurePath

    from django.core.files.uploadedfile import UploadedFile

    unknown = [key for key in data if key not in COMPANY_INFO_FIELDS]
    if unknown:
        raise KeyError(f"知らない会社の情報の鍵: {'・'.join(str(key) for key in unknown)}")
    logo = data.get("logo")
    values = {}
    for name in COMPANY_INFO_FIELDS:
        if name not in data or name == "logo":
            continue
        field = Company._meta.get_field(name)
        value = data[name]
        text = "" if value is None else str(value).strip()
        if field.max_length is not None and len(text) > field.max_length:
            raise ValueError(f"{field.verbose_name}は {field.max_length} 字までにしてください。")
        values[name] = text
    with transaction.atomic():
        row = Company.objects.select_for_update().get(pk=company.pk)
        changed = [name for name, value in values.items() if getattr(row, name) != value]
        for name in changed:
            setattr(row, name, values[name])
        if isinstance(logo, UploadedFile):
            row.logo.save("logo" + PurePath(logo.name or "").suffix.lower(), logo, save=False)
            changed.append("logo")
        elif logo is False and row.logo:
            row.logo = ""
            changed.append("logo")
        if changed:
            row.save(update_fields=changed)
    if changed:
        labels = "・".join(str(Company._meta.get_field(name).verbose_name) for name in changed)
        logger.info("会社の情報を変えた: %s (%s)", labels, _user_name(user))
    return row


__all__ = ["clear_setting", "company", "next_number", "set_setting", "transaction", "update_company", "validate_setting"]
