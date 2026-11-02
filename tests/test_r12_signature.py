"""R12: 受け口は、登録した取引先からの、署名の合う物だけを受ける。

守り方は services.receive (向きが受けるのつながりの鍵で signing.verify を呼ぶ)。
断った物は 400 で、受信箱に入れない。
鍵はテストのたびに作り、環境変数に置く。固定の鍵の文字列をファイルに書かない。
"""

import json
import logging
import time
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services, signing
from seizou.connect.models import Inbox
from seizou.masters.services import create_partner
from tests.test_connect_signing import _new_secret

ENV_NAME = "SEIZOU_PARTNER_TESTCO_SECRET"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(ENV_NAME, value)
    return value


@pytest.fixture
def partner(user):
    return create_partner(
        {"code": "TC01", "name": "作り物の取引先", "role": "customer"}, user
    )


@pytest.fixture
def connection(partner, user, secret):
    return services.register_connection(partner, "", ENV_NAME, "receive", user)


def _order_body(msg_id):
    data = {
        "id": msg_id,
        "kind": "order",
        "sender": "作り物の送る会社",
        "receiver": "作り物の受ける会社",
        "lines": [{"item_code": "TEST-001", "quantity": "10", "unit": "個"}],
    }
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _post(client, body, headers):
    return client.post(
        reverse("connect:receive"),
        data=body,
        content_type="application/json",
        headers=headers,
    )


def _assert_rejected(response, message):
    assert response.status_code == 400
    assert response.json() == {"status": "rejected", "message": message}
    assert Inbox.objects.count() == 0


def test_wrong_key_is_rejected_and_not_stored(connection, client):
    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)
    headers = signing.headers_for(_new_secret(), msg_id, body)

    _assert_rejected(_post(client, body, headers), services.MESSAGE_SIGNATURE_MISMATCH)


def test_tampered_body_is_rejected(connection, secret, client):
    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)
    headers = signing.headers_for(secret, msg_id, body)

    tampered = bytearray(body)
    index = body.index(b'"10"') + 1
    tampered[index] = ord("2")
    assert len(tampered) == len(body)

    _assert_rejected(
        _post(client, bytes(tampered), headers), services.MESSAGE_SIGNATURE_MISMATCH
    )


def test_unregistered_sender_is_rejected(db, secret, client):
    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)
    headers = signing.headers_for(secret, msg_id, body)

    response = _post(client, body, headers)

    # 未登録の相手と鍵違いを、外から見分けられない (文が同じ)。
    _assert_rejected(response, services.MESSAGE_SIGNATURE_MISMATCH)


def test_send_only_connection_does_not_receive(partner, user, secret, client):
    services.register_connection(
        partner, "http://127.0.0.1:8002/connect/receive/order/", ENV_NAME, "send", user
    )
    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)
    headers = signing.headers_for(secret, msg_id, body)

    _assert_rejected(_post(client, body, headers), services.MESSAGE_SIGNATURE_MISMATCH)


def test_timestamp_outside_tolerance_is_rejected(connection, secret, client):
    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)
    headers = signing.headers_for(secret, msg_id, body, now=time.time() - 301)

    _assert_rejected(_post(client, body, headers), services.MESSAGE_OUT_OF_TOLERANCE)


def test_missing_headers_are_rejected(connection, client):
    body = _order_body(str(uuid.uuid4()))

    _assert_rejected(_post(client, body, {}), services.MESSAGE_MISSING_HEADER)


def test_unset_env_var_is_rejected(connection, secret, client, monkeypatch):
    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)
    headers = signing.headers_for(secret, msg_id, body)
    monkeypatch.delenv(ENV_NAME)

    _assert_rejected(_post(client, body, headers), services.MESSAGE_SIGNATURE_MISMATCH)


def test_secret_never_appears_in_response_or_log(
    partner, user, connection, secret, client, caplog, monkeypatch
):
    caplog.set_level(logging.DEBUG, logger="seizou.connect")
    # 鍵の形が壊れたつながり (設定の誤り) も 1 つ置き、その値もログに出ないことを見る。
    broken_env = "SEIZOU_PARTNER_BROKEN_SECRET"
    broken = _new_secret().removeprefix("whsec_")
    monkeypatch.setenv(broken_env, broken)
    services.register_connection(partner, "", broken_env, "receive", user)

    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)
    good = signing.headers_for(secret, msg_id, body)
    wrong = signing.headers_for(_new_secret(), msg_id, body)
    stale = signing.headers_for(secret, msg_id, body, now=time.time() - 301)

    responses = [
        _post(client, body, good),
        _post(client, body, good),
        _post(client, body, wrong),
        _post(client, body, stale),
        _post(client, body, {}),
    ]

    assert [r.status_code for r in responses] == [200, 200, 400, 400, 400]
    # 断った物と、飛ばした鍵のログが出ている (空のログで通らないように)。
    assert any("受け口で断った" in r.getMessage() for r in caplog.records)
    assert any(broken_env in r.getMessage() for r in caplog.records)
    for value in (secret, broken):
        for response in responses:
            assert value not in response.content.decode("utf-8")
        assert value not in caplog.text
