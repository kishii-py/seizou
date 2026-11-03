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
