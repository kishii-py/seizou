"""connect の受け口と、つながりの登録のテスト。

鍵はテストのたびに作り、環境変数に置く。固定の鍵の文字列をファイルに書かない。
取引先と本文は作り物。
"""

import json
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from seizou.connect import services, signing
from seizou.connect.models import Connection, Inbox
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
        "sent_at": "2026-11-02T12:00:00+09:00",
        "lines": [
            {
                "item_name": "作り物の軸",
                "item_code": "TEST-001",
                "quantity": "10",
                "unit": "個",
                "due_date": "2026-11-20",
                "note": "",
            }
        ],
    }
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _post(client, body, headers):
    return client.post(
        reverse("connect:receive"),
        data=body,
        content_type="application/json",
        headers=headers,
    )


def test_valid_order_is_stored(connection, secret, partner):
    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)
    headers = signing.headers_for(secret, msg_id, body)

    # CSRF の確かめを有効にした client でも通る。ログインもしない。
    response = _post(Client(enforce_csrf_checks=True), body, headers)

    assert response.status_code == 200
    assert response.json() == {"status": "received", "id": msg_id}
    inbox = Inbox.objects.get()
    assert inbox.message_id == uuid.UUID(msg_id)
    assert inbox.partner == partner
    assert inbox.kind == Inbox.KIND_ORDER
    assert inbox.payload == json.loads(body)
    assert inbox.status == Inbox.STATUS_RECEIVED
    assert inbox.received_at is not None
    assert timezone.is_aware(inbox.received_at)


def test_non_json_body_is_rejected(connection, secret, client):
    msg_id = str(uuid.uuid4())
    for body in (b"not json", b"[1, 2, 3]", "注文".encode("cp932")):
        headers = signing.headers_for(secret, msg_id, body)

        response = _post(client, body, headers)

        assert response.status_code == 400
        assert response.json() == {
            "status": "rejected",
            "message": services.MESSAGE_INVALID_BODY,
        }
    assert Inbox.objects.count() == 0


def test_non_uuid_id_is_rejected(connection, secret, client):
    msg_id = "order-0001"
    body = _order_body(msg_id)
    headers = signing.headers_for(secret, msg_id, body)

    response = _post(client, body, headers)

    assert response.status_code == 400
    assert response.json()["message"] == services.MESSAGE_INVALID_ID
    assert Inbox.objects.count() == 0


def test_get_is_not_allowed(connection, client):
    response = client.get(reverse("connect:receive"))

    assert response.status_code == 405
    assert Inbox.objects.count() == 0


def test_register_connection_validates_direction_and_env_name(partner, user):
    for direction in ("both", "", "SEND"):
        with pytest.raises(ValueError):
            services.register_connection(partner, "", ENV_NAME, direction, user)

    for name in ("seizou_partner_testco_secret", "1ABC", "", "SEIZOU-PARTNER", "A B"):
        with pytest.raises(ValueError):
            services.register_connection(partner, "", name, "receive", user)

    # 鍵の値らしい物 (= を含む) を渡すと断り、文に値を出さない。
    value = f"{ENV_NAME}={_new_secret()}"
    with pytest.raises(ValueError) as excinfo:
        services.register_connection(partner, "", value, "receive", user)
    assert value not in str(excinfo.value)

    # 送る向きで URL の形でない物は断る。
    with pytest.raises(ValueError):
        services.register_connection(partner, "not a url", ENV_NAME, "send", user)
    assert Connection.objects.count() == 0

    received = services.register_connection(partner, "", ENV_NAME, "receive", user)
    sent = services.register_connection(
        partner, "http://127.0.0.1:8002/connect/receive/order/", ENV_NAME, "send", user
    )
    assert isinstance(received, Connection)
    assert (received.partner, received.url, received.secret_env, received.direction) == (
        partner,
        "",
        ENV_NAME,
        "receive",
    )
    assert sent.direction == "send"
    # 同じ取引先に、送ると受けるの 2 行を持てる。
    assert list(partner.connections.values_list("direction", flat=True)) == [
        "receive",
        "send",
    ]


def test_receive_uses_signing_verify(connection, secret, monkeypatch):
    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)
    headers = signing.headers_for(secret, msg_id, body)
    calls = []

    def fake_verify(given_secret, given_headers, given_body, **kwargs):
        calls.append((given_secret, given_headers, given_body))
        return msg_id

    monkeypatch.setattr(signing, "verify", fake_verify)

    result = services.receive(body, headers)

    assert calls == [(secret, headers, body)]
    assert isinstance(result, Inbox)
    assert result.message_id == uuid.UUID(msg_id)


def test_url_is_included_under_connect_prefix():
    assert reverse("connect:receive") == "/connect/receive/order/"
