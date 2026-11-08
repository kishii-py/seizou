"""core の公開する関数 (core の公開する物の設計の決め)。

- company(): 会社の設定 (Company) の 1 行。無ければ既定で作る。
- company_or_none(): 会社の設定 (Company) の 1 行を読むだけ。無ければ作らずに None。
- next_number(kind): 会社ごとの連番。kind は "order"・"workorder"・"shipment"・"delivery_note"・"invoice"・"purchase_order"。
  形は選べる設定 order_number で決まる (seq = 連番 / ym_seq = 年月 + 連番。月ごとに 1 から)。
  トランザクションの中でだけ呼べる (外で呼ぶと RuntimeError)。行をロックして増やすので、同時に呼んでも重ならない。
- validate_setting(key, value): 選べる設定の値を確かめて、整えた値を返す。誤りは日本語の ValueError (知らない鍵は KeyError)。
- set_setting(key, value, user): 設定の画面からの上書き (SettingOverride) を書く。clear_setting(key, user) は消す。
  どちらも書いた後に seizou.core.conf の控えを捨て、logging の "seizou.settings" に INFO の 1 行を書く (変更は core の変更の履歴にも残す)。
- user_preference(user, key, default): 人ごとの表示の設定 (UserPreference) の値。行が無いか、ログインしていない人は default。
  set_user_preference(user, key, value) は本人の行を書く (鍵と値は core.display の物だけ。標準の値は行を消す)。
- update_company(company, data, user): 会社の情報 (住所・電話・振込先・登録番号・ロゴ) を直す。設定の画面の「会社の情報」と、答えの YAML の company (命令 seizou new) が呼ぶ。
- check_registration_no(text): 適格請求書の登録番号の形 (T と 13 桁の数字) を確かめる。形が違えば日本語の ValueError。
- company_icons(company): ロゴから作った印 (favicon) の URL。無ければ None (土台は static の favicon.svg を出す)。
- record_history(obj, user, *, before=None, by="", changes=None): 変更の履歴 (ChangeLog) に 1 行書く (作った・直した・使用停止・再開・取消)。
  各 app の services が、書く前に core.history.saved_state(obj) を取り、書いた後に同じ atomic の中で呼ぶ (作者の決め)。
- record_history_deleted(obj, user, *, by=""): 消す前に呼び、その時の値を前に残して「消した」の 1 行を書く。
"""

from __future__ import annotations

import logging
import re

from django.db import connection, transaction
from django.utils import timezone

from seizou.core import conf
from seizou.core.conf import setting
from seizou.core.models import BellOpened, ChangeLog, Company, NumberSequence, SettingOverride, UserPreference

logger = logging.getLogger("seizou.settings")

PREFIXES = {
    "order": "SO",
    "workorder": "WO",
    "shipment": "SH",
    "delivery_note": "DN",
    "invoice": "IV",
    "purchase_order": "PO",
    "self_invoice": "SI",
}


def company_or_none() -> Company | None:
    """会社の設定の 1 行 (pk の小さい物)。読むだけで、無ければ作らずに None を返す。"""
    return Company.objects.order_by("pk").first()


def company() -> Company:
    """会社の設定の 1 行。無ければ既定の 1 行を作って返す。"""
    row = company_or_none()
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
_TEXT_SETTINGS = ("assistant_model", "intake_model", "feedback_model")  # feedback_model は開発の記録 docs/nights/20261103/N59.md の前提 (本体が先に入れた)
_TEXT_MAX = 100

#: 業種 (industry) の字数の上限 (左上の会社の名前の下に出す)
_INDUSTRY_MAX = 40


def validate_setting(key, value):
    """選べる設定 key の値 value を確かめて、整えた値を返す。

    知らない鍵は KeyError。画面から変えない鍵 (conf.LOCKED_KEYS) と、形の合わない値は日本語の ValueError。
    主色 (brand_color) と業種 (industry) は、空の字を既定 (設定なし) として通す (点検 seizou_doctor が既定の値も確かめるため)。
    mail (知らせのメールの送り方) は、欠けた中の鍵を既定で埋め、7 つの形を見る。パスワードは受けない (.env の EMAIL_HOST_PASSWORD だけ)。
    訳: 送れない形の送り方を残さないため。出所 = AI の案 (形は SMTP の名前とポートとメールアドレスと URL の一般の形)。変えてよい条件 = 送り方の項目を足す時。
    """
    if key not in conf.DEFAULTS:
        raise KeyError(f"知らない設定の鍵: {key}")
    if key in conf.LOCKED_KEYS:
        raise ValueError(f"保存できません。{key} は画面からは変えられません。答えの YAML の settings に書いてください。")
    if key in conf.CHOICES:
        if not isinstance(value, str) or value not in conf.CHOICES[key]:
            raise ValueError(f"保存できません。{key} の値は {' / '.join(conf.CHOICES[key])} のどれかにしてください (入れた値: {value!r})。")
        return value
    if isinstance(conf.DEFAULTS[key], bool):
        if not isinstance(value, bool):
            raise ValueError(f"保存できません。{key} の値は真偽 (true / false) にしてください (入れた値: {value!r})。")
        return value
    if key == "access_control":
        kinds = tuple(conf.DEFAULTS["access_control"])
        if (
            not isinstance(value, dict)
            or set(value) != set(kinds)
            or any(not isinstance(value[kind], str) or value[kind] not in conf.ACCESS_MODES for kind in kinds)
        ):
            raise ValueError(
                "保存できません。access_control は department・write・money の 3 つの鍵を持つ辞書で、"
                f"値はそれぞれ off / log / on にしてください (入れた値: {value!r})。"
            )
        return {kind: value[kind] for kind in kinds}
    if key == "mail":
        return _validate_mail(value)
    if key == "brand_color":
        text = value.strip() if isinstance(value, str) else None
        if text is None or (text and not conf.BRAND_COLOR_PATTERN.fullmatch(text)):
            raise ValueError(f"保存できません。brand_color は # と 16 進の 6 桁 (例 #15803d) にしてください。空なら土台の色です (入れた値: {value!r})。")
        return text.lower()
    if key == "industry":
        text = value.strip() if isinstance(value, str) else None
        if text is None or len(text) > _INDUSTRY_MAX:
            raise ValueError(f"保存できません。industry (業種) は {_INDUSTRY_MAX} 字までの字にしてください。空なら左上は seizou です (入れた値: {value!r})。")
        return text
    if key in _TEXT_SETTINGS:
        text = value.strip() if isinstance(value, str) else ""
        if not text or len(text) > _TEXT_MAX:
            raise ValueError(f"保存できません。{key} は空でない {_TEXT_MAX} 字までの字にしてください。")
        return text
    if key == "hidden_fields":
        if not isinstance(value, (list, tuple)) or any(not isinstance(name, str) for name in value):
            raise ValueError(f"保存できません。hidden_fields は項目の名前の並びにしてください (入れた値: {value!r})。")
        names = []
        for name in value:
            name = name.strip()
            if name and name not in names:
                names.append(name)
        return names
    raise ValueError(f"保存できません。{key} は画面からは変えられません。")


#: 知らせのメールの送り方 (mail) の字数の上限 (サーバーの名前 253・アカウントと差出人 254・差出人の名前 100・URL の頭 200)
_MAIL_HOST_MAX = 253
_MAIL_ADDRESS_MAX = 254
_MAIL_FROM_NAME_MAX = 100
_MAIL_SITE_URL_MAX = 200


def _mail_text(value):
    """mail の中の字の値の前後の空白を落とす。字でなければ None (呼び手が断る)。"""
    return value.strip() if isinstance(value, str) else None


def _mail_site_url_ok(text: str) -> bool:
    """seizou の URL の頭の形。scheme が http か https・ホストがある・空白が無い・ポートが読める (connect の _url_is_valid と同じ見方)。"""
    from urllib.parse import urlsplit

    if len(text) > _MAIL_SITE_URL_MAX or any(char.isspace() for char in text):
        return False
    try:
        parts = urlsplit(text)
        _ = parts.port  # 数でないポートと範囲の外のポートは ValueError になる
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def _validate_mail(value) -> dict:
    """知らせのメールの送り方 (mail) を確かめて、conf.MAIL_KEYS の順の新しい辞書を返す。誤りは「保存できません。」で始まる ValueError。

    見る順は 辞書と鍵 → サーバー → ポート → 暗号化の方式 → アカウント → 差出人 → 差出人の名前 → seizou の URL の頭。
    欠けた中の鍵は既定 (conf.DEFAULTS["mail"]) で埋める (答えの YAML に一部だけ書いた会社でも、点検と画面が止まらないように)。
    サーバーと差出人と URL の頭は空を通す (メールを使わない会社の既定)。字は前後の空白を落とし、URL の頭は末尾の「/」を落とす。
    入れた値は断りの文に入れない。断りの文は raise ValueError の括弧の中に字のまま書く (使い方の画面が ast で集め、
    画面から起きない文の表 core/manual.py の NOT_FROM_SCREEN と 1 字ずつ比べるため)。パスワードの鍵は受けない。
    訳: 送れない形の送り方を残さないため。出所 = AI の案 (形は SMTP の名前とポートとメールアドレスと URL の一般の形)。変えてよい条件 = 送り方の項目を足す時。
    """
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    if not isinstance(value, dict) or any(key not in conf.MAIL_KEYS for key in value):
        raise ValueError("保存できません。メールの送り方は、host・port・security・user・from・from_name・site_url の鍵を持つ辞書にしてください。")
    defaults = conf.DEFAULTS["mail"]
    values = {key: value.get(key, defaults[key]) for key in conf.MAIL_KEYS}

    host = _mail_text(values["host"])
    if host is None or len(host) > _MAIL_HOST_MAX or any(char.isspace() for char in host):
        raise ValueError("保存できません。メールの送り方の「サーバー」は、空白の無い 253 字までの名前にしてください (例 smtp.example.jp)。")
    port = values["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("保存できません。メールの送り方の「ポート」は 1〜65535 の整数にしてください。")
    security = values["security"]
    if not isinstance(security, str) or security not in conf.MAIL_SECURITY:
        raise ValueError("保存できません。メールの送り方の「暗号化の方式」は starttls・ssl・none のどれかにしてください。")
    user = _mail_text(values["user"])
    if user is None or len(user) > _MAIL_ADDRESS_MAX:
        raise ValueError("保存できません。メールの送り方の「アカウント」は 254 字までにしてください。")
    sender = _mail_text(values["from"])
    if sender:
        try:
            validate_email(sender)
        except ValidationError:
            sender = None
    if sender is None or len(sender) > _MAIL_ADDRESS_MAX:
        raise ValueError("保存できません。メールの送り方の「差出人」がメールアドレスの形ではありません。例 notice@example.jp の形で入れてください。")
    from_name = _mail_text(values["from_name"])
    if from_name is None or len(from_name) > _MAIL_FROM_NAME_MAX or any(ord(char) < 32 or ord(char) == 127 for char in from_name):
        raise ValueError("保存できません。メールの送り方の「差出人の名前」は、改行の無い 100 字までにしてください。")
    site_url = _mail_text(values["site_url"])
    if site_url is None or (site_url and not _mail_site_url_ok(site_url)):
        raise ValueError("保存できません。メールの送り方の「seizou の URL の頭」は http:// か https:// で始まる URL にしてください (例 https://seizou.example.jp)。")
    return {
        "host": host,
        "port": port,
        "security": security,
        "user": user,
        "from": sender,
        "from_name": from_name,
        "site_url": site_url.rstrip("/"),
    }


def _user_name(user) -> str:
    name = user.get_username() if user is not None and hasattr(user, "get_username") else ""
    return name or "(不明)"


def _setting_change(key, before, after) -> list[dict]:
    """設定 key の前と後の値の変更の履歴の中身 (字は設定の画面の字)。前と後の字が同じなら空の並び。

    settings_screen は関数の中で import する (settings_screen が module の頭でこの module を import するため)。
    """
    from seizou.core import settings_screen

    label = settings_screen.SETTING_LABELS.get(key, (key, {}))[0]
    before_text = settings_screen._display(key, before)
    after_text = settings_screen._display(key, after)
    if before_text == after_text:
        return []
    return [{"field": key, "label": label, "type": "text", "before": before_text, "after": after_text}]


def set_setting(key, value, user):
    """選べる設定 key を画面の値 value で上書きし、上書きの行 (SettingOverride) を返す。

    前と後の字 (設定の画面の字) が違えば、同じ atomic の中で変更の履歴に「直した」の 1 行を書く (作者の決めの「会社の設定」)。
    """
    value = validate_setting(key, value)
    before = conf.setting(key)
    with transaction.atomic():
        row, _created = SettingOverride.objects.update_or_create(
            key=key,
            defaults={"value": value, "updated_by": user if getattr(user, "pk", None) is not None else None},
        )
        changes = _setting_change(key, before, value)
        if changes:
            _write_history(row, user, ChangeLog.ACTION_UPDATED, changes, text=changes[0]["label"])
    # 同じ要求のこの後の読みが新しい値を読むよう、控えを捨てる (別の process は CACHE_SECONDS 秒で読み直す)
    conf.reset_cache()
    logger.info("設定を変えた: %s %r → %r (%s)", key, before, value, _user_name(user))
    return row


def clear_setting(key, user):
    """選べる設定 key の画面の上書きを消し、答え (SEIZOU) か既定の値に戻す。上書きが無くても誤りにしない。

    上書きの行があり、戻った値の字が前と違えば、同じ atomic の中で変更の履歴に「直した」の 1 行を書く (後の値は戻った値の字。
    消した行でなく値が変わった記録のため)。上書きの行が無ければ値が変わらないので書かない。
    """
    if key not in conf.DEFAULTS:
        raise KeyError(f"知らない設定の鍵: {key}")
    before = conf.setting(key)
    with transaction.atomic():
        row = SettingOverride.objects.filter(key=key).first()
        if row is not None:
            row_pk = row.pk
            row.delete()
            conf.reset_cache()
            changes = _setting_change(key, before, conf.setting(key))
            if changes:
                row.pk = row_pk  # delete() は物の pk を None にする。消した行の番号で残す
                _write_history(row, user, ChangeLog.ACTION_UPDATED, changes, text=changes[0]["label"])
    conf.reset_cache()
    logger.info("設定を戻した: %s %r → %r (%s)", key, before, conf.setting(key), _user_name(user))


def user_preference(user, key, default=""):
    """人ごとの表示の設定 key の値 (読むだけ。決め 11-05 21:2x。開発の記録 docs/nights/20261105/N167.md)。行が無いか、ログインしていない人 (表に無い利用者を含む) は default。"""
    if user is None or not getattr(user, "is_authenticated", False) or getattr(user, "pk", None) is None:
        return default
    row = UserPreference.objects.filter(user=user, key=key).first()
    return default if row is None else row.value


def set_user_preference(user, key, value):
    """人ごとの表示の設定 key を value にし、書いた行を返す (標準の値なら行を消して None)。本人の行だけを書く。

    鍵は core.display の物だけ (今は上の帯の色 = HEADER_COLOR。知らない鍵は KeyError)。
    上の帯の色は HEADER_COLORS の名前だけを通し、外れた値は日本語の ValueError。標準 (white) は行を消す (既定 = 行なし)。
    """
    from seizou.core import display

    if key != display.HEADER_COLOR:
        raise KeyError(f"知らない表示の設定の鍵: {key}")
    if not isinstance(value, str) or value not in display.HEADER_COLORS:
        raise ValueError(
            f"保存できません。ヘッダーの色は {' / '.join(display.HEADER_COLORS)} のどれかです (入れた値: {value!r})。"
            "表示の設定の見本から選んでください。"
        )
    with transaction.atomic():
        if value == display.HEADER_COLOR_DEFAULT:
            UserPreference.objects.filter(user=user, key=key).delete()
            return None
        row, _created = UserPreference.objects.update_or_create(user=user, key=key, defaults={"value": value})
    return row


#: 会社の情報の画面と答えの YAML の company が書く列 (画面の欄の順)
COMPANY_INFO_FIELDS = ("address", "phone", "bank_account", "registration_no", "logo")
#: 適格請求書発行事業者の登録番号の形 (T と 13 桁の数字を続けた 14 字。作者の決め)。
#: 数字は [0-9] で書く (\d は全角の数字にも当たる)。cli.py の答えの読みは core を import しないので、同じ形を写して持つ
REGISTRATION_NO_PATTERN = re.compile(r"T[0-9]{13}")
#: ロゴから作る印 (favicon) の絵 (ロゴと同じ storage の固定の名前 → 一辺の画素。PNG)。決め 11-05 20:1x。開発の記録 docs/nights/20261105/N166.md
COMPANY_ICONS = {"company/favicon.png": 32, "company/apple-touch-icon.png": 180}


def update_company(company, data, user):
    """会社の 1 行の会社の情報を data の値で直し、直した行 (表から取り直した物) を返す。

    data の鍵は COMPANY_INFO_FIELDS の中だけ (ほかの鍵は KeyError。会社名は答えの YAML の name で決まり、ここでは変えない)。
    字の値は前後の空白を落とし、列の字数を超えると日本語の ValueError (表に書く前)。
    transaction.atomic の中で会社の行を select_for_update で取り直し、取り直した行と比べて変わった列だけを書く
    (変わった列が無ければ書かない)。画面の ModelForm は検証の中で対象の物の値を先に書き換えるので、渡された物とは比べない。
    書いた後に logging の "seizou.settings" に INFO の 1 行 (変えた列の見出しだけ。値は書かない = 振込先の番号を記録に残さない)。
    logo は、新しく上げたファイル (UploadedFile) なら company/ の下に "logo" + 拡張子 (小文字) で置き、False (「クリア」) なら
    今のファイルを外す。ほかの値 (今のファイル・None) は触らない。差し替えた・外した古いファイルは置き場に残す
    (部品の図面の masters.services.update_item と同じ。消すのは次の版で)。形 (PNG か JPEG) は form が見る。
    ロゴを上げたら、ロゴから印 (COMPANY_ICONS) を作って同じ storage に置き、外したら印を消す (_write_company_icons)。
    登録番号はインボイス制度で適格請求書に書く番号 (作者の決め)。形 (T と 13 桁の数字) は check_registration_no が見る (作者の決め)。
    変えた列があれば、同じ atomic の中で変更の履歴に 1 行書く (前は取り直した行の core.history.saved_state)。振込先は直したことと
    見出しだけを残し、前と後の値は残さない (Company.history_hidden_values)。
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
            raise ValueError(f"保存できません。{field.verbose_name}は {field.max_length} 字までにしてください。")
        if name == "registration_no":
            check_registration_no(text)
        values[name] = text
    from seizou.core import history

    with transaction.atomic():
        row = Company.objects.select_for_update().get(pk=company.pk)
        before = history.saved_state(row)
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
            record_history(row, user, before=before)
    if "logo" in changed and not _write_company_icons(row):
        logger.info("印は作らなかった: ロゴを Pillow で絵として開けない (%s)", _user_name(user))
    if changed:
        labels = "・".join(str(Company._meta.get_field(name).verbose_name) for name in changed)
        logger.info("会社の情報を変えた: %s (%s)", labels, _user_name(user))
    return row


def check_registration_no(text):
    """登録番号の字 (前後の空白を落とした物) の形を確かめる。空 (登録していない事業者) は通す。形が違えば日本語の ValueError。

    インボイス制度 (消費税法の適格請求書等保存方式) の登録番号は、T と 13 桁の数字。会社の情報の変更の画面 (core.forms.CompanyForm) と
    update_company が呼ぶ。小文字の t・T の無い 13 桁・桁の足りない値・全角の字は止める (字を直して通すことはしない)。
    検査の数字 (13 桁の最初の 1 桁) と、国税庁の公表サイトでの登録の有無は見ない。
    確かめた資料: 中小企業共通 EDI の付表 3 の SIID27 の値の例 (T と 13 桁を続けた 1 つの字)。国税庁の資料は未読。
    訳: お客様に渡す請求書に形の違う登録番号を刷らないため (形の違う値は打ち間違いしか無い。RULES.md の「止めるのは、あり得ない入力と、記録どうしの矛盾だけ」のあり得ない入力)。出所 = 法令 (インボイス制度の登録番号の形。作者の決め)。
    変えてよい条件 = 登録番号の形が法令で変わったとき。
    """
    if text and not REGISTRATION_NO_PATTERN.fullmatch(text):
        raise ValueError("保存できません。登録番号は T と 13 桁の数字で入れてください。")


def _write_company_icons(row) -> bool:
    """今の印 (COMPANY_ICONS) を消し、ロゴがあれば、ロゴから印を作って同じ storage に置く。決め 11-05 20:1x。開発の記録 docs/nights/20261105/N166.md

    正方形になるよう上下か左右に余白を足してから縮める (余白は透明。ロゴが透明を持たなければ白)。
    ロゴを Pillow で絵として開けない (SVG など) ときは作らず False (ロゴはそのまま)。ロゴが無ければ消すだけで True。
    """
    from io import BytesIO

    from django.core.files.base import ContentFile
    from PIL import Image, ImageOps

    storage = row.logo.storage
    for name in COMPANY_ICONS:
        storage.delete(name)
    if not row.logo:
        return True
    try:
        with storage.open(row.logo.name, "rb") as handle, Image.open(handle) as image:
            fill = (0, 0, 0, 0) if image.has_transparency_data else (255, 255, 255, 255)
            logo = image.convert("RGBA")
    except (OSError, ValueError, Image.DecompressionBombError):
        return False
    for name, size in COMPANY_ICONS.items():
        buffer = BytesIO()
        ImageOps.pad(logo, (size, size), method=Image.Resampling.LANCZOS, color=fill).save(buffer, format="PNG")
        storage.save(name, ContentFile(buffer.getvalue()))
    return True


def company_icons(company) -> dict | None:
    """ロゴから作った印の URL ({"icon": 32 の PNG, "touch": 180 の PNG})。会社にロゴが無いか印のファイルが無ければ None。

    URL の末尾に ?v=<字> を付ける。字は 32 の印の更新時刻 (秒) の 16 進 (storage が時刻を持たなければ大きさの 16 進)。
    印を作り直すと字が変わるので、ブラウザが古い印を出し続けない (決め 11-06 00:0x。開発の記録 docs/nights/20261106/N173.md。/favicon.ico の口は付けない)。
    """
    if not getattr(company, "logo", None):
        return None
    storage = company.logo.storage
    icon, touch = COMPANY_ICONS
    if not storage.exists(icon):
        return None
    try:
        stamp = int(storage.get_modified_time(icon).timestamp())
    except NotImplementedError:
        stamp = storage.size(icon)
    version = f"?v={stamp:x}"
    return {"icon": storage.url(icon) + version, "touch": storage.url(touch) + version}


def _actor(user):
    """変更の履歴の人の列の値 (表にいる利用者だけ。AnonymousUser と表に無い物は None)。"""
    return user if getattr(user, "pk", None) is not None and getattr(user, "is_authenticated", True) else None


def _write_history(obj, user, action, changes, *, by="", text=""):
    """変更の履歴の 1 行を書く (record_history・record_history_deleted と設定の記録が呼ぶ)。

    人の字はその時の str(user)。人の無い処理は呼び手が渡した by の字。どちらも無ければ空 (画面は「システム」)。
    行の字は text (渡さなければ str(obj))。出す詳細は core.history.owner_of(obj)。
    """
    from seizou.core import history

    actor = _actor(user)
    owner = history.owner_of(obj)
    return ChangeLog.objects.create(
        actor=actor,
        actor_label=(str(actor) if actor is not None else by or "")[:150],
        action=action,
        model=obj._meta.label_lower,
        object_pk=obj.pk,
        object_text=(text or str(obj))[:200],
        owner_model=owner._meta.label_lower,
        owner_pk=owner.pk,
        changes=list(changes),
    )


def _hidden_value(obj, name, text) -> str:
    """history_hidden_values の列は値を残さない (直したことと見出しだけ)。空は空のまま。"""
    from seizou.core import history

    if text and name in (getattr(obj.__class__, "history_hidden_values", ()) or ()):
        return history.HIDDEN_VALUE
    return text


def record_history(obj, user, *, before=None, by="", changes=None):
    """変更の履歴 (ChangeLog) に obj の 1 行を書き、書いた行を返す (書かなければ None)。

    before が None なら「作った」(空でない値を後に)。before (書く前に取った core.history.saved_state(obj)) があれば、
    今の saved_state と比べ、変わった項目が無ければ何も書かない。使用中 (active) が真 → 偽は「使用停止にした」、偽 → 真は
    「使用を再開した」、状態 (status) が "cancelled" になったら「取り消した」、ほかは「直した」。
    changes を渡せば比べずにその並びを「直した」で書く (列でない物 = 標準の工程の並び・設定の値。空の並びなら書かない)。
    呼び手は書く前に before を取り、書いて拡張の表も書いた後に、同じ transaction.atomic の中で呼ぶ (止まって戻ったら履歴も残らない)。
    人の無い処理 (受け口) は user が None で、by に人の字 (例「<取引先の名前> の seizou」) を渡す。
    訳: 誰が・いつ・どの項目を・前の値 → 後の値に直したかを、マスターと伝票の全部で 1 つの表に残すため。出所 = 持ち主の決め
    と作者の決め、送り合ったデータと伝票の直し・消しの記録を残す電子帳簿保存法の考え (確かめた資料 =
    中小企業共通 EDI のガイドブック 3.2.2 と EDI インボイス仕様編 6.3.2。法令の本文と国税庁の資料は未読なので、どの表が法律の
    記録に当たるかは未確認)。
    変えてよい条件 = 残す列を減らすときは、その列が法律の記録に当たらないかを確かめてから。
    """
    from seizou.core import history

    if changes is not None:
        if not changes:
            return None
        return _write_history(obj, user, ChangeLog.ACTION_UPDATED, changes, by=by)
    after = history.saved_state(obj)
    if before is None:
        items = [
            {"field": name, "label": s["label"], "type": s["type"], "before": "", "after": _hidden_value(obj, name, s["value"])}
            for name, s in after.items()
            if s["value"] != ""
        ]
        return _write_history(obj, user, ChangeLog.ACTION_CREATED, items, by=by)
    items = []
    for name, state in after.items():
        old = before.get(name, {"value": "", "code": ""})
        if old.get("value", "") == state["value"] and old.get("code", "") == state.get("code", ""):
            continue
        items.append(
            {
                "field": name,
                "label": state["label"],
                "type": state["type"],
                "before": _hidden_value(obj, name, old.get("value", "")),
                "after": _hidden_value(obj, name, state["value"]),
            }
        )
    if not items:
        return None
    action = ChangeLog.ACTION_UPDATED
    changed = {item["field"] for item in items}
    if "active" in changed and after["active"]["value"] in ("true", "false"):
        action = ChangeLog.ACTION_RESUMED if after["active"]["value"] == "true" else ChangeLog.ACTION_STOPPED
    elif "status" in changed and after["status"].get("code") == "cancelled":
        action = ChangeLog.ACTION_CANCELLED
    return _write_history(obj, user, action, items, by=by)


def record_history_deleted(obj, user, *, by=""):
    """obj を消す前に呼び、その時の値 (空でない物) を前に残して「消した」の 1 行を書く。書いた行を返す。

    行の字 (object_text) はその時の str(obj) なので、行が表から消えた後も、どの行を消したかを読める。
    訳: 消した事も、誰が・いつ・何を消したかを残すため。出所 = 持ち主の決め (作者の決めの「作った・消した (使用停止) も残す」)。
    変えてよい条件 = record_history と同じ (残す列を減らすときは、その列が法律の記録に当たらないかを確かめてから)。
    """
    from seizou.core import history

    items = [
        {"field": name, "label": s["label"], "type": s["type"], "before": _hidden_value(obj, name, s["value"]), "after": ""}
        for name, s in history.saved_state(obj).items()
        if s["value"] != ""
    ]
    return _write_history(obj, user, ChangeLog.ACTION_DELETED, items, by=by)


# ---- 上の帯の知らせの鈴 (core.bell が読む。受け渡しの seizou-部品の6案-20261107/指示書.md の 4) ----


def bell_opened_at(user):
    """鈴を最後に開いた日時。行が無ければ登録した日時 (date_joined = それより前の知らせは数えない)。pk の無い利用者 (未ログイン) は None。読むだけ。"""
    user_pk = getattr(user, "pk", None)
    if user_pk is None:
        return None
    row = BellOpened.objects.filter(user_id=user_pk).only("opened_at").first()
    return row.opened_at if row is not None else getattr(user, "date_joined", None)


def open_bell(user):
    """鈴を開いた日時を今にし、その行を返す (本人の行だけ。無ければ作る)。

    訳: 鈴は「見たら印が消える」物 (本人の決め 11-07 18:4x)。開いた日時を 1 人 1 行で持ち、それより後の知らせだけを赤い数にする。
    知らせの 1 件ごとの既読は持たない (表を増やさず、数え方を 1 つにするため)。
    """
    with transaction.atomic():
        row, _created = BellOpened.objects.update_or_create(user=user, defaults={"opened_at": timezone.now()})
    return row


__all__ = ["bell_opened_at", "clear_setting", "company", "company_icons", "company_or_none", "next_number", "open_bell", "record_history", "record_history_deleted", "set_setting", "set_user_preference", "transaction", "update_company", "user_preference", "validate_setting"]
