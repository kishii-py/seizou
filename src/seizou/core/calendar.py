"""稼働日の数え方 (読むだけ。表に書かない)。

稼働日 = 定休日の曜日 (設定 weekly_holidays。既定は土日) でも、休日の表 (masters の Holiday) の日でもない日。

関数の並び:
- weekly_holidays(): 定休日の曜日の集合 (0 = 月 〜 6 = 日)。形の違う値は既定に戻す (例外を投げない)。
- holiday_dates(): 休日の表の日付の集合。
- is_workday(day): 稼働日か。next_workday(day): day を含めて最初の稼働日。
- add_workdays(day, n): n 個先 (負なら前) の稼働日。workdays_between(start, end): 符号つきの稼働日の数。

どの関数も休日の集合を引数 holidays で受けられ、渡されなければ holiday_dates() を読む (まとめて数えたい呼び手と試験が渡す)。

holiday_dates() は core.conf の画面の上書きの控えと同じ形にする。process の中に (集合, 読んだ時刻) の控えを持ち、
CACHE_SECONDS 秒より新しければ控えを返す (1 回の要求の中で表を多くて 1 回だけ読む。注残の盤は行ごとに数えるため)。
書いた時 (masters の services の create_holiday・delete_holiday) に reset_cache() で捨て、別の process の書いた休日は
CACHE_SECONDS 秒で読み直す。表が読めないとき (app の登録の前・migrate の前・db の無い試験) は空の集合を返し、控えに入れない。

core はほかの部品を import しない (01 の 1 節)。休日の表は apps.get_model で名前から引き、masters が入っていない会社では
休日を空の集合として、定休日の曜日だけで数える。
"""

import time
from datetime import timedelta

from django.apps import apps
from django.db import DatabaseError, transaction

from seizou.core import conf

#: 控えの寿命 (秒)。設定の画面の上書きの控えと同じ長さ
CACHE_SECONDS = conf.CACHE_SECONDS
#: next_workday・add_workdays が探す日の上限 (10 年)
SEARCH_LIMIT_DAYS = 3660
_NOT_FOUND_MESSAGE = "稼働日が 10 年先まで見つかりません。休日の表と定休日の設定を見てください。"

# 休日の表の控え。(日付の集合, 読んだ時刻) の組か None (組を 1 つの名前に入れる理由は core.conf の _cache と同じ)。
_cache = None


def _now() -> float:
    """控えの時計。試験は monkeypatch でこの関数を差し替えて時計を進める。"""
    return time.monotonic()


def reset_cache() -> None:
    """控えを捨てる。次に読む時に表を読み直す (masters の services の create_holiday・delete_holiday と試験が呼ぶ)。"""
    global _cache
    _cache = None


def weekly_holidays() -> frozenset:
    """定休日の曜日の集合 (0 = 月 〜 6 = 日)。設定 weekly_holidays を読む。

    空の並びは「定休日なし」。並びでない・整数でない要素 (真偽を含む) がある・0〜6 の外の数がある・7 つの曜日を全部含む
    (稼働日が無くなる) のどれかなら、既定 (conf.DEFAULTS) に戻す。盤を 500 にしないよう、例外を投げない。
    """
    default = frozenset(conf.DEFAULTS["weekly_holidays"])
    value = conf.setting("weekly_holidays")
    if not isinstance(value, (list, tuple)):
        return default
    if any(isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6 for day in value):
        return default
    days = frozenset(value)
    if len(days) == 7:
        return default
    return days


def holiday_dates() -> frozenset:
    """休日の表の日付の全部 (読むだけ)。控えが CACHE_SECONDS 秒より新しければ控えを返す。

    古いか無ければ表を 1 回読んで控える。表が読めないとき (app の登録の前・masters の無い会社 (LookupError)・
    表の無い置き場 (DatabaseError)・db の無い試験 (RuntimeError)) は空の集合を返し、控えに入れない。
    表の読みは savepoint (transaction.atomic) の中 (失敗した問い合わせが外の transaction を壊さないように。core.conf と同じ)。
    """
    global _cache
    cached = _cache
    now = _now()
    if cached is not None and now - cached[1] < CACHE_SECONDS:
        return cached[0]
    if not apps.ready:
        return frozenset()
    try:
        model = apps.get_model("masters", "Holiday")
    except LookupError:
        return frozenset()
    try:
        # queryset は遅延なので、frozenset(...) で読み切るまでを try の中に置く (with も try の中。core.conf と同じ理由)。
        with transaction.atomic():
            days = frozenset(model._default_manager.values_list("date", flat=True))
    except (DatabaseError, RuntimeError):
        return frozenset()
    _cache = (days, now)
    return days


def _holidays(holidays):
    return holiday_dates() if holidays is None else holidays


def _is_workday(day, weekly, holidays) -> bool:
    return day.weekday() not in weekly and day not in holidays


def is_workday(day, *, holidays=None) -> bool:
    """day が稼働日か (定休日の曜日でなく、休日の表の日でもない)。"""
    return _is_workday(day, weekly_holidays(), _holidays(holidays))


def next_workday(day, *, holidays=None):
    """day を含めて、最初の稼働日 (day が稼働日なら day)。10 年先まで無ければ日本語の ValueError。"""
    weekly = weekly_holidays()
    holidays = _holidays(holidays)
    for offset in range(SEARCH_LIMIT_DAYS + 1):
        candidate = day + timedelta(days=offset)
        if _is_workday(candidate, weekly, holidays):
            return candidate
    raise ValueError(_NOT_FOUND_MESSAGE)


def add_workdays(day, n, *, holidays=None):
    """n が正なら day の翌日から数えて n 個目の稼働日、負なら前日から遡って -n 個目、0 なら day のまま。

    例: 2026-11-20 (金) に 1 = 11-24 (火) (11-21・22 は土日、11-23 は勤労感謝の日)。10 年を見ても足りなければ日本語の ValueError。
    """
    if n == 0:
        return day
    weekly = weekly_holidays()
    holidays = _holidays(holidays)
    step = timedelta(days=1 if n > 0 else -1)
    remaining = abs(n)
    candidate = day
    for _ in range(SEARCH_LIMIT_DAYS):
        candidate += step
        if _is_workday(candidate, weekly, holidays):
            remaining -= 1
            if remaining == 0:
                return candidate
    raise ValueError(_NOT_FOUND_MESSAGE)


def workdays_between(start, end, *, holidays=None) -> int:
    """符号つきの稼働日の数。

    end が start より後なら start < d <= end の稼働日の数、前なら end < d <= start の稼働日の数のマイナス、同じ日なら 0。
    定休日も休日も無ければ (end - start).days と同じ。日の数だけ回す (問い合わせは増えない)。
    """
    if end == start:
        return 0
    weekly = weekly_holidays()
    holidays = _holidays(holidays)
    low, high, sign = (start, end, 1) if end > start else (end, start, -1)
    count = 0
    for offset in range(1, (high - low).days + 1):
        if _is_workday(low + timedelta(days=offset), weekly, holidays):
            count += 1
    return sign * count
