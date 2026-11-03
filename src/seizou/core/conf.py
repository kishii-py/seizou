"""選べる設定 (01 の 9 節と 10.5)。

読む順は 3 段: 画面の上書き (表 SettingOverride。設定の画面から保存した値) → 会社の settings.py の辞書 SEIZOU →
既定 (DEFAULTS)。鍵は DEFAULTS のとおり。

画面の上書きは process の中に控え (dict) を持つ。書いた時 (core の services の set_setting・clear_setting) に捨て、
別の process (gunicorn の複数の worker) の書いた値は CACHE_SECONDS 秒で読み直す。表が読めないとき
(migrate の前の置き場・DB を止めた試験) は上書き無しと同じで、控えにも入れない。SEIZOU と既定は控えずに毎回読む。
表の読みは savepoint (transaction.atomic) で包み、読めない読みが外の transaction (services の atomic の中) を壊さないようにする。
"""

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
    "order_number": "seq",  # 受注番号の形: seq (連番) / ym_seq (年月 + 連番)
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
}

CHOICES = {
    "order_form": ("multi", "single"),
    "over_shipment": ("warn", "stop", "off"),
    "over_workorder": ("warn", "stop", "off"),
    "order_number": ("seq", "ym_seq"),
}

#: 画面の上書きの控えの寿命 (秒)。別の process (gunicorn の複数の worker) が、ほかの worker の書いた上書きを読み直すまでの長さ
CACHE_SECONDS = 30
#: 画面からは変えない鍵 (形が深いので答えの YAML で書く。services と設定の画面が読む)
LOCKED_KEYS = ("menu", "weekly_holidays")
#: 値の出所の字 (設定の画面が出す)
SOURCE_SCREEN = "画面"
SOURCE_ANSWER = "答え (settings.py)"
SOURCE_DEFAULT = "既定"

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
