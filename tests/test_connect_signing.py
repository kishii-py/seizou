"""connect の署名の module (Standard Webhooks の形) のテスト。

鍵はテストのたびに作る。固定の鍵の文字列をファイルに書かない。
本文は作り物の JSON の bytes。
"""

import base64
import hashlib
import hmac
import importlib.util
import secrets
import sys
import uuid

import pytest

from seizou.connect import signing
from seizou.connect.signing import (
    HEADER_ID,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    SignatureError,
)

NOW = 1_800_000_000
BODY = b'{"kind":"order","lines":[{"item_code":"TEST-001","quantity":10}]}'


def _new_secret() -> str:
    """公開の仕様の形 (接頭辞つきの base64) の鍵を実行時に作る。"""
    return "whsec_" + base64.b64encode(secrets.token_bytes(32)).decode()


def _new_id() -> str:
    return str(uuid.uuid4())


def _assert_rejected(reason, secret, headers, body=BODY, now=NOW):
    with pytest.raises(SignatureError) as excinfo:
        signing.verify(secret, headers, body, now=now)
    assert excinfo.value.reason == reason
    assert secret not in str(excinfo.value)


def test_sign_and_verify_roundtrip():
    secret = _new_secret()
    msg_id = _new_id()
    headers = signing.headers_for(secret, msg_id, BODY, now=NOW)

    assert (HEADER_ID, HEADER_TIMESTAMP, HEADER_SIGNATURE) == (
        "webhook-id",
        "webhook-timestamp",
        "webhook-signature",
    )
    assert set(headers) == {HEADER_ID, HEADER_TIMESTAMP, HEADER_SIGNATURE}
    assert headers[HEADER_ID] == msg_id
    assert headers[HEADER_TIMESTAMP] == str(NOW)

    # 署名は「番号.時刻.本文」の HMAC-SHA256 を base64 にして v1, を付けた物
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = f"{msg_id}.{NOW}.".encode() + BODY
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    expected = "v1," + base64.b64encode(digest).decode()
    assert headers[HEADER_SIGNATURE] == expected
    assert signing.sign(secret, msg_id, NOW, BODY) == expected

    assert signing.verify(secret, headers, BODY, now=NOW) == msg_id

    # now を省くと今の時刻で作り、今の時刻で確かめる
    fresh = signing.headers_for(secret, msg_id, BODY)
    assert signing.verify(secret, fresh, BODY) == msg_id


def test_wrong_key_is_rejected():
    secret = _new_secret()
    other = _new_secret()
    headers = signing.headers_for(secret, _new_id(), BODY, now=NOW)

    _assert_rejected(SignatureError.SIGNATURE_MISMATCH, other, headers)


def test_tampered_body_is_rejected():
    secret = _new_secret()
    headers = signing.headers_for(secret, _new_id(), BODY, now=NOW)

    tampered = bytearray(BODY)
    index = BODY.index(b"10")
    tampered[index] = ord("2")
    assert len(tampered) == len(BODY)
    assert tampered != BODY

    _assert_rejected(
        SignatureError.SIGNATURE_MISMATCH, secret, headers, bytes(tampered)
    )


def test_tampered_id_or_timestamp_is_rejected():
    secret = _new_secret()
    headers = signing.headers_for(secret, _new_id(), BODY, now=NOW)

    forged_id = {**headers, HEADER_ID: _new_id()}
    _assert_rejected(SignatureError.SIGNATURE_MISMATCH, secret, forged_id)

    # 時刻の幅の中で時刻だけを変えても、署名が合わない
    forged_time = {**headers, HEADER_TIMESTAMP: str(NOW + 1)}
    _assert_rejected(SignatureError.SIGNATURE_MISMATCH, secret, forged_time)


def test_timestamp_outside_tolerance_is_rejected():
    secret = _new_secret()
    msg_id = _new_id()
    headers = signing.headers_for(secret, msg_id, BODY, now=NOW)

    # 受ける側の時刻を +301 秒 (見出しが 301 秒前) と -301 秒 (301 秒後) にずらす
    for offset in (301, -301):
        _assert_rejected(
            SignatureError.TIMESTAMP_OUT_OF_TOLERANCE,
            secret,
            headers,
            now=NOW + offset,
        )

    # 境目の 300 秒は過去と未来の両側で通す
    for offset in (300, -300):
        assert signing.verify(secret, headers, BODY, now=NOW + offset) == msg_id


def test_missing_or_malformed_headers_are_rejected():
    secret = _new_secret()
    headers = signing.headers_for(secret, _new_id(), BODY, now=NOW)

    for name in (HEADER_ID, HEADER_TIMESTAMP, HEADER_SIGNATURE):
        missing = {key: value for key, value in headers.items() if key != name}
        _assert_rejected(SignatureError.MISSING_HEADER, secret, missing)
        empty = {**headers, name: ""}
        _assert_rejected(SignatureError.MISSING_HEADER, secret, empty)

    for bad in (
        "abc",
        f"{NOW}.5",
        f"-{NOW}",
        f" {NOW}",
        "1_800_000_000",
        "".join(chr(0xFF10 + digit) for digit in (1, 8, 0, 0)),  # 全角の数字
        "9" * 5000,
    ):
        malformed = {**headers, HEADER_TIMESTAMP: bad}
        _assert_rejected(SignatureError.INVALID_TIMESTAMP, secret, malformed)

    value = headers[HEADER_SIGNATURE].removeprefix("v1,")
    for bad in (f"v2,{value}", value, "v1"):
        no_v1 = {**headers, HEADER_SIGNATURE: bad}
        _assert_rejected(SignatureError.SIGNATURE_MISMATCH, secret, no_v1)


def test_replay_within_tolerance_passes_signature_check():
    # 再送を見分けるのは呼ぶ側 (receive と Inbox の一意の制約) の仕事
    secret = _new_secret()
    msg_id = _new_id()
    headers = signing.headers_for(secret, msg_id, BODY, now=NOW)

    first = signing.verify(secret, headers, BODY, now=NOW)
    second = signing.verify(secret, headers, BODY, now=NOW + 60)
    assert first == second == msg_id


def test_replay_after_tolerance_is_rejected():
    secret = _new_secret()
    msg_id = _new_id()
    headers = signing.headers_for(secret, msg_id, BODY, now=NOW)

    assert signing.verify(secret, headers, BODY, now=NOW) == msg_id
    _assert_rejected(
        SignatureError.TIMESTAMP_OUT_OF_TOLERANCE, secret, headers, now=NOW + 301
    )


def test_multiple_signatures_any_v1_match():
    secret = _new_secret()
    msg_id = _new_id()
    headers = signing.headers_for(secret, msg_id, BODY, now=NOW)
    good = headers[HEADER_SIGNATURE]
    bad = signing.sign(_new_secret(), msg_id, NOW, BODY)
    bad2 = signing.sign(_new_secret(), msg_id, NOW, BODY)

    for combined in (f"{bad} {good}", f"{good} {bad}", f"{bad}  {good}"):
        rotated = {**headers, HEADER_SIGNATURE: combined}
        assert signing.verify(secret, rotated, BODY, now=NOW) == msg_id

    no_match = {**headers, HEADER_SIGNATURE: f"{bad} {bad2}"}
    _assert_rejected(SignatureError.SIGNATURE_MISMATCH, secret, no_match)

    # 合う値でも v1, 以外の候補は無視する
    value = good.removeprefix("v1,")
    other_versions = {**headers, HEADER_SIGNATURE: f"v1a,{value} v2,{value} {bad}"}
    _assert_rejected(SignatureError.SIGNATURE_MISMATCH, secret, other_versions)


def test_header_names_are_case_insensitive():
    secret = _new_secret()
    msg_id = _new_id()
    headers = signing.headers_for(secret, msg_id, BODY, now=NOW)

    upper = {key.upper(): value for key, value in headers.items()}
    title = {key.title(): value for key, value in headers.items()}
    assert set(title) == {"Webhook-Id", "Webhook-Timestamp", "Webhook-Signature"}

    assert signing.verify(secret, upper, BODY, now=NOW) == msg_id
    assert signing.verify(secret, title, BODY, now=NOW) == msg_id


def test_secret_without_prefix_is_refused():
    secret = _new_secret()
    raw = secret.removeprefix("whsec_")
    headers = signing.headers_for(secret, _new_id(), BODY, now=NOW)

    with pytest.raises(ValueError) as excinfo:
        signing.headers_for(raw, _new_id(), BODY, now=NOW)
    assert raw not in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        signing.verify(raw, headers, BODY, now=NOW)
    assert raw not in str(excinfo.value)

    with pytest.raises(ValueError):
        signing.sign("whsec_!!!", _new_id(), NOW, BODY)


def test_module_does_not_import_django(monkeypatch):
    monkeypatch.setitem(sys.modules, "django", None)
    monkeypatch.setitem(sys.modules, "requests", None)
    with pytest.raises(ImportError):
        importlib.import_module("django")

    # 読み込み済みの module を差し替えず、別の名前で 1 から読む
    spec = importlib.util.spec_from_file_location(
        "_signing_without_framework", signing.__file__
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    secret = _new_secret()
    msg_id = _new_id()
    headers = module.headers_for(secret, msg_id, BODY, now=NOW)
    assert module.verify(secret, headers, BODY, now=NOW) == msg_id
