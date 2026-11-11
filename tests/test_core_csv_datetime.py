"""一覧の CSV の書き出しの日時の字 (csvio.to_text)。

表は日時を世界標準時で持つ。CSV には、置き場の時刻帯 (settings の TIME_ZONE。試験は Asia/Tokyo) の時刻を
「年-月-日 時:分:秒」で書き、時刻帯の印 (「+00:00」) は付けない。直す前は str のままで、画面の時刻と 9 時間ずれた字が出た
(不良の一覧と実績の一覧の CSV。開発の記録 docs/nights/20261111/N407b.md の要望 3)。日付と字は今までどおり。
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from seizou.core import csvio


def test_aware_datetime_is_written_in_local_time_without_offset():
    utc = datetime(2026, 11, 3, 1, 0, tzinfo=UTC)
    assert csvio.to_text(utc) == "2026-11-03 10:00:00"
    tokyo = datetime(2026, 11, 3, 10, 0, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert csvio.to_text(tokyo) == "2026-11-03 10:00:30"


def test_datetime_near_midnight_keeps_the_local_date():
    """世界標準時では前の日でも、置き場の時刻帯の日付で書く。"""
    assert csvio.to_text(datetime(2026, 11, 2, 15, 30, tzinfo=UTC)) == "2026-11-03 00:30:00"


def test_naive_datetime_and_date_are_written_as_they_are():
    assert csvio.to_text(datetime(2026, 11, 3, 10, 0)) == "2026-11-03 10:00:00"
    assert csvio.to_text(date(2026, 11, 3)) == "2026-11-03"


def test_settings_time_zone_decides_the_text(settings):
    settings.TIME_ZONE = "UTC"
    assert csvio.to_text(datetime(2026, 11, 3, 1, 0, tzinfo=UTC)) == "2026-11-03 01:00:00"
