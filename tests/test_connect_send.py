"""注文を送る関数 (connect.services.send_order) のテスト。

相手の受け口への HTTP は requests.post を偽の物に差し替えて見る (外へは出ない)。
鍵はテストのたびに作り、環境変数に置く。固定の鍵の文字列をファイルに書かない。
取引先・品目・会社は作り物。
"""

import json
import logging
from datetime import date
from decimal import Decimal

import pytest
import requests
from django.contrib.auth import get_user_model
from django.db import connection as db_connection

from seizou.connect import services, signing
from seizou.connect.models import Inbox, Outbox
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrder
from tests.test_connect_signing import _new_secret

ENV_NAME = "SEIZOU_PARTNER_TESTSUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"

ORDER_KEYS = {"id", "kind", "sent_at", "order_no", "order_date", "buyer", "supplier", "note", "lines"}
LINE_KEYS = {"line_no", "part_no", "part_name", "qty", "unit", "unit_price", "due_date", "note"}


class FakeResponse:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self._data = data

    def json(self):
        if self._data is None:
            raise ValueError("JSON ではない")
        return self._data


class FakePost:
    """requests.post の代わり。受けた引数を控え、決めた返事を返す (か、例外を投げる)。"""

    def __init__(self, reply=None, raises=None, inside=None):
        self.reply = reply if reply is not None else FakeResponse(200, {"status": "received"})
        self.raises = raises
        self.inside = inside
        self.calls = []

    def __call__(self, url, data=None, headers=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "data": data,
                "headers": headers,
                "timeout": timeout,
                "in_atomic_block": db_connection.in_atomic_block,
            }
        )
        if self.inside is not None:
            return self.inside(data, headers)
        if self.raises is not None:
            raise self.raises
        return self.reply


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(ENV_NAME, value)
    return value


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の外注先", "role": "subcontractor"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def send_connection(subcontractor, user):
    return services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)


def _create(subcontractor, item, user):
    return purchasing_services.create_purchase_order(
        subcontractor,
        [
            {
                "item": item,
                "qty": Decimal("10"),
                "unit_price": Decimal("25.5"),
                "due_date": date(2026, 11, 20),
                "note": "作り物の明細の備考",
            }
        ],
        user,
        order_date=date(2026, 11, 2),
        note="作り物の発注の備考",
    )


@pytest.mark.django_db
def test_send_records_outbox_and_marks_sent(monkeypatch, user, secret, subcontractor, item, send_connection):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    po = _create(subcontractor, item, user)

    assert po.status == PurchaseOrder.STATUS_SENT
    outbox = Outbox.objects.get()
    assert outbox.response == "受け取った"
    assert outbox.purchase_order == po and outbox.connection == send_connection

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == PARTNER_URL
    assert call["timeout"] == 10
    assert call["headers"]["Content-Type"] == "application/json"
    # 見出しの 3 つで署名が通り、番号は Outbox の番号と同じ
    assert signing.verify(secret, call["headers"], call["data"]) == str(outbox.message_id)

    sent = json.loads(call["data"].decode("utf-8"))
    assert set(sent) == ORDER_KEYS
    assert sent["id"] == str(outbox.message_id)
    assert sent["kind"] == "order"
    assert sent["order_no"] == po.po_no
    assert sent["order_date"] == "2026-11-02"
    assert set(sent["buyer"]) == {"id", "name"}
    assert sent["supplier"] == {"code": "S001", "name": "作り物の外注先"}
    assert sent["note"] == "作り物の発注の備考"
    assert "+09:00" in sent["sent_at"]
    (line,) = sent["lines"]
    assert set(line) == LINE_KEYS
    assert line == {
        "line_no": 1,
        "part_no": "T-100",
        "part_name": "作り物の軸",
        "qty": "10.00",
        "unit": "個",
        "unit_price": "25.50",
        "due_date": "2026-11-20",
        "note": "作り物の明細の備考",
    }
    # 本文は鍵の並びを固定した JSON の bytes そのまま
    assert call["data"] == json.dumps(
        sent, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


@pytest.mark.django_db(transaction=True)
def test_http_is_sent_outside_atomic(monkeypatch, user, secret, subcontractor, item, send_connection):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    _create(subcontractor, item, user)

    assert len(fake.calls) == 1
    assert fake.calls[0]["in_atomic_block"] is False


@pytest.mark.django_db
def test_rejected_reply_is_recorded_and_stays_draft(monkeypatch, user, secret, subcontractor, item, send_connection):
    reason = "登録した取引先の署名と合いません。"
    fake = FakePost(reply=FakeResponse(400, {"status": "rejected", "message": reason + "あ" * 300}))
    monkeypatch.setattr("requests.post", fake)

    po = _create(subcontractor, item, user)

    assert po.status == PurchaseOrder.STATUS_DRAFT
    response = Outbox.objects.get().response
    assert response.startswith("断られた: " + reason)
    assert len(response) == len("断られた: ") + 200


@pytest.mark.django_db
def test_other_status_is_unreadable(monkeypatch, user, secret, subcontractor, item, send_connection):
    monkeypatch.setattr("requests.post", FakePost(reply=FakeResponse(500)))

    po = _create(subcontractor, item, user)

    assert po.status == PurchaseOrder.STATUS_DRAFT
    assert Outbox.objects.get().response == "返事が読めません (HTTP 500)"


@pytest.mark.django_db
def test_unreachable_is_recorded(monkeypatch, user, secret, subcontractor, item, send_connection):
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))

    po = _create(subcontractor, item, user)

    assert po.status == PurchaseOrder.STATUS_DRAFT
    assert Outbox.objects.get().response == (
        "届きません: 相手につながりません。つながりの URL と、相手が立っているかを確かめてください。"
    )


@pytest.mark.django_db
def test_missing_secret_is_recorded_without_http(monkeypatch, user, subcontractor, item, send_connection):
    monkeypatch.delenv(ENV_NAME, raising=False)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    po = _create(subcontractor, item, user)

    assert fake.calls == []
    assert po.status == PurchaseOrder.STATUS_DRAFT
    response = Outbox.objects.get().response
    assert ENV_NAME in response and "値が無い" in response


@pytest.mark.django_db
def test_malformed_secret_is_recorded_without_http(monkeypatch, user, subcontractor, item, send_connection):
    monkeypatch.setenv(ENV_NAME, "not-a-prefixed-key")
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    po = _create(subcontractor, item, user)

    assert fake.calls == []
    assert po.status == PurchaseOrder.STATUS_DRAFT
    response = Outbox.objects.get().response
    assert ENV_NAME in response and "not-a-prefixed-key" not in response


@pytest.mark.django_db
def test_no_connection_is_value_error(monkeypatch, user, secret, subcontractor, item):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    po = _create(subcontractor, item, user)  # つながりが無いので送らない

    with pytest.raises(ValueError, match="へのつながりがありません"):
        services.send_order(po, user)
    assert fake.calls == []
    assert Outbox.objects.count() == 0


@pytest.mark.django_db
def test_cancelled_order_is_value_error(monkeypatch, user, secret, subcontractor, item):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    po = _create(subcontractor, item, user)
    services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)
    cancelled = purchasing_services.cancel_purchase_order(po, user)

    with pytest.raises(ValueError, match="取り消してある"):
        services.send_order(cancelled, user)
    assert fake.calls == []
    assert Outbox.objects.count() == 0


@pytest.mark.django_db
def test_round_trip_into_receive(monkeypatch, user, secret, subcontractor, item, send_connection):
    # 同じ DB に、同じ鍵の置き場の名前で向き「受ける」のつながりを作る (相手の会社の役)。
    buyer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の発注元", "role": "customer"}, user
    )
    services.register_connection(buyer, "", ENV_NAME, "receive", user)

    def into_receive(data, headers):
        result = services.receive(data, headers)
        assert not isinstance(result, str), result
        return FakeResponse(200, {"status": "received", "id": str(result.message_id)})

    monkeypatch.setattr("requests.post", FakePost(inside=into_receive))

    po = _create(subcontractor, item, user)

    inbox = Inbox.objects.get()
    outbox = Outbox.objects.get()
    assert inbox.message_id == outbox.message_id
    assert inbox.partner == buyer
    assert [line["part_no"] for line in inbox.payload["lines"]] == ["T-100"]
    assert inbox.payload["order_no"] == po.po_no
    assert po.status == PurchaseOrder.STATUS_SENT


@pytest.mark.django_db
def test_secret_value_not_in_outbox_or_logs(monkeypatch, caplog, user, secret, subcontractor, item, send_connection):
    caplog.set_level(logging.DEBUG, logger="seizou.connect")
    monkeypatch.setattr("requests.post", FakePost())
    _create(subcontractor, item, user)
    monkeypatch.setattr("requests.post", FakePost(raises=requests.Timeout("作り物の待ち切れ")))
    _create(subcontractor, item, user)

    assert Outbox.objects.count() == 2
    for outbox in Outbox.objects.all():
        assert secret not in outbox.response
        assert secret[len("whsec_"):] not in outbox.response
    assert "注文を送った" in caplog.text
    assert secret not in caplog.text
    assert secret[len("whsec_"):] not in caplog.text


@pytest.mark.django_db
def test_resend_reuses_the_first_number_so_the_receiver_keeps_one_row(
    monkeypatch, user, secret, subcontractor, item, send_connection
):
    """送り直し (2 つのタブからでも) は 1 回目の番号を使い回し、相手の受信箱は 1 行のまま (初見テスト 2 回目の所見)。"""
    buyer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の発注元", "role": "customer"}, user
    )
    services.register_connection(buyer, "", ENV_NAME, "receive", user)

    def into_receive(data, headers):
        result = services.receive(data, headers)
        assert not isinstance(result, str), result
        return FakeResponse(200, {"status": "received", "id": str(result.message_id)})

    monkeypatch.setattr("requests.post", FakePost(inside=into_receive))

    po = _create(subcontractor, item, user)
    services.send_order(po, user)

    outboxes = list(Outbox.objects.order_by("pk"))
    assert len(outboxes) == 2
    assert outboxes[0].message_id == outboxes[1].message_id
    assert Inbox.objects.count() == 1
    assert outboxes[1].response == "受け取った"
