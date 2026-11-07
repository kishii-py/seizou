"""AI (Claude の API) の呼び方の共通の部分。assistant・intake・feedback が使う。

- 鍵は、呼ぶ側が名前を渡す環境変数からだけ読み、client に明示して渡す (ほかの資格に落ちる道を作らない)。
  鍵の値を画面・ログ・例外の文・返す物に出さない。
- SDK (anthropic) は関数の中で import する (SDK が無くても、この module の import は落ちない)。
- 待ちの上限は呼ぶ側が渡す。再試行はしない。
- SDK の例外は、呼ぶ側が渡した表の日本語の文で ValueError に替える。文は部品ごとに違うので、この module は文を持たない。
- API を呼ぶ関数 (部品ごとの _ask_claude) は部品に残す。試験はその関数を作り物の返事に差し替える。
- 上限 (同時の数・人ごと機能ごとの 1 時間の回数・1 つの質問の締め切り・注文書の見張りの 1 日の枚数) と使った記録はここの 1 か所 (guard・has_time・record_use・take_watch_call)。上限の断りの字と、model が使えないときの字だけは、機能の名前と設定の鍵を入れるのでここが持つ。BoundedSemaphore は 1 つの process の中だけに効く (worker を増やすと worker ごとに数える)。
"""

from __future__ import annotations

import contextvars
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.core.cache.backends.filebased import FileBasedCache
from django.core.cache.backends.locmem import LocMemCache
from django.db import DatabaseError
from django.utils import timezone

from seizou.core import conf
from seizou.core import services as core_services


def company_name() -> str:
    """system に入れる会社名。会社の行が読めないとき (DatabaseError) は "seizou"。"""
    try:
        return core_services.company().name
    except DatabaseError:
        return "seizou"


def has_key(api_key_env) -> bool:
    """鍵の環境変数 api_key_env に空でない値があるか。値そのものは返さない。"""
    return bool(os.environ.get(api_key_env, "").strip())


def model_for(setting_key, default) -> str:
    """使う model の名前。会社の設定 setting_key (core の conf.DEFAULTS の鍵) が空でない字なら前後の空白を落とした字、ほかは default。"""
    value = conf.setting(setting_key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def client(api_key_env, timeout):
    """環境変数 api_key_env の鍵で SDK の client を作って返す。待ちは timeout 秒で、再試行しない。

    鍵の環境変数が無ければ KeyError (呼ぶ側が先に鍵の有無を見る)。
    """
    import anthropic

    return anthropic.Anthropic(api_key=os.environ[api_key_env], timeout=timeout, max_retries=0)


@contextmanager
def translate_errors(messages, *, model=""):
    """with の中で起きた SDK の例外を、messages の文の ValueError に替える。ほかの例外はそのまま通す。

    messages の鍵は 5 つ。bad_key (鍵が合わない)・busy (混んでいる)・no_credit (状態 402 = 残高が足りない)・
    unreachable (届かない。402 の外の状態の番号は「文 (状態 番号)」にする)・timeout (待ちの上限を超えた)。
    元の例外は from None で切る (返事の本文と鍵の値を文に入れない)。
    状態 404 は、model (呼んだ model の名前) を渡し、guard の中で呼んだときだけ、設定の鍵と model の名前を入れた文にする
    (_model_missing。model の ID の廃止を「少し待って」の文にしない)。model を渡さないか guard の外なら、今までの「文 (状態 404)」。
    """
    import anthropic

    # 順に意味がある。SDK では AuthenticationError と RateLimitError が APIStatusError の子、
    # APITimeoutError が APIConnectionError の子なので、子を先に見る。
    try:
        yield
    except anthropic.AuthenticationError:
        raise ValueError(messages["bad_key"]) from None
    except anthropic.RateLimitError:
        raise ValueError(messages["busy"]) from None
    except anthropic.APIStatusError as exc:
        if exc.status_code == 402:
            raise ValueError(messages["no_credit"]) from None
        missing = _model_missing(exc.status_code, model)
        if missing:
            raise ValueError(missing) from None
        raise ValueError(f"{messages['unreachable']} (状態 {exc.status_code})") from None
    except anthropic.APITimeoutError:
        raise ValueError(messages["timeout"]) from None
    except anthropic.APIConnectionError:
        raise ValueError(messages["unreachable"]) from None


# ---- 上限と使った記録 ----

#: 1 つの質問の合計の締め切り (秒)。gunicorn の --timeout 90 と nginx の proxy_read_timeout 90s より短い
DEADLINE_SECONDS = 75
#: 1 回の呼び出しの待ち (秒。各部品の TIMEOUT_SECONDS と同じ)。締め切りまでの残りがこれより短ければ、次の回を始めない
ROUND_SECONDS = 60
#: 人ごと・機能ごとの回数を数える窓の長さ (秒。その人のその機能の最初の 1 回から数える)
HOUR_SECONDS = 3600
#: 見張りの 1 日の数を残す長さ (秒。鍵に日付が入るので、日が替われば新しい鍵で 0 から数える)
DAY_KEEP_SECONDS = 2 * 24 * 3600
#: 回数の数のファイルを置く場所を書く設定 (settings.py)。無ければ BASE_DIR / "cache" / "ai"
CACHE_DIR_SETTING = "SEIZOU_AI_CACHE_DIR"
#: cache の鍵の頭
CACHE_PREFIX = "seizou:ai:"

#: AI を使う機能の名前 → (画面に出す名前, model を決める設定の鍵)。名前は conf.DEFAULTS の ai_features の鍵と同じ
FEATURES = {
    "assistant": ("AI チャット", "assistant_model"),
    "intake": ("注文書の読み取り", "intake_model"),
    "feedback": ("フィードバックの AI の聞き取り", "feedback_model"),
}

logger = logging.getLogger("seizou.ai")

# guard の中の機能の名前とログイン ID (record_use と 404 の文が読む。guard の外は空の字)
_feature = contextvars.ContextVar("seizou_ai_feature", default="")
_login = contextvars.ContextVar("seizou_ai_login", default="")

# process ごとの同時の数 (最初に guard を通る時に conf.ai_limits() の concurrent の数で作る。数を変えたら起動し直す)
_slots = None
_slots_lock = threading.Lock()


class LimitReached(ValueError):
    """上限で AI を呼ばなかった (guard の入口)。ValueError の子なので、読み取りの画面と見張りは今の断りと同じに扱う。"""


def _now() -> float:
    """締め切りの時計。試験は monkeypatch でこの関数を差し替えて時計を進める (conf._now と同じ形)。"""
    return time.monotonic()


def clock() -> float:
    """1 つの質問を始めた時刻。has_time に渡す。"""
    return _now()


def has_time(started) -> bool:
    """clock() で取った started から、次の 1 回の呼び出しを始めてよいか (締め切りまでの残りが ROUND_SECONDS 秒以上か)。

    各回の待ちは今の 60 秒のままなので、残りが 60 秒より短ければ次の回を始めない (合計は DEADLINE_SECONDS の 75 秒の内)。
    訳: 1 つの質問の待ちの合計を 75 秒の内に収め、nginx と gunicorn の 90 秒で画面が誤りになる前に答えを返し、thread を長くふさがないため。出所 = エンタープライズ化の調べ 第一弾の候補 18 の確かめ直しの直し方の直し (a) (11-07)。変えてよい条件 = gunicorn の --timeout と nginx の proxy_read_timeout を変えたとき、または各部品の 1 回の待ち (TIMEOUT_SECONDS) を変えたとき。
    """
    return DEADLINE_SECONDS - (_now() - started) >= ROUND_SECONDS


def feature_enabled(name) -> bool:
    """AI を使う機能 name (FEATURES の鍵) を会社が使うか (設定 ai_features)。"""
    return conf.ai_feature(name)


def _make_cache():
    """回数の数の置き場。SEIZOU_AI_CACHE_DIR か置き場の cache/ai/ のファイル (worker と process の全部で 1 か所)。BASE_DIR も無ければ process の記憶。"""
    location = getattr(settings, CACHE_DIR_SETTING, None)
    if not location:
        base = getattr(settings, "BASE_DIR", None)
        location = Path(base) / "cache" / "ai" if base else None
    if location:
        return FileBasedCache(str(location), {"TIMEOUT": HOUR_SECONDS})
    return LocMemCache("seizou-ai", {"TIMEOUT": HOUR_SECONDS})


cache = _make_cache()


def _semaphore():
    global _slots
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(conf.ai_limits()["concurrent"])
        return _slots


def reset_limits() -> None:
    """回数の数を全部捨て、同時の数の semaphore を作り直す (次に guard を通る時に設定から作る)。試験が試験ごとに呼ぶ。"""
    global _slots
    with _slots_lock:
        _slots = None
    cache.clear()


def _user_key(user) -> str:
    """回数の鍵に使う利用者の印 (pk)。利用者が無い呼び (pk が None) は 1 つの "-" にまとめて数える。"""
    pk = getattr(user, "pk", None)
    return "-" if pk is None else str(pk)


def _login_id(user) -> str:
    getter = getattr(user, "get_username", None)
    return str(getter() or "") if callable(getter) else ""


def _hour_window(value, now) -> dict:
    """1 時間の窓 {"n": 回数, "until": 窓の終わりの時刻 (time.time() の秒)}。無い・形が違う・終わった窓は、now から始まる 0 回の窓。"""
    if isinstance(value, dict):
        count = value.get("n")
        until = value.get("until")
        if isinstance(count, int) and isinstance(until, (int, float)) and until > now:
            return {"n": count, "until": until}
    return {"n": 0, "until": now + HOUR_SECONDS}


@contextmanager
def guard(feature, user, *, busy, hourly=True):
    """with で入ると、上限の中なら AI を呼んでよい。入れなければ API を呼ばずに LimitReached (ValueError の子) を出す。

    入る時に見る順: (1) hourly が真なら、その人のその機能の 1 時間の回数が設定 ai_limits の per_hour に達していないか
    (窓は最初の 1 回から HOUR_SECONDS 秒)。(2) process ごとの同時の数 (concurrent) の semaphore を待たずに取れるか。取れなければ文は busy
    (呼ぶ側の今の混み合いの字)。(3) 取れたら 1 時間の回数を 1 足し、機能の名前とログイン ID を record_use と 404 の文のために控える。
    抜ける時に semaphore を返す (例外のときも)。1 つの質問 (道具の往復で API を何回呼んでも) を 1 回と数える。
    回数は置き場の cache/ai/ のファイルの読み書きなので厳密でなく目安 (同時に入った worker の間で 1〜2 回ずれうる)。
    BoundedSemaphore は 1 つの process の中だけに効く (worker を増やすと worker ごとに数える)。
    訳: AI の呼び出しは process ごとに同時 2 まで (gunicorn の 4 本の thread のうち 2 本を画面に残す)、人ごと・機能ごとに 1 時間 30 回まで (1 つの質問を 1 回と数える)。既定は緩めにし、会社が答えの YAML の ai_limits で下げる。審査員が AI チャットを続けて試しても断られず、長い待ちで会社の画面の全部が止まらないため。出所 = 本人の決め 11-08 00:29 (同時 2・1 時間 30 回・見張り 1 日 200 枚 (おすすめ))。変えてよい条件 = 本人が決め直したとき、または会社が設定で変えたとき。
    """
    label, _model_key = FEATURES[feature]
    limits = conf.ai_limits()
    key = f"{CACHE_PREFIX}hour:{feature}:{_user_key(user)}"
    now = time.time()
    window = _hour_window(cache.get(key), now) if hourly else None
    if window is not None and window["n"] >= limits["per_hour"]:
        raise LimitReached(
            f"{label}は 1 人 1 時間に {limits['per_hour']} 回までです。時間をおいてからもう一度試してください。"
        )
    slots = _semaphore()
    if not slots.acquire(blocking=False):
        raise LimitReached(busy)
    try:
        if window is not None:
            cache.set(key, {"n": window["n"] + 1, "until": window["until"]}, max(1, int(window["until"] - now) + 1))
        feature_token = _feature.set(feature)
        login_token = _login.set(_login_id(user))
        try:
            yield
        finally:
            _login.reset(login_token)
            _feature.reset(feature_token)
    finally:
        slots.release()


def _model_missing(status_code, model) -> str:
    """状態 404 で model の名前が渡され、guard の中で呼んだときの文 (設定の鍵と model の名前を入れる)。ほかは空の字。"""
    feature = _feature.get()
    if status_code != 404 or not model or feature not in FEATURES:
        return ""
    setting_key = FEATURES[feature][1]
    return f"設定 {setting_key} の model「{model}」が使えません。設定の画面で、使える model の名前に直してください。"


def _plain(owner, name):
    value = getattr(owner, name, None)
    return "" if value is None else value


def record_use(model, response) -> None:
    """AI を 1 回呼んだ記録を、記録 seizou.ai に INFO で 1 行出す (部品の _ask_claude が返事を受けた直後に呼ぶ)。

    出すのはログイン ID と機能の名前 (guard の中の控え。外なら空)・model・入力と出力の token の数 (response.usage。無ければ空)・
    stop_reason・request-id (SDK の返事の _request_id)。質問の字・注文書の字・返事の本文・鍵の値は出さない。
    """
    usage = getattr(response, "usage", None)
    logger.info(
        "AI を呼んだ: ログイン ID %s・機能 %s・model %s・入力 token %s・出力 token %s・stop_reason %s・request-id %s",
        _login.get(),
        _feature.get(),
        model,
        _plain(usage, "input_tokens"),
        _plain(usage, "output_tokens"),
        _plain(response, "stop_reason"),
        _plain(response, "_request_id"),
    )


def take_watch_call() -> bool:
    """注文書の見張りの今日 (timezone.localdate()) の枚数を 1 足して真を返す。設定 ai_limits の watch_per_day に達していれば、足さずに偽。

    見張り (intake の extract_to_inbox) が、同じ中身の確かめの後、読み取りに回す前に 1 枚ごとに呼ぶ
    (同じ中身で API を呼ばない物は数えない。形式・大きさ・鍵で API の前に断られる物は 1 枚と数える)。
    数は置き場の cache/ai/ のファイルで、日付ごとの鍵に持つ (目安。同時に動く見張りが 2 つあると 1〜2 枚ずれうる)。
    訳: 注文書の見張りの費用に 1 日の上限を付けるため。API を呼ぶ回数で数え、超えた物は読まずに置いたまま (動かさず、次の日に読む)。出所 = 本人の決め 11-08 00:29 (見張り 1 日 200 枚) と、エンタープライズ化の調べ 第一弾の候補 18 の直し方の直し (d) (11-07)。変えてよい条件 = 本人が決め直したとき、または会社が設定 ai_limits の watch_per_day で変えたとき。
    """
    limit = conf.ai_limits()["watch_per_day"]
    key = f"{CACHE_PREFIX}watch-day:{timezone.localdate().isoformat()}"
    count = cache.get(key, 0)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        count = 0
    if count >= limit:
        return False
    cache.set(key, count + 1, DAY_KEEP_SECONDS)
    return True
