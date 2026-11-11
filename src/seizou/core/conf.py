"""選べる設定 (選べる設定の設計の決め)。

読む順は 3 段: 画面の上書き (表 SettingOverride。設定の画面から保存した値) → 会社の settings.py の辞書 SEIZOU →
既定 (DEFAULTS)。鍵は DEFAULTS のとおり。

画面の上書きは process の中に控え (dict) を持つ。書いた時 (core の services の set_setting・clear_setting) に捨て、
別の process (gunicorn の複数の worker) の書いた値は CACHE_SECONDS 秒で読み直す。表が読めないとき
(migrate の前の置き場・DB を止めた試験) は上書き無しと同じで、控えにも入れない。SEIZOU と既定は控えずに毎回読む。
表の読みは savepoint (transaction.atomic) で包み、読めない読みが外の transaction (services の atomic の中) を壊さないようにする。
"""

import os
import re
import time

from django.apps import apps
from django.conf import settings
from django.db import DatabaseError, transaction

DEFAULTS = {
    "order_form": "multi",  # 受注の形: multi (1 件に明細が複数) / single (1 件 1 品)
    "partial_shipment": True,  # 分納を許す
    "over_shipment": "warn",  # 受注より多い出荷のポカよけ (R01・R04): warn / stop / off
    "over_workorder": "warn",  # 受注より多い指示書の数量と予定より多い実績 (R06・R07): warn / stop / off
    "receiving_required": "off",  # 支給品と預かり品の入荷 (引当) が無い受注の明細の指示書の発行と出荷: warn / stop / off
    "over_payment": "warn",  # 請求書の残を超える入金のポカよけ (R24): warn / stop / off
    "progress_staff": "warn",  # 作業した人の無い実績のポカよけ (R26): warn / stop / off
    "billed_shipment": "warn",  # 請求済みの出荷の直しと削除のポカよけ (R27): warn / stop / off
    "order_number": "seq",  # 受注番号の形: seq (連番) / ym_seq (年月 + 連番)
    "transcription_minutes_per_line": 1,  # 1 行の手入力にかかる分 (ダッシュボードの「手入力せずに取り込んだ明細」の入力を省けた時間 ≒ m 分。答えの YAML の settings で変える)
    "hidden_fields": [],  # 使わない注文の項目 (決まりが見る項目は隠せない)
    "production_plan": True,  # 生産計画の画面 (メニューの「生産計画」と工程ごとの盤) を使う
    "weekly_holidays": [5, 6],  # 定休日の曜日 (0 = 月 〜 6 = 日)。既定は土日。休日の表 (masters の Holiday) と合わせて稼働日を決める (core.calendar)
    "access_control": {"department": "log", "write": "log", "money": "log"},  # 権限の判定: 部署・編集の権限・金額の閲覧のそれぞれ off / log / on
    "assistant_model": "claude-sonnet-5-5",  # AI チャットが使う model (会社の鍵で呼ぶ)
    "intake_model": "claude-opus-5-5",  # 注文書の読み取りが使う model
    "feedback_model": "claude-haiku-5-5",  # フィードバックの AI の聞き取りが使う model (安い model)
    "menu": [],  # 左のメニューの括り (label・apps・screens の並び)。空なら既定の並び (core.context_processors.SECTIONS)
    "brand_color": "",  # 画面の主色 (# と 16 進の 6 桁。空なら土台の色。2 社を並べたときに見分ける)
    "industry": "",  # 業種 (seizou new が答えの YAML の industry を写す。左上の会社の名前の下に出す。空なら seizou)
    "mail": {"host": "", "port": 587, "security": "starttls", "user": "", "from": "", "from_name": "", "site_url": ""},  # 知らせのメールの送り方 (サーバー・ポート・暗号化の方式・アカウント・差出人・差出人の名前・seizou の URL の頭)。パスワードは持たない (.env の EMAIL_HOST_PASSWORD)
    "session_idle_minutes": 120,  # 操作しない間のログインの時間切れ (分)。答えの YAML の settings で変える
    "session_max_hours": 12,  # ログインからの最長の時間 (時間)。答えの YAML の settings で変える
    "ai_limits": {"concurrent": 2, "per_hour": 30, "watch_per_day": 200},  # AI の呼び出しの上限 (process ごとの同時の数・人ごと機能ごとの 1 時間の回数・注文書の見張りの 1 日の枚数)
    "ai_features": {"assistant": True, "intake": True, "feedback": True},  # AI を使う機能ごとの使う / 止める
    "site_notice": "",  # 全部の画面の上に出す知らせの帯の字 (200 字まで。空なら出さない。答えの YAML の settings で入れる。core/_site_notice_band.html)
}

#: 知らせのメールの送り方 (mail) の中の鍵の並び (設定の画面の表の塊と、設定の変更の画面の欄の並び)
MAIL_KEYS = ("host", "port", "security", "user", "from", "from_name", "site_url")
#: 知らせのメールの暗号化の方式 (starttls = つないだ後に暗号化 / ssl = 初めから暗号化 / none = 暗号化しない)
MAIL_SECURITY = ("starttls", "ssl", "none")
#: 知らせのメールの送り方のうち、置き場の .env (環境変数) だけで決める物 (サーバー・ポート・暗号化の方式) と、環境変数の名前。
#: 画面の上書きと答えの YAML の値は読まない (.env のパスワード EMAIL_HOST_PASSWORD を渡す先を、画面から変えられないように)
MAIL_ENV = {"host": "EMAIL_HOST", "port": "EMAIL_PORT", "security": "EMAIL_SECURITY"}

CHOICES = {
    "order_form": ("multi", "single"),
    "over_shipment": ("warn", "stop", "off"),
    "over_workorder": ("warn", "stop", "off"),
    "receiving_required": ("warn", "stop", "off"),
    "over_payment": ("warn", "stop", "off"),
    "progress_staff": ("warn", "stop", "off"),
    "billed_shipment": ("warn", "stop", "off"),
    "order_number": ("seq", "ym_seq"),
}

#: 画面の上書きの控えの寿命 (秒)。別の process (gunicorn の複数の worker) が、ほかの worker の書いた上書きを読み直すまでの長さ
CACHE_SECONDS = 30
#: 画面からは変えない鍵 (形が深いので答えの YAML で書く。services と設定の画面が読む)
LOCKED_KEYS = ("menu", "weekly_holidays", "transcription_minutes_per_line", "session_idle_minutes", "session_max_hours", "ai_limits", "ai_features", "site_notice")
#: 値の出所の字 (設定の画面の「どこで決めたか」の列・設定の変更の画面の頭の行・MCP と AI チャットの company_info の sources が出す)。
#: 事務の人が読める画面の言葉にし、作る人向けの字 (settings.py・YAML) を出さない (作者の決め)。
#: 順に、画面の上書き (SettingOverride)・会社の settings.py の SEIZOU (導入の時に答えの YAML から作る)・DEFAULTS
SOURCE_SCREEN = "この画面で変えた"
SOURCE_ANSWER = "導入の時に決めた"
SOURCE_DEFAULT = "初期値"

# 画面の上書きの控え。(上書きの辞書, 読んだ時刻) の組か None。
# 組を 1 つの名前に入れる (辞書と時刻を 2 つの名前に分けると、別の thread が片方だけ新しい物を見るため)。
_cache = None


def _now() -> float:
    """控えの時計。試験は monkeypatch でこの関数を差し替えて時計を進める (time.monotonic は Django の接続も読むため)。"""
    return time.monotonic()


def reset_cache() -> None:
    """控えを捨てる。次に読む時に表を読み直す (services の set_setting・clear_setting と試験が呼ぶ)。"""
    global _cache
    _cache = None


def overrides() -> dict:
    """画面の上書きの全部 {鍵: 値} (読むだけ。中身を変えない)。

    控えが CACHE_SECONDS 秒より新しければ控えを返す。古いか無ければ表を 1 回読んで控える。
    表が読めないときは {} を返し、控えに入れない (migrate の後に読み直せるように)。読めないのは、
    app の登録の前・表の無い置き場 (DatabaseError)・pytest-django が db の無い試験で DB を止めたとき (RuntimeError)。
    表の読みは savepoint (transaction.atomic) の中 (PostgreSQL で失敗した問い合わせが外の transaction を壊さないように)。
    """
    global _cache
    cached = _cache
    now = _now()
    if cached is not None and now - cached[1] < CACHE_SECONDS:
        return cached[0]
    if not apps.ready:
        return {}
    try:
        model = apps.get_model("core", "SettingOverride")
    except LookupError:
        return {}
    try:
        # queryset は遅延なので、dict(...) で読み切るまでを try の中に置く。
        # savepoint (transaction.atomic) で包む: PostgreSQL では失敗した問い合わせ (migrate の前は表が無い) が外の transaction を
        # 壊し、以後の問い合わせが全部落ちる。services.next_number などは atomic の中から setting() を読むので、外に響かないようにする。
        # with も try の中に置く: db の無い試験では atomic の入口 (get_autocommit → ensure_connection) が RuntimeError を出す。
        with transaction.atomic():
            values = dict(model._default_manager.values_list("key", "value"))
    except (DatabaseError, RuntimeError):
        return {}
    _cache = (values, now)
    return values


def setting(key: str):
    """選べる設定の値。画面の上書き → SEIZOU → 既定の順に読む。知らない鍵は KeyError。"""
    if key not in DEFAULTS:
        raise KeyError(f"知らない設定の鍵: {key}")
    saved = overrides()
    if key in saved:
        return saved[key]
    values = getattr(settings, "SEIZOU", {}) or {}
    return values.get(key, DEFAULTS[key])


def setting_source(key: str) -> str:
    """値の出所の字 (SOURCE_SCREEN・SOURCE_ANSWER・SOURCE_DEFAULT のどれか)。知らない鍵は KeyError。"""
    if key not in DEFAULTS:
        raise KeyError(f"知らない設定の鍵: {key}")
    if key in overrides():
        return SOURCE_SCREEN
    values = getattr(settings, "SEIZOU", {}) or {}
    return SOURCE_ANSWER if key in values else SOURCE_DEFAULT


#: 権限の判定の状態 (off = 使わない / log = 記録だけ / on = 有効。seizou.core.access)
ACCESS_MODES = ("off", "log", "on")


def access_control(kind: str) -> str:
    """権限の判定 kind (department / write / money) の状態。

    access_control が辞書でなければ既定、鍵が無ければ log。真偽の True は on・False は off
    (YAML 1.1 は引用符の無い on と off を真偽で読む)。ACCESS_MODES の外の値は log。知らない kind は KeyError。
    """
    defaults = DEFAULTS["access_control"]
    if kind not in defaults:
        raise KeyError(f"知らない権限の判定: {kind}")
    values = setting("access_control")
    if not isinstance(values, dict):
        values = defaults
    value = values.get(kind, "log")
    if value is True:
        return "on"
    if value is False:
        return "off"
    return value if isinstance(value, str) and value in ACCESS_MODES else "log"



def mail_settings() -> dict:
    """知らせのメールの送り方 (mail) を、MAIL_KEYS の順の新しい辞書で返す (読むだけ。例外を投げない)。

    mail が辞書でなければ既定で埋める。辞書なら、無い中の鍵と字でない値を既定で埋める。字は前後の空白を落とす。値を確かめて保存するのは services の validate_setting で、
    ここは答えの YAML の書き損じで画面と知らせを 500 にしないための読み方 (access_control() と同じ考え)。
    パスワードは持たない (core.mail が .env の EMAIL_HOST_PASSWORD を読む)。
    サーバー・ポート・暗号化の方式 (MAIL_ENV の鍵) は、画面の上書きと SEIZOU を読まず、mail_env_value で環境変数だけから読む。
    """
    defaults = DEFAULTS["mail"]
    values = setting("mail")
    if not isinstance(values, dict):
        values = {}
    result = {}
    for key in MAIL_KEYS:
        if key in MAIL_ENV:
            result[key] = mail_env_value(key)
            continue
        value = values.get(key, defaults[key])
        result[key] = value.strip() if isinstance(value, str) else defaults[key]
    return result


def mail_env_value(key: str):
    """知らせのメールのサーバー・ポート・暗号化の方式 (MAIL_ENV の鍵) を、環境変数 (置き場の .env の行) だけから読む (例外を投げない)。

    サーバーは前後の空白を落とした字 (無ければ空の字 = 送らない)。ポートは 1〜65535 の整数の字なら整数、ほかは既定の 587。
    暗号化の方式は MAIL_SECURITY のどれか (大小を問わない)、ほかは既定の starttls。
    訳: パスワード (.env の EMAIL_HOST_PASSWORD) を渡す先を、.env を書ける人 (置き場を預かる人) だけが決めるため。
    画面から変えられると、アカウント管理の権限の人が自分のサーバーを入れて「試しに送る」を押すだけで、パスワードを受け取れる。
    出所 = Claude Security の走査の指摘 (11-07)。
    変えてよい条件 = 画面で送り先を変えられる人を、.env を書ける人と同じ人 (システム管理者) に限ると決めた時。
    """
    default = DEFAULTS["mail"][key]
    text = os.environ.get(MAIL_ENV[key], "").strip()
    if key == "port":
        return int(text) if text.isascii() and text.isdigit() and 1 <= int(text) <= 65535 else default
    if key == "security":
        return text.lower() if text.lower() in MAIL_SECURITY else default
    return text


def ai_limits() -> dict:
    """AI の呼び出しの上限 (ai_limits) を、DEFAULTS の鍵の順の新しい辞書で返す (読むだけ。例外を投げない)。

    鍵は concurrent (process ごとの同時の数)・per_hour (人ごと機能ごとの 1 時間の回数)・watch_per_day (注文書の見張りの 1 日の枚数)。
    答えの値を既定に重ね、正の整数でない値 (字・0 と負の数・真偽) と無い鍵は既定にする。ai_limits が辞書でなければ全部が既定。
    答えの YAML の書き損じで画面を 500 にしないための読み方 (access_control() と同じ考え)。画面からは変えない (LOCKED_KEYS)。
    """
    defaults = DEFAULTS["ai_limits"]
    values = setting("ai_limits")
    if not isinstance(values, dict):
        values = {}
    result = {}
    for key, default in defaults.items():
        value = values.get(key, default)
        result[key] = value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default
    return result


def ai_feature(name: str) -> bool:
    """AI を使う機能 name (assistant / intake / feedback) を使うか (ai_features。読むだけ)。

    ai_features が辞書でなければ既定、鍵が無いか真偽でない値も既定 (使う)。知らない name は KeyError。
    画面からは変えない (LOCKED_KEYS)。
    """
    defaults = DEFAULTS["ai_features"]
    if name not in defaults:
        raise KeyError(f"知らない AI の機能: {name}")
    values = setting("ai_features")
    if not isinstance(values, dict):
        values = {}
    value = values.get(name, defaults[name])
    return value if isinstance(value, bool) else defaults[name]


def company_id() -> str:
    """会社の id (組み立ての命令が SEIZOU に書く)。試験では空。"""
    values = getattr(settings, "SEIZOU", {}) or {}
    return values.get("id", "")


#: 画面の主色 (brand_color) の形。# と 16 進の 6 桁 (fullmatch で見る。services の validate_setting も使う)
BRAND_COLOR_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}")


def brand_color() -> str:
    """画面の主色 (brand_color) を、# と 16 進の 6 桁の小文字で返す。形の違う値と空は空の字 (土台の色)。

    答えの YAML の書き損じと画面の値を、base.html の style に入れる前にここで止める。
    """
    value = setting("brand_color")
    if isinstance(value, str) and BRAND_COLOR_PATTERN.fullmatch(value):
        return value.lower()
    return ""


#: 知らせの帯 (site_notice) の字数の上限 (前後の空白を落とした後の字数)
SITE_NOTICE_MAX = 200


def check_site_notice(value) -> str:
    """知らせの帯 (site_notice) の値を確かめて、前後の空白を落とした字を返す。

    字でない値 (YAML の数・真偽・空の値の None) と、SITE_NOTICE_MAX 字を超える字は日本語の ValueError で断る。
    文は services の validate_setting の断りの文と同じ形 (「保存できません。」で始め、直し方と空のときの動きを書く)。
    入れた値は文に入れず、字数だけを入れる (長い字で文が読めなくならないように)。
    今は site_notice() だけが呼ぶ (鍵は LOCKED_KEYS にあり、設定の画面からは保存しない)。
    訳: 何のために 200 字で切るかは、依頼書に書かれておらず未確認。出所 = 依頼書 N398 (11-11。200 字の根拠は未確認)。
    変えてよい条件 = 帯にもっと長い字を出したい会社が出たとき、または本人が上限を決め直したとき。
    """
    if not isinstance(value, str):
        raise ValueError(f"保存できません。site_notice (知らせの帯) は字にしてください。空なら帯を出しません (入れた値の種類: {type(value).__name__})。")
    text = value.strip()
    if len(text) > SITE_NOTICE_MAX:
        raise ValueError(f"保存できません。site_notice (知らせの帯) は {SITE_NOTICE_MAX} 字までの字にしてください。空なら帯を出しません (入れた字数: {len(text)})。")
    return text


def site_notice() -> str:
    """全部の画面の上に出す知らせの帯の字 (site_notice。読むだけ。例外を投げない)。空なら帯を出さない。

    値は check_site_notice で確かめ、断られた値 (字でない値と SITE_NOTICE_MAX 字を超える字) は空の字 (帯を出さない) にする。
    全部の画面の描画 (core.context_processors.seizou) が読むので、答えの YAML の書き損じで全部の画面を 500 にしない (brand_color() と同じ考え)。
    """
    try:
        return check_site_notice(setting("site_notice"))
    except ValueError:
        return ""


def session_limits() -> tuple[int, int]:
    """ログインの時間切れの 2 つの時間を (操作しない間の秒, ログインからの最長の秒) で返す (読むだけ。例外を投げない)。

    値は session_idle_minutes (分) と session_max_hours (時間) を setting() で読む。真偽 (True / False)・整数でない値・0 以下は
    既定 (DEFAULTS) に戻す (access_control() と mail_settings() と同じ考え。答えの YAML の書き損じで全部の画面を 500 にしない)。
    core.middleware.SessionTimeoutMiddleware と、ログインの画面 (core.views.RememberLoginView) が読む。
    訳: 共用の PC が多い町工場の事務所で、離れた席の開いたままの画面から伝票を直されないため。既定は審査と現場で切れすぎない緩めの値
    (操作しない間 2 時間・ログインから最長 12 時間) にし、会社が答えの YAML で縮められる。出所 = 本人の決め 11-08 00:40 (エンタープライズ化の調べを受けた作者の決め)。
    変えてよい条件 = 本人が決め直したとき、または会社が設定 (session_idle_minutes・session_max_hours) で変えたとき。
    """

    def whole(key: str) -> int:
        value = setting(key)
        # 真偽は int の仲間なので先に外す (True を 1 分と読まない)。
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return DEFAULTS[key]
        return value

    return whole("session_idle_minutes") * 60, whole("session_max_hours") * 60 * 60
