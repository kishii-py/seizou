"""稼働日の数え方 (seizou.core.calendar。読むだけ)。

関数の試験は休日の集合を holidays= で渡す (表を読まない)。表の控えの試験だけ db を使い、休日は masters の services で書く。
日付は 2026 年の 11 月の決まった日 (11-20 は金曜、11-23 は勤労感謝の日の月曜)。作り物のデータだけを使う。
"""

from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from seizou.core import calendar
from seizou.masters import services as masters_services
from seizou.masters.models import Holiday

LABOR_DAY = date(2026, 11, 23)
HOLIDAYS = frozenset({LABOR_DAY})
FRI = date(2026, 11, 20)
SAT = date(2026, 11, 21)
SUN = date(2026, 11, 22)
TUE = date(2026, 11, 24)


def test_is_workday_skips_weekly_holidays_and_holiday_dates():
    assert calendar.is_workday(FRI, holidays=HOLIDAYS) is True
    assert calendar.is_workday(SAT, holidays=HOLIDAYS) is False
    assert calendar.is_workday(LABOR_DAY, holidays=HOLIDAYS) is False
    assert calendar.is_workday(LABOR_DAY, holidays=frozenset()) is True


def test_next_workday_includes_the_day_itself():
    assert calendar.next_workday(FRI, holidays=HOLIDAYS) == FRI
    assert calendar.next_workday(SAT, holidays=frozenset()) == LABOR_DAY
    assert calendar.next_workday(SAT, holidays=HOLIDAYS) == TUE


def test_add_workdays_counts_forward_and_backward():
    assert calendar.add_workdays(FRI, 1, holidays=HOLIDAYS) == TUE
    assert calendar.add_workdays(FRI, 0, holidays=HOLIDAYS) == FRI
    assert calendar.add_workdays(TUE, -1, holidays=HOLIDAYS) == FRI
    assert calendar.add_workdays(SAT, 1, holidays=HOLIDAYS) == TUE


def test_workdays_between_is_signed():
    # 11-10 (火) → 11-15 (日): 稼働日は 11・12・13 の 3 日
    assert calendar.workdays_between(date(2026, 11, 10), date(2026, 11, 15), holidays=HOLIDAYS) == 3
    assert calendar.workdays_between(FRI, FRI, holidays=HOLIDAYS) == 0
    assert calendar.workdays_between(FRI, TUE, holidays=HOLIDAYS) == 1
    assert calendar.workdays_between(TUE, FRI, holidays=HOLIDAYS) == -1
    # どちらも休みの日なら 0 (日曜から見た土曜)
    assert calendar.workdays_between(SUN, SAT, holidays=HOLIDAYS) == 0


def test_without_weekly_holidays_workdays_equal_calendar_days(settings):
    settings.SEIZOU = {"weekly_holidays": []}
    assert calendar.weekly_holidays() == frozenset()
    first = date(2026, 11, 1)
    days = [first + timedelta(days=n) for n in range(60)]
    for start in days:
        for end in days:
            assert calendar.workdays_between(start, end, holidays=frozenset()) == (end - start).days, (start, end)


def test_add_workdays_and_workdays_between_agree():
    for start in (date(2026, 11, 1) + timedelta(days=n) for n in range(14)):
        for n in range(21):
            end = calendar.add_workdays(start, n, holidays=HOLIDAYS)
            assert calendar.workdays_between(start, end, holidays=HOLIDAYS) == n, (start, n)


@pytest.mark.parametrize("value", ["5,6", [7], [True], [0, 1, 2, 3, 4, 5, 6], None, [-1], [5.0]])
def test_weekly_holidays_falls_back_to_default_on_bad_values(settings, value):
    settings.SEIZOU = {"weekly_holidays": value}
    assert calendar.weekly_holidays() == frozenset({5, 6})


def test_weekly_holidays_reads_setting(settings):
    assert calendar.weekly_holidays() == frozenset({5, 6})  # 既定は土日
    settings.SEIZOU = {"weekly_holidays": [6]}
    assert calendar.weekly_holidays() == frozenset({6})
    # 土曜を稼働日として数える
    assert calendar.is_workday(SAT, holidays=frozenset()) is True
    assert calendar.workdays_between(FRI, TUE, holidays=HOLIDAYS) == 2


def test_add_workdays_refuses_when_no_workday_is_found(settings):
    settings.SEIZOU = {"weekly_holidays": [0, 1, 2, 3, 4, 5]}
    every_sunday = frozenset(date(2026, 11, 1) + timedelta(days=7 * n) for n in range(600))
    with pytest.raises(ValueError) as error:
        calendar.next_workday(date(2026, 11, 2), holidays=every_sunday)
    assert "10 年先まで" in str(error.value)
    with pytest.raises(ValueError):
        calendar.add_workdays(date(2026, 11, 2), 1, holidays=every_sunday)


@pytest.mark.django_db
def test_holiday_dates_reads_the_table_once_and_follows_writes(monkeypatch):
    user = get_user_model().objects.create_user("tester", password="pass-for-tests-only")
    clock = [1000.0]
    monkeypatch.setattr(calendar, "_now", lambda: clock[0])

    assert calendar.holiday_dates() == frozenset()
    row = masters_services.create_holiday({"date": LABOR_DAY, "name": "勤労感謝の日", "kind": Holiday.KIND_PUBLIC}, user)
    assert calendar.holiday_dates() == frozenset({LABOR_DAY})  # 書いた後に控えを捨てる

    with CaptureQueriesContext(connection) as queries:
        assert calendar.holiday_dates() == frozenset({LABOR_DAY})
        assert calendar.holiday_dates() == frozenset({LABOR_DAY})
        assert calendar.is_workday(LABOR_DAY) is False
    # 控えが新しいうちは休日の表を読まない (定休日の設定の読みは core.conf の控えの分)
    assert not [q for q in queries if "masters_holiday" in q["sql"]]

    # 別の process が書いた休日 (控えを捨てない書き方) は、CACHE_SECONDS 秒の後に読み直す
    Holiday.objects.create(date=TUE, name="作り物の会社の休み", kind=Holiday.KIND_COMPANY)
    assert TUE not in calendar.holiday_dates()
    clock[0] += calendar.CACHE_SECONDS + 1
    with CaptureQueriesContext(connection) as queries:
        assert calendar.holiday_dates() == frozenset({LABOR_DAY, TUE})
        assert calendar.holiday_dates() == frozenset({LABOR_DAY, TUE})
    assert len([q for q in queries if "masters_holiday" in q["sql"]]) == 1

    masters_services.delete_holiday(row, user)
    assert calendar.holiday_dates() == frozenset({TUE})


def test_holiday_dates_is_empty_without_db():
    # db の無い試験では表を読めない。空の集合を返し、控えに入れない (次に読める時に読み直す)
    assert calendar.holiday_dates() == frozenset()
    assert calendar._cache is None
