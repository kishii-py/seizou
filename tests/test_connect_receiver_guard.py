"""受け口の断り (本体の追記 10): 知らない kind と、明細の無い注文は 400 で断り、受信箱に残さない。kind の無い注文は今までどおり入る。

注文のデータは送る側と同じ形 (tests/test_r10_inbox_accept.py の _payload と _line) を署名して受け口 (connect:receive) に POST する。
鍵はテストのたびに作り、環境変数に置く。固定の鍵の文字列をファイルに書かない。品番と会社名は作り物。
"""

import json
import logging
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services, signing
from seizou.connect.models import Inbox
from seizou.masters.services import create_partner
from tests.test_connect_signing import _new_secret
from tests.test_r10_inbox_accept import _line, _payload

ENV_NAME = "SEIZOU_PARTNER_GUARDCO_SECRET"


@pytest.fixture
def secret(db, monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(ENV_NAME, value)
    user = get_user_model().objects.create_user("tester", password="pass-for-tests-only")
    partner = create_partner({"code": "GD01", "name": "作り物の得意先", "role": "customer"}, user)
    services.register_connection(partner, "", ENV_NAME, "receive", user)
    return value


def _post(client, secret, data):
    msg_id = data["id"]
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return client.post(
        reverse("connect:receive"),
        data=raw,
        content_type="application/json",
        headers=signing.headers_for(secret, msg_id, raw),
    )


def _order(**changes):
    data = _payload(str(uuid.uuid4()), [_line(1, "X-100")])
    data.update(changes)
    return data


def test_unknown_kind_is_refused(client, secret, caplog):
    caplog.set_level(logging.WARNING, logger="seizou.connect")
    long_kind = "作り物の知らない種類" * 10

    response = _post(client, secret, _order(kind=long_kind))

    assert response.status_code == 400
    assert response.json()["message"] == services.MESSAGE_UNKNOWN_KIND.format(kind=long_kind[:40])
    assert Inbox.objects.count() == 0
    assert any("知らない種類の通知です" in record.getMessage() for record in caplog.records)

    response = _post(client, secret, _order(kind=["order"]))
    assert response.status_code == 400
    assert response.json()["message"] == services.MESSAGE_UNKNOWN_KIND.format(kind="(字でない値)")
    assert Inbox.objects.count() == 0


def test_order_without_kind_is_received(client, secret):
    data = _order()
    del data["kind"]

    response = _post(client, secret, data)

    assert response.status_code == 200
    inbox = Inbox.objects.get()
    assert inbox.source == Inbox.SOURCE_DIRECT
    assert inbox.kind == Inbox.KIND_ORDER


def test_order_without_lines_is_refused(client, secret):
    data = _order()
    del data["lines"]

    response = _post(client, secret, data)

    assert response.status_code == 400
    assert response.json()["message"] == services.MESSAGE_RECEIVE_NO_LINES
    assert Inbox.objects.count() == 0


@pytest.mark.parametrize("lines", [[], "X-100", {"line_no": 1}], ids=["empty", "text", "dict"])
def test_order_with_empty_lines_is_refused(client, secret, lines):
    response = _post(client, secret, _order(lines=lines))

    assert response.status_code == 400
    assert response.json()["message"] == services.MESSAGE_RECEIVE_NO_LINES
    assert Inbox.objects.count() == 0
