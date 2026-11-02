"""R11: 同じ番号の注文のデータは 1 回だけ登録する (2 回届いても 1 件)。

守り方は Inbox の番号の一意の制約と、services.receive (2 回目は 1 回目の行を返す)。
鍵はテストのたびに作り、環境変数に置く。固定の鍵の文字列をファイルに書かない。
"""

import json
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
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


def test_same_id_twice_makes_one_row(connection, secret, client):
    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)
    headers = signing.headers_for(secret, msg_id, body)

    first = _post(client, body, headers)
    second = _post(client, body, headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json() == {"status": "received", "id": msg_id}
    assert Inbox.objects.count() == 1


def test_receive_returns_the_first_row_on_replay(connection, secret):
    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)
    headers = signing.headers_for(secret, msg_id, body)

    first = services.receive(body, headers)
    second = services.receive(body, headers)

    assert isinstance(first, Inbox)
    assert isinstance(second, Inbox)
    assert second.pk == first.pk
    assert Inbox.objects.count() == 1


def test_different_ids_make_two_rows(connection, secret, client):
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    for msg_id in ids:
        body = _order_body(msg_id)
        response = _post(client, body, signing.headers_for(secret, msg_id, body))
        assert response.status_code == 200

    assert Inbox.objects.count() == 2
    assert {str(value) for value in Inbox.objects.values_list("message_id", flat=True)} == set(ids)


def test_db_unique_constraint_backs_r11(partner):
    message_id = uuid.uuid4()
    Inbox.objects.create(message_id=message_id, partner=partner, kind="order", payload={})

    with pytest.raises(IntegrityError), transaction.atomic():
        Inbox.objects.create(message_id=message_id, partner=partner, kind="order", payload={})
    assert Inbox.objects.count() == 1
