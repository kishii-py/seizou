"""ログインの記録 (seizou.core.login_log) と、締め出しの間の試しの行 (seizou.core.login_guard)。

成功・失敗・ログアウトが記録 (seizou.login) に 1 行ずつ出て、ID と接続元があり、パスワードの字が無い事・
締め出しの間の試しは認証せずに断るので失敗の行が増えず、締め出しの間の行が 1 行出る事・ID の長さと改行の扱いを見る。
作り物の利用者 (genba01) と例に使う番地だけを使う。cache は試験の間に残るので、前と後に消す。
"""

import logging

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_login_failed
from django.urls import reverse

from seizou.core import login_guard
from seizou.core.login_guard import INVALID_MESSAGE, LOCKED_MESSAGE

PASSWORD = "tests-only-pass-1108"
WRONG = "tests-only-wrong-1108"
LOGGER = "seizou.login"


@pytest.fixture(autouse=True)
def fresh_cache():
    # 締め出しの数は login_guard.cache (試験では tests.settings の SEIZOU_LOGIN_CACHE_DIR = 一時の置き場)。始めに見た物を終わりにも消す。
    store = login_guard.cache
    store.clear()
    yield
    store.clear()


@pytest.fixture
def user():
    return get_user_model().objects.create_user("genba01", password=PASSWORD)


def attempt(client, username, password, **extra):
    return client.post(reverse("core:login"), {"username": username, "password": password}, **extra)


def body(response) -> str:
    return response.content.decode()


def lines(caplog, word):
    """記録 (seizou.login) の行のうち、word を含む物。"""
    return [record for record in caplog.records if record.name == LOGGER and word in record.getMessage()]


def has_password(record) -> bool:
    message = record.getMessage()
    return PASSWORD in message or WRONG in message


# ---------------------------------------------------------------- 成功・失敗・ログアウトの行 (1)


@pytest.mark.django_db
def test_success_writes_one_info_line_without_password(client, user, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)

    assert attempt(client, "genba01", PASSWORD).status_code == 302

    found = lines(caplog, "ログインしました")
    assert len(found) == 1
    assert found[0].levelno == logging.INFO
    message = found[0].getMessage()
    assert "genba01" in message and "127.0.0.1" in message
    assert not has_password(found[0]), "記録の行にパスワードが出た"


@pytest.mark.django_db
def test_wrong_password_writes_one_warning_line_without_password(client, user, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)

    response = attempt(client, "genba01", WRONG)
    assert INVALID_MESSAGE in body(response)

    found = lines(caplog, "ログインに失敗しました")
    assert len(found) == 1
    assert found[0].levelno == logging.WARNING
    message = found[0].getMessage()
    assert "genba01" in message and "127.0.0.1" in message
    assert not has_password(found[0]), "記録の行にパスワードが出た"
    assert lines(caplog, "ログインしました") == []


@pytest.mark.django_db
def test_unknown_username_writes_one_failure_line(client, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)

    attempt(client, "nobody99", WRONG)

    found = lines(caplog, "ログインに失敗しました")
    assert len(found) == 1
    assert "nobody99" in found[0].getMessage()
    assert not has_password(found[0])


@pytest.mark.django_db
def test_logout_writes_one_info_line(client, user, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    assert attempt(client, "genba01", PASSWORD).status_code == 302

    response = client.post(reverse("core:logout"))
    assert response.status_code in (200, 302)

    found = lines(caplog, "ログアウトしました")
    assert len(found) == 1
    assert found[0].levelno == logging.INFO
    message = found[0].getMessage()
    assert "genba01" in message and "127.0.0.1" in message


@pytest.mark.django_db
def test_logout_without_login_writes_empty_username(client, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)

    client.post(reverse("core:logout"))

    found = lines(caplog, "ログアウトしました")
    assert len(found) == 1
    assert "ID: ''" in found[0].getMessage()


# ---------------------------------------------------------------- 締め出しの間の試し (1 の (a))


@pytest.mark.django_db
def test_attempt_during_lock_writes_lock_line_and_no_failure_line(client, user, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    for _ in range(login_guard.FAIL_LIMIT):
        attempt(client, "genba01", WRONG)
    failures = len(lines(caplog, "ログインに失敗しました"))
    assert failures == login_guard.FAIL_LIMIT
    assert lines(caplog, "締め出しの間") == []

    sixth = attempt(client, "genba01", PASSWORD)
    assert sixth.status_code == 200 and LOCKED_MESSAGE in body(sixth)

    during = lines(caplog, "締め出しの間")
    assert len(during) == 1
    assert during[0].levelno == logging.WARNING
    assert "genba01" in during[0].getMessage() and "127.0.0.1" in during[0].getMessage()
    assert not has_password(during[0])
    assert len(lines(caplog, "ログインに失敗しました")) == failures, "締め出しの間は認証しないので、失敗の行は増えない"
    assert lines(caplog, "ログインしました") == []


# ---------------------------------------------------------------- ID の長さと改行 (1 の (f))


def test_long_username_is_cut_to_150_in_failure_line(rf, caplog):
    # ログインの画面の欄は 150 字までなので、500 字の ID は欄の確かめで止まり認証まで届かない。受け手に直に送って見る。
    caplog.set_level(logging.INFO, logger=LOGGER)
    request = rf.post("/login/", REMOTE_ADDR="198.51.100.4")

    user_login_failed.send(sender=__name__, credentials={"username": "a" * 500, "password": "*" * 20}, request=request)

    found = lines(caplog, "ログインに失敗しました")
    assert len(found) == 1
    message = found[0].getMessage()
    assert repr("a" * 150) in message
    assert "a" * 151 not in message
    assert "198.51.100.4" in message
    assert "*" * 20 not in message, "伏せたパスワードの字も書かない"


def test_failure_line_without_request_has_empty_address(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)

    user_login_failed.send(sender=__name__, credentials={"username": "genba01"}, request=None)

    found = lines(caplog, "ログインに失敗しました")
    assert len(found) == 1
    assert "接続元: ''" in found[0].getMessage()


@pytest.mark.django_db
def test_newline_in_username_does_not_split_the_line(client, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)

    attempt(client, "genba01\nログインしました (ID: 'admin'", WRONG)

    found = lines(caplog, "ログインに失敗しました")
    assert len(found) == 1
    message = found[0].getMessage()
    assert "\n" not in message and "\r" not in message
    assert "\\n" in message


# ---------------------------------------------------------------- 接続元ごとの上限 (2。見出しのある置き場だけ。本人の決め 11-08 00:38)

HEADER = "X-Real-IP"
ATTACKER = "203.0.113.7"
NEIGHBOR = "203.0.113.8"


@pytest.fixture
def fast_hashers(settings):
    # 接続元の上限の試験は 30 回を超えて打つので、試験の間だけ速いハッシュにする (既定の PBKDF2 は 1 回が重い)。
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture
def fast_user(fast_hashers):
    return get_user_model().objects.create_user("genba01", password=PASSWORD)


@pytest.fixture
def behind_proxy(settings, fast_hashers):
    settings.SEIZOU_CLIENT_IP_HEADER = HEADER


@pytest.fixture
def clock(monkeypatch):
    """login_guard._now を差し替える時計。clock["now"] を進めて使う。"""
    state = {"now": 1_800_000_000.0}
    monkeypatch.setattr(login_guard, "_now", lambda: state["now"])
    return state


def fail_from(client, address, count, start=0):
    """接続元 address から、作り物の違う ID (nobody00, nobody01, …) で count 回失敗する。"""
    return [attempt(client, f"nobody{start + i:02d}", WRONG, HTTP_X_REAL_IP=address) for i in range(count)]


@pytest.mark.django_db
def test_thirty_failures_from_one_address_lock_every_id(client, fast_user, behind_proxy):
    responses = fail_from(client, ATTACKER, login_guard.ADDRESS_FAIL_LIMIT)
    assert all(INVALID_MESSAGE in body(response) for response in responses[:-1])
    assert LOCKED_MESSAGE in body(responses[-1])

    right = attempt(client, "genba01", PASSWORD, HTTP_X_REAL_IP=ATTACKER)
    assert right.status_code == 200 and LOCKED_MESSAGE in body(right), "正しい ID とパスワードでも、その接続元は断る"
    assert "_auth_user_id" not in client.session

    neighbor = attempt(client, "genba01", PASSWORD, HTTP_X_REAL_IP=NEIGHBOR)
    assert neighbor.status_code == 302, "別の接続元は締め出されない"


@pytest.mark.django_db
def test_success_does_not_clear_the_address_count(client, fast_user, behind_proxy):
    fail_from(client, ATTACKER, login_guard.ADDRESS_FAIL_LIMIT - 1)

    assert attempt(client, "genba01", PASSWORD, HTTP_X_REAL_IP=ATTACKER).status_code == 302
    client.logout()

    last = attempt(client, "nobody99", WRONG, HTTP_X_REAL_IP=ATTACKER)
    assert LOCKED_MESSAGE in body(last), "成功を挟んでも、窓の中の 30 回目で締め出す"
    again = attempt(client, "genba01", PASSWORD, HTTP_X_REAL_IP=ATTACKER)
    assert again.status_code == 200 and LOCKED_MESSAGE in body(again)


@pytest.mark.django_db
def test_window_starts_at_first_failure_and_does_not_extend(client, fast_user, behind_proxy, clock):
    fail_from(client, ATTACKER, 1)
    clock["now"] += login_guard.ADDRESS_WINDOW_SECONDS - 60
    fail_from(client, ATTACKER, login_guard.ADDRESS_FAIL_LIMIT - 2, start=1)

    clock["now"] += 61  # 最初の失敗から 15 分と 1 秒
    late = attempt(client, "nobody98", WRONG, HTTP_X_REAL_IP=ATTACKER)
    assert INVALID_MESSAGE in body(late) and LOCKED_MESSAGE not in body(late), "窓が過ぎたので、30 回目にならない"
    assert login_guard.cache.get(login_guard.address_key(ATTACKER))["count"] == 1, "窓が新しくなり、数え直す"

    assert attempt(client, "genba01", PASSWORD, HTTP_X_REAL_IP=ATTACKER).status_code == 302


@pytest.mark.django_db
def test_address_lock_ends_after_fifteen_minutes(client, fast_user, behind_proxy, clock):
    fail_from(client, ATTACKER, login_guard.ADDRESS_FAIL_LIMIT)

    clock["now"] += login_guard.LOCK_SECONDS - 1
    still = attempt(client, "genba01", PASSWORD, HTTP_X_REAL_IP=ATTACKER)
    assert still.status_code == 200 and LOCKED_MESSAGE in body(still)

    clock["now"] += 2  # 締め出してから 15 分と 1 秒
    assert attempt(client, "genba01", PASSWORD, HTTP_X_REAL_IP=ATTACKER).status_code == 302


@pytest.mark.django_db
def test_no_header_setting_means_no_address_limit(client, fast_user, settings, monkeypatch):
    # 見出しが無い置き場 (nginx の後ろでは全員が 127.0.0.1 に見える) では、接続元の上限を効かせない
    monkeypatch.delenv(login_guard.HEADER_SETTING, raising=False)
    if hasattr(settings, login_guard.HEADER_SETTING):
        monkeypatch.delattr(settings, login_guard.HEADER_SETTING)

    for i in range(login_guard.ADDRESS_FAIL_LIMIT + 1):
        attempt(client, f"nobody{i:02d}", WRONG)

    assert attempt(client, "genba01", PASSWORD).status_code == 302
    assert login_guard.cache.get(login_guard.address_key("127.0.0.1")) is None
    assert login_guard.is_address_locked("127.0.0.1") is False
    assert login_guard.record_address_failure("127.0.0.1") == 0


def test_empty_address_is_not_counted(behind_proxy):
    assert login_guard.record_address_failure("") == 0
    assert login_guard.is_address_locked("") is False


def test_address_key_hides_the_address():
    key = login_guard.address_key(ATTACKER)

    assert ATTACKER not in key
    assert key.startswith(login_guard.ADDRESS_PREFIX)
    assert len(key) == len(login_guard.ADDRESS_PREFIX) + 64
    assert login_guard.address_key(NEIGHBOR) != key


@pytest.mark.django_db
def test_address_lock_writes_one_warning_line_without_password(client, fast_user, behind_proxy, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)

    fail_from(client, ATTACKER, login_guard.ADDRESS_FAIL_LIMIT)

    found = lines(caplog, "この接続元を")
    assert len(found) == 1
    assert found[0].levelno == logging.WARNING
    message = found[0].getMessage()
    assert ATTACKER in message and "15 分に 30 回" in message
    assert "続けて失敗したので" not in message, "ID と接続元の組の締め出しの行と字で分ける"
    assert not has_password(found[0])

    attempt(client, "genba01", PASSWORD, HTTP_X_REAL_IP=ATTACKER)
    assert len(lines(caplog, "この接続元を")) == 1
    assert len(lines(caplog, "締め出しの間")) == 1
