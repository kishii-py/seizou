"""ログインの時間切れ (操作しない間とログインからの最長。seizou.core.conf.session_limits と core.middleware.SessionTimeoutMiddleware)。

会社の設定の 2 つの時間の読み方と、画面から変えない事を見る。
利用者は作り物だけを使う。
"""

import pytest

from seizou.core import conf, doctor, services


# ---------------------------------------------------------------- 1. 会社の設定の 2 つの時間


def test_session_limits_default_is_two_hours_and_twelve_hours():
    assert conf.DEFAULTS["session_idle_minutes"] == 120
    assert conf.DEFAULTS["session_max_hours"] == 12
    assert conf.session_limits() == (7200, 43200)


def test_session_limits_follow_company_settings(settings):
    settings.SEIZOU = {"session_idle_minutes": 30, "session_max_hours": 8}

    assert conf.session_limits() == (1800, 28800)


@pytest.mark.parametrize("bad", ["x", True, False, 0, -5, 1.5, None, [30]])
def test_session_limits_fall_back_to_defaults_on_bad_values(settings, bad):
    """答えの YAML の書き損じ (字・真偽・0 以下・小数・空・並び) は既定に戻し、例外を投げない。"""
    settings.SEIZOU = {"session_idle_minutes": bad, "session_max_hours": bad}

    assert conf.session_limits() == (7200, 43200)


@pytest.mark.django_db
def test_session_keys_are_locked_from_the_screen(settings):
    for key in ("session_idle_minutes", "session_max_hours"):
        assert key in conf.LOCKED_KEYS
        with pytest.raises(ValueError):
            services.validate_setting(key, 30)
    # 既にある 3 つの鍵は並びも字も変えない (足すのは並びの末尾だけ)
    assert conf.LOCKED_KEYS[:3] == ("menu", "weekly_holidays", "transcription_minutes_per_line")

    assert [(row.ok, row.label) for row in doctor.check_settings()] == [(True, "会社の選べる設定")]
    settings.SEIZOU = {"session_idle_minutes": 30, "session_max_hours": 8}
    assert [(row.ok, row.label) for row in doctor.check_settings()] == [(True, "会社の選べる設定")]
