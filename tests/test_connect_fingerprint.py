"""鍵の指紋 (connect.signing.fingerprint) と、つながりの詳細の「鍵の指紋」の行のテスト。

意図: 2 社が同じ鍵の値を置いたかを、鍵の値を出さずに電話で読み合わせられるようにするため (本体の決め 11-11 14:2x。精査 Q3 の T9)。

鍵はテストのたびに tests/test_connect_signing.py の _new_secret で作り、環境変数に置く。固定の鍵の文字列をファイルに書かない。
鍵の値を含む比べは真偽にしてから assert する (失敗の文に値を出さないため)。取引先は作り物。
helper と fixture は既にある試験のファイルから import し、そのファイルは変えない。
"""

import base64
import re

import pytest
from django.urls import reverse

from seizou.connect import services, signing
from tests.test_connect_connection_views import (  # noqa: F401 (fixture)
    ENV_NAME,
    client_logged_in,
    connection,
    partner,
    user,
)
from tests.test_connect_signing import _new_secret

FINGERPRINT_FORM = re.compile(r"^[0-9a-f]{4}( [0-9a-f]{4}){3}$")


def _windows(text, size=8):
    """字の size 字以上の並びの全部 (size 字の窓で足りる)。"""
    return {text[start : start + size] for start in range(len(text) - size + 1)}


# ---------------------------------------------------------------- (g) 指紋の字


def test_fingerprint_same_value_same_text():
    secret = _new_secret()
    other = _new_secret()

    first = signing.fingerprint(secret)
    same = (first == signing.fingerprint(secret), first == signing.fingerprint(other))
    assert same == (True, False)
    assert FINGERPRINT_FORM.fullmatch(first) is not None
    assert FINGERPRINT_FORM.fullmatch(signing.fingerprint(other)) is not None


def test_fingerprint_does_not_show_the_value():
    for _ in range(20):
        secret = _new_secret()
        value_part = secret.removeprefix("whsec_")
        text = signing.fingerprint(secret)
        joined = text.replace(" ", "")
        leaked = any(window in text or window in joined for window in _windows(value_part))
        assert leaked is False


def test_fingerprint_is_not_the_plain_hash_of_the_key():
    """決まった字を頭に付けるので、鍵の bytes の SHA-256 の頭とは違う字になる (ほかで使う鍵の hash と並ばないため)。"""
    import hashlib

    secret = _new_secret()
    plain = hashlib.sha256(base64.b64decode(secret.removeprefix("whsec_"))).hexdigest()[:16]
    same = signing.fingerprint(secret).replace(" ", "") == plain
    assert same is False


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-key",
        "whsec_",
        "whsec_***",
        "whsec_" + base64.b64encode(b"short-key").decode(),
    ],
    ids=["no-prefix", "empty", "not-base64", "too-short"],
)
def test_fingerprint_unreadable_value_is_value_error(bad):
    with pytest.raises(ValueError):
        signing.fingerprint(bad)


# ---------------------------------------------------------------- (h) つながりの詳細


def test_detail_shows_fingerprint_without_value(client_logged_in, connection, monkeypatch):  # noqa: F811
    secret = _new_secret()
    monkeypatch.setenv(ENV_NAME, secret)
    value_part = secret.removeprefix("whsec_")
    expected = signing.fingerprint(secret)

    body = client_logged_in.get(reverse("connect:connection_detail", args=[connection.pk])).content.decode()

    shown = ("鍵の指紋" in body, expected in body, "電話で読み合わせる" in body)
    leaked = (value_part in body, any(window in body for window in _windows(value_part)))
    assert shown == (True, True, True)
    assert leaked == (False, False)


def test_detail_without_secret_has_no_fingerprint(client_logged_in, connection, monkeypatch):  # noqa: F811
    monkeypatch.delenv(ENV_NAME, raising=False)

    body = client_logged_in.get(reverse("connect:connection_detail", args=[connection.pk])).content.decode()

    assert "鍵の設定" in body and "まだ" in body
    assert "鍵の指紋" not in body
    assert "電話で読み合わせる" not in body


def test_detail_with_unreadable_secret_says_so(client_logged_in, connection, monkeypatch):  # noqa: F811
    monkeypatch.setenv(ENV_NAME, "not-a-key-for-tests")

    body = client_logged_in.get(reverse("connect:connection_detail", args=[connection.pk])).content.decode()

    assert "鍵の指紋" in body and "鍵の形が読めません" in body
    assert "not-a-key-for-tests" not in body


def test_detail_with_send_connection(client_logged_in, partner, user, monkeypatch):  # noqa: F811
    secret = _new_secret()
    monkeypatch.setenv(ENV_NAME, secret)
    row = services.register_connection(
        partner, "https://partner.example.test/connect/receive/order/", ENV_NAME, "send", user
    )

    body = client_logged_in.get(reverse("connect:connection_detail", args=[row.pk])).content.decode()

    shown = (signing.fingerprint(secret) in body, secret.removeprefix("whsec_") in body)
    assert shown == (True, False)
