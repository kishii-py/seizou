"""注文回答 (connect.services.send_reply と、受け口が元の発注に当てる所) のテスト。

承認 (受け入れ) か断りをすると、その結果を注文を送ってきた相手の受け口へ署名して返し、
相手の受け口は元の発注に 1 回だけ当てる。届かなくても承認と断りは成立する。

1 つの DB に、注文を送る会社と受ける会社の両方の役を置く (test_connect_send.py の test_round_trip_into_receive の形)。
取引先は 2 つ。協力企業 S001 には向き「送る」(注文を送る相手) と向き「受ける」(注文回答を受ける) のつながり、
得意先 C001 には向き「受ける」(注文を受ける) と向き「送る」(注文回答を返す) のつながりを置く。
鍵の置き場は注文の向きと注文回答の向きで分ける。1 つの鍵を 4 行で使い回すと、受け口が向き「受ける」の
最初に合ったつながりの取引先を選び、注文と注文回答の取引先を取り違えるため (1 つの DB に 2 社の役を置く試験だけの事)。
相手への HTTP は requests.post を偽の物に差し替え、受けた本文を services.receive に渡す (外へは出ない)。
鍵はテストのたびに作り、環境変数に置く。固定の鍵の文字列をファイルに書かない。取引先・品目・会社は作り物。
"""

import json
import logging
import uuid
from datetime import date
from decimal import Decimal

import pytest
import requests
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse

from seizou.connect import services, signing
from seizou.connect.models import Inbox, Outbox
from seizou.masters import services as masters_services
from seizou.orders.models import Order
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrder
from tests.test_connect_send import FakePost, FakeResponse
from tests.test_connect_signing import _new_secret
from tests.test_r10_inbox_accept import _line, _payload

ORDER_ENV = "SEIZOU_PARTNER_REPLYCO_ORDER_SECRET"
REPLY_ENV = "SEIZOU_PARTNER_REPLYCO_REPLY_SECRET"
SUPPLIER_URL = "https://supplier.example.test/connect/receive/order/"
BUYER_URL = "https://buyer.example.test/connect/receive/order/"

REPLY_KEYS = {
    "id",
    "kind",
    "sent_at",
    "order_id",
    "order_no",
    "result",
    "supplier_order_no",
    "reason",
    "supplier",
}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def order_secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(ORDER_ENV, value)
    return value


@pytest.fixture
def reply_secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(REPLY_ENV, value)
    return value


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の発注元", "role": "customer"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def own_item(user):
    return masters_services.create_item(
        {"part_no": "B-200", "name": "作り物の受ける側の部品", "unit": "個"}, user
    )


def _connect(user, subcontractor, customer, *, reply_back=True):
    """2 社の役のつながりを置く。reply_back が偽なら、注文回答を返す向き (C001 の送る) を置かない。"""
    services.register_connection(subcontractor, SUPPLIER_URL, ORDER_ENV, "send", user)
    services.register_connection(customer, "", ORDER_ENV, "receive", user)
    services.register_connection(subcontractor, "", REPLY_ENV, "receive", user)
    if reply_back:
        services.register_connection(customer, BUYER_URL, REPLY_ENV, "send", user)


@pytest.fixture
def connections(user, subcontractor, customer, order_secret, reply_secret):
    _connect(user, subcontractor, customer)


def _into_receive(data, headers):
    """相手の受け口の役。受け口の view と同じく、通れば 200、断れば 400 と理由を返す。"""
    result = services.receive(data, headers)
    if isinstance(result, str):
        return FakeResponse(400, {"status": "rejected", "message": result})
    return FakeResponse(200, {"status": "received", "id": str(result.message_id)})


def _send_order(subcontractor, item, user):
    return purchasing_services.create_purchase_order(
        subcontractor,
        [
            {
                "item": item,
                "qty": Decimal("10"),
                "unit_price": Decimal("25.5"),
                "due_date": date(2026, 11, 20),
            }
        ],
        user,
        order_date=date(2026, 11, 2),
    )


def _reply_raw(po, *, result="accepted", order_id=None, order_no=None, reason=""):
    """手で作る注文回答の本文 (送る側と同じ bytes の作り方)。返す物は (番号, 本文の bytes)。"""
    msg_id = str(uuid.uuid4())
    if order_id is None:
        order_id = str(Outbox.objects.filter(purchase_order=po).order_by("pk").first().message_id)
    data = {
        "id": msg_id,
        "kind": "order_reply",
        "sent_at": "2026-11-03T10:00:00+09:00",
        "order_id": order_id,
        "order_no": po.po_no if order_no is None else order_no,
        "result": result,
        "supplier_order_no": "SO-0001" if result == "accepted" else "",
        "reason": reason,
        "supplier": {"id": "b-company", "name": "作り物の受ける会社"},
    }
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return msg_id, raw


def _post(client, raw, headers):
    return client.post(
        reverse("connect:receive"), data=raw, content_type="application/json", headers=headers
    )


def _signed_post(client, raw, msg_id, secret):
    return _post(client, raw, signing.headers_for(secret, msg_id, raw))


def _reply_call_headers(call):
    """偽の post が受けた見出しから、署名の 3 つだけを取り出す (Content-Type は test client が付ける)。"""
    return {key: value for key, value in call["headers"].items() if key != "Content-Type"}


@pytest.mark.django_db
def test_accept_sends_reply_and_marks_purchase_order_accepted(
    monkeypatch, user, reply_secret, subcontractor, customer, item, own_item, connections
):
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    po = _send_order(subcontractor, item, user)
    assert po.status == PurchaseOrder.STATUS_SENT
    inbox = Inbox.objects.get()
    assert inbox.partner == customer

    order = services.accept(inbox, {"T-100": own_item}, user)

    assert len(fake.calls) == 2
    call = fake.calls[1]
    assert call["url"] == BUYER_URL
    assert call["timeout"] == 10
    assert call["headers"]["Content-Type"] == "application/json"
    inbox.refresh_from_db()
    assert inbox.reply_message_id is not None
    # 見出しの 3 つで署名が通り、番号は届いた注文の注文回答の番号と同じ
    assert signing.verify(reply_secret, call["headers"], call["data"]) == str(inbox.reply_message_id)

    sent = json.loads(call["data"].decode("utf-8"))
    assert set(sent) == REPLY_KEYS
    assert sent["id"] == str(inbox.reply_message_id)
    assert sent["kind"] == "order_reply"
    assert sent["result"] == "accepted"
    assert sent["order_no"] == po.po_no
    assert sent["order_id"] == str(Outbox.objects.get().message_id)
    assert sent["supplier_order_no"] == order.order_no
    assert sent["reason"] == ""
    assert set(sent["supplier"]) == {"id", "name"}
    assert "+09:00" in sent["sent_at"]
    # 本文は鍵の並びを固定した JSON の bytes そのまま
    assert call["data"] == json.dumps(
        sent, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ACCEPTED
    assert po.get_status_display() == "相手が受け入れた"
    assert po.replied_at is not None
    assert po.reply_message_id == inbox.reply_message_id
    assert inbox.status == Inbox.STATUS_ACCEPTED
    assert inbox.reply_sent_at is not None
    assert inbox.reply_response == "受け取った"
    # 注文回答は届いた注文の行にしない (発注した側の届いた注文は増えない)
    assert Inbox.objects.count() == 1


@pytest.mark.django_db
def test_reject_sends_reply_with_reason_and_marks_rejected(
    monkeypatch, user, subcontractor, customer, item, connections
):
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    po = _send_order(subcontractor, item, user)
    inbox = Inbox.objects.get()

    services.reject(inbox, "納期に間に合わないため", user)

    sent = json.loads(fake.calls[1]["data"].decode("utf-8"))
    assert sent["result"] == "rejected"
    assert sent["reason"] == "納期に間に合わないため"
    assert sent["supplier_order_no"] == ""
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_REJECTED
    assert po.get_status_display() == "断られた"
    assert po.reply_note == "納期に間に合わないため"
    assert po.replied_at is not None
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_REJECTED
    assert inbox.reply_response == "受け取った"


@pytest.mark.django_db
def test_same_reply_twice_applies_once(
    monkeypatch, client, user, subcontractor, customer, item, own_item, connections
):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_order(subcontractor, item, user)
    inbox = Inbox.objects.get()
    # 注文回答は相手に渡さずに控える (受け口へは test client で 2 回送る)
    held = FakePost()
    monkeypatch.setattr("requests.post", held)
    services.accept(inbox, {"T-100": own_item}, user)
    (call,) = held.calls
    headers = _reply_call_headers(call)

    first = _post(client, call["data"], headers)
    po.refresh_from_db()
    replied_at = po.replied_at
    second = _post(client, call["data"], headers)

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"] == headers[signing.HEADER_ID]
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ACCEPTED
    assert po.replied_at == replied_at
    assert str(po.reply_message_id) == headers[signing.HEADER_ID]

    # 注文回答の番号が空の発注は何件でも並べる (null は一意の制約に当たらない)。同じ番号は DB が止める。
    monkeypatch.setattr("requests.post", FakePost(reply=FakeResponse(500)))
    others = [_send_order(subcontractor, item, user) for _ in range(2)]
    assert [other.reply_message_id for other in others] == [None, None]
    with pytest.raises(IntegrityError), transaction.atomic():
        PurchaseOrder.objects.filter(pk=others[0].pk).update(reply_message_id=po.reply_message_id)


@pytest.mark.django_db
def test_reply_without_valid_signature_is_refused(
    monkeypatch, client, user, subcontractor, customer, item, connections
):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_order(subcontractor, item, user)
    msg_id, raw = _reply_raw(po)

    missing = _post(client, raw, {})
    wrong = _signed_post(client, raw, msg_id, _new_secret())

    assert missing.status_code == 400
    assert missing.json() == {"status": "rejected", "message": services.MESSAGE_MISSING_HEADER}
    assert wrong.status_code == 400
    assert wrong.json() == {"status": "rejected", "message": services.MESSAGE_SIGNATURE_MISMATCH}
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_SENT
    assert po.replied_at is None and po.reply_message_id is None


@pytest.mark.django_db
def test_reply_for_another_partners_order_is_refused(
    monkeypatch, client, user, order_secret, subcontractor, customer, item, connections
):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_order(subcontractor, item, user)
    msg_id, raw = _reply_raw(po)

    # 注文の向きの鍵 = 得意先 C001 の向き「受ける」のつながりで通るが、発注の取引先は S001。
    response = _signed_post(client, raw, msg_id, order_secret)

    assert response.status_code == 400
    assert "その取引先への物ではありません" in response.json()["message"]
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_SENT
    assert po.reply_message_id is None


@pytest.mark.django_db
def test_reply_with_unknown_order_or_wrong_order_id_is_refused(
    monkeypatch, client, user, reply_secret, subcontractor, customer, item, connections
):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_order(subcontractor, item, user)

    unknown_id, unknown = _reply_raw(po, order_no="PO-9999")
    wrong_id, wrong = _reply_raw(po, order_id=str(uuid.uuid4()))
    broken_id, broken = _reply_raw(po, order_id="uuid-でない字")
    shape_id, shape = _reply_raw(po, result="maybe")

    responses = {
        "unknown": _signed_post(client, unknown, unknown_id, reply_secret),
        "wrong": _signed_post(client, wrong, wrong_id, reply_secret),
        "broken": _signed_post(client, broken, broken_id, reply_secret),
        "shape": _signed_post(client, shape, shape_id, reply_secret),
    }

    assert {name: r.status_code for name, r in responses.items()} == dict.fromkeys(responses, 400)
    assert "PO-9999 がありません" in responses["unknown"].json()["message"]
    assert responses["wrong"].json()["message"] == services.MESSAGE_REPLY_ORDER_ID
    assert responses["broken"].json()["message"] == services.MESSAGE_REPLY_ORDER_ID
    assert responses["shape"].json()["message"] == services.MESSAGE_REPLY_RESULT
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_SENT


@pytest.mark.django_db
def test_reply_with_non_string_fields_is_refused(
    monkeypatch, client, user, reply_secret, subcontractor, customer, item, connections
):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_order(subcontractor, item, user)
    msg_id = str(uuid.uuid4())
    raw = json.dumps(
        {"id": msg_id, "kind": "order_reply", "order_no": ["PO"], "order_id": 1, "result": "accepted"}
    ).encode("utf-8")

    response = _signed_post(client, raw, msg_id, reply_secret)

    assert response.status_code == 400
    assert response.json()["message"] == services.MESSAGE_REPLY_SHAPE
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_SENT


@pytest.mark.django_db
def test_reply_marks_sent_first_when_still_draft(
    monkeypatch, client, user, reply_secret, subcontractor, customer, item, connections
):
    # 送った時の返事が落ちた (相手は 500) ので、発注は「作成」のまま。送った注文の行はある。
    monkeypatch.setattr("requests.post", FakePost(reply=FakeResponse(500)))
    draft = _send_order(subcontractor, item, user)
    assert draft.status == PurchaseOrder.STATUS_DRAFT
    msg_id, raw = _reply_raw(draft)

    response = _signed_post(client, raw, msg_id, reply_secret)

    assert response.status_code == 200
    draft.refresh_from_db()
    assert draft.status == PurchaseOrder.STATUS_ACCEPTED
    assert str(draft.reply_message_id) == msg_id

    cancelled = purchasing_services.cancel_purchase_order(_send_order(subcontractor, item, user), user)
    msg_id, raw = _reply_raw(cancelled)

    response = _signed_post(client, raw, msg_id, reply_secret)

    assert response.status_code == 400
    assert "取り消した発注" in response.json()["message"]
    cancelled.refresh_from_db()
    assert cancelled.status == PurchaseOrder.STATUS_CANCELLED
    assert cancelled.replied_at is None and cancelled.reply_message_id is None


@pytest.mark.django_db(transaction=True)
def test_reply_is_sent_outside_atomic(
    monkeypatch, user, subcontractor, customer, item, own_item, connections
):
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    _send_order(subcontractor, item, user)
    _send_order(subcontractor, item, user)
    first, second = Inbox.objects.order_by("pk")

    services.accept(first, {"T-100": own_item}, user)
    services.reject(second, "作り物の断りの理由", user)

    # 注文 2 回と注文回答 2 回。どれも atomic の外 (R17)。
    assert len(fake.calls) == 4
    assert [call["url"] for call in fake.calls[2:]] == [BUYER_URL, BUYER_URL]
    assert [call["in_atomic_block"] for call in fake.calls] == [False] * 4
    assert sorted(PurchaseOrder.objects.values_list("status", flat=True)) == [
        PurchaseOrder.STATUS_ACCEPTED,
        PurchaseOrder.STATUS_REJECTED,
    ]


@pytest.mark.django_db
def test_accept_succeeds_when_reply_is_unreachable(
    monkeypatch, user, subcontractor, customer, item, own_item, connections
):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_order(subcontractor, item, user)
    inbox = Inbox.objects.get()
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))

    order = services.accept(inbox, {"T-100": own_item}, user)

    assert isinstance(order, Order)
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_ACCEPTED
    assert inbox.order == order
    assert inbox.reply_sent_at is None
    assert inbox.reply_response.startswith("届きません")
    assert inbox.reply_message_id is not None
    assert inbox.reply_state().startswith("届きません")
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_SENT


@pytest.mark.django_db
def test_accept_without_send_connection_records_and_succeeds(
    monkeypatch, user, order_secret, reply_secret, subcontractor, customer, item, own_item
):
    _connect(user, subcontractor, customer, reply_back=False)
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    _send_order(subcontractor, item, user)
    inbox = Inbox.objects.get()
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    order = services.accept(inbox, {"T-100": own_item}, user)

    assert fake.calls == []
    assert Order.objects.filter(pk=order.pk).exists()
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_ACCEPTED
    assert "取引先との接続" in inbox.reply_response
    assert inbox.reply_sent_at is None


@pytest.mark.django_db
def test_send_reply_refuses_received_row(monkeypatch, user, order_secret, customer):
    services.register_connection(customer, "", ORDER_ENV, "receive", user)
    services.register_connection(customer, BUYER_URL, ORDER_ENV, "send", user)
    msg_id = str(uuid.uuid4())
    raw = json.dumps(_payload(msg_id, [_line(1, "A-100")]), ensure_ascii=False).encode("utf-8")
    inbox = services.receive(raw, signing.headers_for(order_secret, msg_id, raw))
    assert isinstance(inbox, Inbox) and inbox.status == Inbox.STATUS_RECEIVED
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    with pytest.raises(ValueError, match="注文回答を送れません"):
        services.send_reply(inbox, user)

    assert fake.calls == []
    inbox.refresh_from_db()
    assert inbox.reply_message_id is None and inbox.reply_response == ""
    assert inbox.reply_state() == ""


@pytest.mark.django_db
def test_reply_secret_not_in_rows_or_logs(
    monkeypatch, caplog, user, order_secret, reply_secret, subcontractor, customer, item, own_item, connections
):
    caplog.set_level(logging.DEBUG, logger="seizou.connect")
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    _send_order(subcontractor, item, user)
    _send_order(subcontractor, item, user)
    first, second = Inbox.objects.order_by("pk")
    services.accept(first, {"T-100": own_item}, user)
    monkeypatch.setattr("requests.post", FakePost(raises=requests.Timeout("作り物の待ち切れ")))
    services.reject(second, "作り物の断りの理由", user)

    assert "注文回答" in caplog.text
    for inbox in Inbox.objects.all():
        for value in (order_secret, reply_secret):
            assert value not in inbox.reply_response
            assert value[len("whsec_"):] not in inbox.reply_response
    for value in (order_secret, reply_secret):
        assert value not in caplog.text
        assert value[len("whsec_"):] not in caplog.text
