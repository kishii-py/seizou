"""ログインの締め出し (seizou.core.login_guard)。

5 回目で締め出し、正しいパスワードも断る事・ID と接続元の組ごとである事・成功で数え直す事・
記録の 1 行にパスワードが無い事・ログインの口の form の差し替えを見る。
cache は 1 つの process の記憶で試験の間に残るので、前と後に消す。
"""

import logging

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import resolve, reverse

from seizou.core import login_guard
from seizou.core.login_guard import (
    CACHE_PREFIX,
    INVALID_MESSAGE,
    LOCKED_MESSAGE,
    LockoutAuthenticationForm,
    failure_key,
)
from seizou.core.views import RememberLoginView

PASSWORD = "tests-only-pass-1103"
WRONG = "tests-only-wrong-1103"


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user():
    return get_user_model().objects.create_user("genba01", password=PASSWORD)


def attempt(client, username, password, **extra):
    return client.post(reverse("core:login"), {"username": username, "password": password}, **extra)


def body(response) -> str:
    return response.content.decode()


def lock_out(client, username="genba01", **extra):
    responses = [attempt(client, username, WRONG, **extra) for _ in range(login_guard.FAIL_LIMIT)]
    return responses[-1]


@pytest.mark.django_db
def test_failures_below_limit_say_invalid_and_success_clears(client, user):
    for _ in range(4):
        response = attempt(client, "genba01", WRONG)
        assert response.status_code == 200
        assert INVALID_MESSAGE in body(response)
        assert LOCKED_MESSAGE not in body(response)

    success = attempt(client, "genba01", PASSWORD)
    assert success.status_code == 302
    assert success["Location"] == reverse("core:home")

    client.logout()
    for _ in range(4):
        assert INVALID_MESSAGE in body(attempt(client, "genba01", WRONG))
    again = attempt(client, "genba01", PASSWORD)
    assert again.status_code == 302


@pytest.mark.django_db
def test_fifth_failure_locks_even_the_right_password(client, user):
    fifth = lock_out(client)
    assert fifth.status_code == 200
    assert LOCKED_MESSAGE in body(fifth)

    right = attempt(client, "genba01", PASSWORD)
    assert right.status_code == 200
    assert LOCKED_MESSAGE in body(right)
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_lock_is_per_username_and_address(client, user):
    get_user_model().objects.create_user("jimu01", password=PASSWORD)
    assert LOCKED_MESSAGE in body(lock_out(client))

    other_user = attempt(client, "jimu01", PASSWORD)
    assert other_user.status_code == 302
    client.logout()

    other_address = attempt(client, "genba01", PASSWORD, REMOTE_ADDR="10.0.0.9")
    assert other_address.status_code == 302


@pytest.mark.django_db
def test_lock_ends_when_the_count_expires(client, user, monkeypatch):
    lock_out(client)
    cache.delete(login_guard.failure_key("genba01", "127.0.0.1"))

    assert attempt(client, "genba01", PASSWORD).status_code == 302

    calls = []

    class FakeCache:
        def get(self, key, default=None):
            return default

        def set(self, key, value, timeout):
            calls.append((key, value, timeout))

    monkeypatch.setattr(login_guard, "cache", FakeCache())
    assert login_guard.record_failure("genba01", "127.0.0.1") == 1
    assert login_guard.LOCK_SECONDS == 900
    assert calls == [(failure_key("genba01", "127.0.0.1"), 1, 900)]


@pytest.mark.django_db
def test_unknown_username_is_counted_too(client):
    for _ in range(login_guard.FAIL_LIMIT - 1):
        assert INVALID_MESSAGE in body(attempt(client, "nobody99", WRONG))

    assert LOCKED_MESSAGE in body(attempt(client, "nobody99", WRONG))


@pytest.mark.django_db
def test_lock_writes_one_log_line_without_password(client, user, caplog):
    caplog.set_level(logging.WARNING, logger="seizou.login")

    def lines():
        return [record.getMessage() for record in caplog.records if record.name == "seizou.login"]

    for _ in range(4):
        attempt(client, "genba01", WRONG)
    assert lines() == []

    attempt(client, "genba01", WRONG)
    assert len(lines()) == 1
    line = lines()[0]
    assert "genba01" in line and "127.0.0.1" in line
    leaked = WRONG in line or PASSWORD in line
    assert not leaked, "記録の 1 行にパスワードが出た"

    sixth = attempt(client, "genba01", WRONG)
    assert LOCKED_MESSAGE in body(sixth)
    assert len(lines()) == 1


def test_login_url_uses_the_lockout_form():
    match = resolve(reverse("core:login"))

    assert match.func.view_initkwargs["authentication_form"] is LockoutAuthenticationForm
    assert match.func.view_class is RememberLoginView


def test_failure_key_hides_the_username():
    key = failure_key("genba01", "127.0.0.1")

    assert "genba01" not in key
    assert key.startswith(CACHE_PREFIX)
    assert len(key) == len(CACHE_PREFIX) + 64
    assert failure_key("GENBA01", "127.0.0.1") == key
    assert failure_key("genba01", "10.0.0.9") != key


# ---------------------------------------------------------------- 接続元の見出し (nginx の後ろ。11-04)


def test_client_address_reads_header_only_when_configured(settings, monkeypatch, rf):
    # 既定 (設定が無い): proxy の見出しがあっても REMOTE_ADDR だけを見る (偽れるため)
    monkeypatch.delenv(login_guard.HEADER_SETTING, raising=False)
    if hasattr(settings, login_guard.HEADER_SETTING):
        monkeypatch.delattr(settings, login_guard.HEADER_SETTING)
    request = rf.get("/login/", REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="203.0.113.5")
    assert login_guard.client_address(request) == "127.0.0.1"

    # 置き場の .env (環境変数) に見出しの名前があれば、その見出しの最初の値。見出しが無い要求は REMOTE_ADDR に戻る
    monkeypatch.setenv(login_guard.HEADER_SETTING, "X-Real-IP")
    assert login_guard.client_address(request) == "203.0.113.5"
    chained = rf.get("/login/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1")
    monkeypatch.setenv(login_guard.HEADER_SETTING, "X-Forwarded-For")
    assert login_guard.client_address(chained) == "203.0.113.7"
    assert login_guard.client_address(rf.get("/login/", REMOTE_ADDR="127.0.0.1")) == "127.0.0.1"

    # settings.py の値が環境変数より先。空の字なら既定に戻る
    monkeypatch.setattr(settings, login_guard.HEADER_SETTING, "", raising=False)
    assert login_guard.client_address(request) == "127.0.0.1"
    monkeypatch.setattr(settings, login_guard.HEADER_SETTING, "X-Real-IP", raising=False)
    assert login_guard.client_address(request) == "203.0.113.5"


@pytest.mark.django_db
def test_lock_behind_proxy_is_per_header_address(client, user, settings):
    settings.SEIZOU_CLIENT_IP_HEADER = "X-Real-IP"

    assert LOCKED_MESSAGE in body(lock_out(client, HTTP_X_REAL_IP="203.0.113.5"))
    same = attempt(client, "genba01", PASSWORD, HTTP_X_REAL_IP="203.0.113.5")
    assert same.status_code == 200 and LOCKED_MESSAGE in body(same)

    other = attempt(client, "genba01", PASSWORD, HTTP_X_REAL_IP="203.0.113.6")
    assert other.status_code == 302, "別の接続元 (見出しの値) は締め出されない"
