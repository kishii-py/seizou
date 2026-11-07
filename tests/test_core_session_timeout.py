"""ログインの時間切れ (操作しない間とログインからの最長。seizou.core.conf.session_limits と core.middleware.SessionTimeoutMiddleware)。

会社の設定の 2 つの時間の読み方と、画面から変えない事を見る。
middleware は試験の settings に入れていないので、試験ごとに MIDDLEWARE に HtmxLoginMiddleware とこの middleware を足す。
時計は core.middleware._now を monkeypatch で差し替え、時間を進めて切れる所を見る。
取り直しの見出しを付ける base.html と、雛形の MIDDLEWARE の 1 行は字で見る。利用者は作り物だけを使う。
"""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse

import seizou.scaffold
from seizou.core import conf, doctor, middleware, services
from seizou.core.middleware import LOGIN_AT_KEY, POLL_HEADER, SEEN_AT_KEY, TOUCH_SECONDS, SessionTimeoutMiddleware

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_PATH = "seizou.core.middleware.SessionTimeoutMiddleware"
HTMX_LOGIN_PATH = "seizou.core.middleware.HtmxLoginMiddleware"
#: 試験の始めの時刻 (秒。作り物の時刻)
START = 1_800_000_000
MINUTE = 60
HOUR = 60 * MINUTE


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


# ---------------------------------------------------------------- 2. 時間切れの middleware と取り直しの見出し


class Clock:
    """差し替える時計 (秒の整数)。advance で進める。"""

    def __init__(self, now: int):
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = Clock(START)
    monkeypatch.setattr(middleware, "_now", fake)
    return fake


@pytest.fixture
def timeout_chain(settings):
    # HtmxLoginMiddleware を外側 (前) に置く: 切れた時の 302 を、htmx の要求では 401 と HX-Redirect に替えてもらう (雛形と同じ並び)。
    settings.MIDDLEWARE = [*settings.MIDDLEWARE, HTMX_LOGIN_PATH, TIMEOUT_PATH]


@pytest.fixture
def worker(db):
    return get_user_model().objects.create_user("jikangire01", password="tests-only-pass-1108")


@pytest.fixture
def signed_in(client, worker, clock, timeout_chain):
    """ログインして画面を 1 回開いた client (ログインの時刻と最後の操作の時刻が START になる)。"""
    client.force_login(worker)
    assert client.get(reverse("core:settings")).status_code == 200
    assert client.session[LOGIN_AT_KEY] == START
    assert client.session[SEEN_AT_KEY] == START
    return client


def screen(client, **extra):
    return client.get(reverse("core:settings"), **extra)


def assert_sent_to_login(client, response, back: str) -> None:
    assert response.status_code == 302
    parts = urlsplit(response["Location"])
    assert parts.path == reverse("core:login")
    assert parse_qs(parts.query)["next"] == [back]
    assert SESSION_KEY not in client.session


def test_idle_two_hours_logs_out_and_sends_to_login(signed_in, clock):
    clock.advance(1 * HOUR + 59 * MINUTE)
    assert screen(signed_in).status_code == 200

    clock.advance(2 * HOUR + 1)
    assert_sent_to_login(signed_in, screen(signed_in), reverse("core:settings"))


def test_idle_htmx_request_gets_401_and_hx_redirect(signed_in, clock):
    clock.advance(2 * HOUR + 1)
    response = screen(signed_in, HTTP_HX_REQUEST="true")

    assert response.status_code == 401
    assert "Location" not in response
    assert urlsplit(response["HX-Redirect"]).path == reverse("core:login")
    assert SESSION_KEY not in signed_in.session


def test_idle_htmx_redirect_goes_back_to_current_page(signed_in, clock):
    """時間切れの 401 の HX-Redirect の next は、利用者が見ていた画面 (HX-Current-URL) の道 (HtmxLoginMiddleware と同じ)。"""
    clock.advance(2 * HOUR + 1)
    response = screen(signed_in, HTTP_HX_REQUEST="true", HTTP_HX_CURRENT_URL="http://testserver/orders/?q=WK")

    assert response.status_code == 401
    parts = urlsplit(response["HX-Redirect"])
    assert parts.path == reverse("core:login")
    assert parse_qs(parts.query)["next"] == ["/orders/?q=WK"]


def test_polling_does_not_count_as_operation(signed_in, clock):
    """見出し X-Seizou-Poll: 1 の取り直しだけを 30 分ごとに続けても、最後の人の操作から 2 時間を過ぎた要求で切れる。"""
    for _ in range(3):
        clock.advance(30 * MINUTE)
        assert screen(signed_in, HTTP_X_SEIZOU_POLL="1").status_code == 200
        assert signed_in.session[SEEN_AT_KEY] == START
    clock.advance(29 * MINUTE)
    assert screen(signed_in, HTTP_X_SEIZOU_POLL="1").status_code == 200

    clock.advance(1 * MINUTE + 1)
    assert_sent_to_login(signed_in, screen(signed_in, HTTP_X_SEIZOU_POLL="1"), reverse("core:settings"))


def test_operation_every_hour_still_ends_twelve_hours_after_login(signed_in, clock):
    """人の操作を 1 時間ごとに続けても、ログインから 12 時間を過ぎた要求で切れる。"""
    for _ in range(12):
        clock.advance(1 * HOUR)
        assert screen(signed_in).status_code == 200
    assert signed_in.session[SEEN_AT_KEY] == START + 12 * HOUR
    assert signed_in.session[LOGIN_AT_KEY] == START

    clock.advance(1)
    assert_sent_to_login(signed_in, screen(signed_in), reverse("core:settings"))


def test_seen_at_is_written_at_most_every_five_minutes(signed_in, clock):
    clock.advance(TOUCH_SECONDS - 1)
    assert screen(signed_in).status_code == 200
    assert signed_in.session[SEEN_AT_KEY] == START

    clock.advance(1)
    assert screen(signed_in).status_code == 200
    assert signed_in.session[SEEN_AT_KEY] == START + TOUCH_SECONDS


def test_company_settings_shorten_the_limits(signed_in, clock, settings):
    settings.SEIZOU = {"session_idle_minutes": 1, "session_max_hours": 12}
    clock.advance(1 * MINUTE)
    assert screen(signed_in).status_code == 200

    clock.advance(1 * MINUTE + 1)
    assert_sent_to_login(signed_in, screen(signed_in), reverse("core:settings"))


def test_request_without_session_auth_key_is_never_cut(db, worker, clock):
    """session に認証の鍵の無い要求 (開発機の自動ログインと同じ形 = request.user だけを差し替えた物) は、時計を進めても通る。"""
    passed = SessionTimeoutMiddleware(lambda request: HttpResponse("通した"))
    request = RequestFactory().get("/settings/")
    request.user = worker
    request.session = SessionStore()

    clock.advance(365 * 24 * HOUR)
    response = passed(request)

    assert response.status_code == 200
    assert response.content.decode() == "通した"
    assert dict(request.session.items()) == {}


def test_login_page_after_timeout_is_not_redirected_again(signed_in, clock):
    """切れた後にログインの画面を開くと 200 (logout だけして通し、302 を重ねない)。"""
    clock.advance(2 * HOUR + 1)
    response = signed_in.get(reverse("core:login"))

    assert response.status_code == 200
    assert SESSION_KEY not in signed_in.session

    assert_sent_to_login(signed_in, screen(signed_in), reverse("core:settings"))
    assert signed_in.get(reverse("core:login")).status_code == 200


def test_base_html_marks_polling_requests():
    text = (ROOT / "src/seizou/core/templates/core/base.html").read_text(encoding="utf-8")

    assert POLL_HEADER == "X-Seizou-Poll"
    assert 'document.addEventListener("htmx:configRequest"' in text
    listener = text[text.index('document.addEventListener("htmx:configRequest"') :]
    listener = listener[: listener.index("});") + 3]
    assert "polling(detail.elt)" in listener
    assert 'detail.headers["X-Seizou-Poll"] = "1"' in listener
    # 受け手は polling() と同じ塊 (htmx の失敗の知らせの塊) の中に置く
    block = text[text.index("function polling(elt)") :]
    assert block.index('"htmx:configRequest"') < block.index("})();")


def test_scaffold_places_timeout_after_htmx_login_and_before_messages():
    text = (Path(seizou.scaffold.__file__).parent / "settings.py.tmpl").read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]

    htmx = lines.index(f'"{HTMX_LOGIN_PATH}",')
    timeout = lines.index(f'"{TIMEOUT_PATH}",')
    messages = lines.index('"django.contrib.messages.middleware.MessageMiddleware",')
    assert htmx < timeout < messages
    assert "SESSION_COOKIE_AGE" not in text
