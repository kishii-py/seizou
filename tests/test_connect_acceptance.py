"""検収 (connect.services.send_acceptance と、受け口が受注の明細に付ける所) のテスト。EDI の 4 段目。

発注した側が受入を入れると、seizou どうしで送った発注なら、その受入を「検収」にして相手の受け口へ署名して送り、
相手の受け口は元の受信した注文から作った受注の明細に 1 回だけ付ける。届かなくても受入は成立する。

1 つの DB に、注文を送る会社と受ける会社の両方の役を置く (test_connect_reply.py の形)。
検収は注文と同じ向き (協力企業 S001 への「送る」→ 得意先 C001 からの「受ける」) を通る。
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

from seizou.connect import services, signing
from seizou.connect.models import Acceptance, Inbox, Outbox
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import Receipt
from seizou.stock.models import KIND_RECEIVED, StockMove
from tests.test_connect_reply import (
    BUYER_URL,
    ORDER_ENV,
    REPLY_ENV,
    SUPPLIER_URL,
    _connect,
    _into_receive,
    _post,
    _reply_call_headers,
    _send_order,
    _signed_post,
)
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ACCEPTANCE_KEYS = {
    "id",
    "kind",
    "sent_at",
    "order_id",
    "order_no",
    "order_revision_no",
    "line_no",
    "accepted_on",
    "qty",
    "buyer",
}
DAY = date(2026, 11, 5)


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


@pytest.fixture
def connections(user, subcontractor, customer, order_secret, reply_secret):
    _connect(user, subcontractor, customer)


def _accepted(monkeypatch, subcontractor, item, own_item, user):
    """注文を送り、相手の役が承認して受注にするまで (注文回答も偽の post の中で往復する)。"""
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    po = _send_order(subcontractor, item, user)
    inbox = Inbox.objects.get()
    order = services.accept(inbox, {"T-100": own_item}, user)
    inbox.refresh_from_db()
    return fake, po, inbox, order


def _acceptance_raw(po, **changes):
    """手で作る検収の本文 (送る側と同じ bytes の作り方)。返す物は (番号, 本文の bytes)。"""
    msg_id = str(uuid.uuid4())
    data = {
        "id": msg_id,
        "kind": "acceptance",
        "sent_at": "2026-11-05T10:00:00+09:00",
        "order_id": str(Outbox.objects.filter(purchase_order=po).order_by("pk").first().message_id),
        "order_no": po.po_no,
        "line_no": 1,
        "accepted_on": "2026-11-05",
        "qty": "4.00",
        "buyer": {"id": "a-company", "name": "作り物の発注する会社"},
    }
    data.update(changes)
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return msg_id, raw


@pytest.mark.django_db
def test_receipt_sends_acceptance_and_marks_order_line(
    monkeypatch, user, order_secret, subcontractor, customer, item, own_item, connections
):
    fake, po, inbox, order = _accepted(monkeypatch, subcontractor, item, own_item, user)

    receipt = purchasing_services.receive(po.lines.get(), Decimal("4"), DAY, user)

    assert len(fake.calls) == 3
    call = fake.calls[2]
    assert call["url"] == SUPPLIER_URL
    assert call["timeout"] == 10
    assert call["headers"]["Content-Type"] == "application/json"
    assert receipt.acceptance_message_id is not None
    # 見出しの 3 つで署名が通り、番号は受入の検収の番号と同じ
    assert signing.verify(order_secret, call["headers"], call["data"]) == str(receipt.acceptance_message_id)

    sent = json.loads(call["data"].decode("utf-8"))
    assert set(sent) == ACCEPTANCE_KEYS
    assert sent["id"] == str(receipt.acceptance_message_id)
    assert sent["kind"] == "acceptance"
    assert sent["order_id"] == str(Outbox.objects.filter(purchase_order=po).order_by("pk").first().message_id)
    assert sent["order_id"] == str(inbox.message_id)
    assert sent["order_no"] == po.po_no
    assert sent["line_no"] == 1
    assert sent["accepted_on"] == "2026-11-05"
    assert sent["qty"] == "4.00"
    assert set(sent["buyer"]) == {"id", "name"}
    assert "+09:00" in sent["sent_at"]
    # 本文は鍵の並びを固定した JSON の bytes そのまま
    assert call["data"] == json.dumps(
        sent, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    acceptance = Acceptance.objects.get()
    assert acceptance.order_line == order.lines.get(line_no=1)
    assert acceptance.inbox == inbox
    assert acceptance.qty == Decimal("4")
    assert acceptance.accepted_on == DAY
    assert acceptance.buyer_order_no == po.po_no
    assert acceptance.buyer_line_no == 1
    assert acceptance.message_id == receipt.acceptance_message_id

    assert isinstance(receipt, Receipt)
    assert receipt.acceptance_sent_at is not None
    assert receipt.acceptance_response == "受け取った"
    assert receipt.acceptance_state().startswith("送った ")
    # 検収は受信した注文の行にしない。受入と在庫の動きは今までどおり 1 行ずつ。
    assert Inbox.objects.count() == 1
    assert Receipt.objects.count() == 1
    assert StockMove.objects.filter(kind=KIND_RECEIVED).count() == 1


@pytest.mark.django_db
def test_two_receipts_make_two_acceptances(
    monkeypatch, user, subcontractor, customer, item, own_item, connections
):
    _fake, po, _inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    line = po.lines.get()

    # 同じ日に同じ数の分納が 2 回あっても、受入日 × 数では 1 件にしない
    first = purchasing_services.receive(line, Decimal("3"), DAY, user)
    second = purchasing_services.receive(line, Decimal("3"), DAY, user)

    assert first.acceptance_message_id != second.acceptance_message_id
    assert Acceptance.objects.count() == 2
    assert set(Acceptance.objects.values_list("message_id", flat=True)) == {
        first.acceptance_message_id,
        second.acceptance_message_id,
    }


@pytest.mark.django_db
def test_same_acceptance_twice_makes_one_row(
    monkeypatch, client, user, subcontractor, customer, item, own_item, connections
):
    _fake, po, inbox, order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    # 検収は相手に渡さずに控える (受け口へは test client で 2 回送る)
    held = FakePost()
    monkeypatch.setattr("requests.post", held)
    purchasing_services.receive(po.lines.get(), Decimal("4"), DAY, user)
    (call,) = held.calls
    headers = _reply_call_headers(call)

    first = _post(client, call["data"], headers)
    second = _post(client, call["data"], headers)

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"] == headers[signing.HEADER_ID]
    acceptance = Acceptance.objects.get()
    assert str(acceptance.message_id) == headers[signing.HEADER_ID]
    # 同じ番号は DB が止める
    with pytest.raises(IntegrityError), transaction.atomic():
        Acceptance.objects.create(
            message_id=acceptance.message_id,
            inbox=inbox,
            order_line=order.lines.get(),
            buyer_line_no=1,
            accepted_on=DAY,
            qty=Decimal("1"),
        )


@pytest.mark.django_db
def test_acceptance_without_valid_signature_is_refused(
    monkeypatch, client, user, subcontractor, customer, item, own_item, connections
):
    _fake, po, _inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    msg_id, raw = _acceptance_raw(po)

    missing = _post(client, raw, {})
    wrong = _signed_post(client, raw, msg_id, _new_secret())

    assert missing.status_code == 400
    assert missing.json() == {"status": "rejected", "message": services.MESSAGE_MISSING_HEADER}
    assert wrong.status_code == 400
    assert wrong.json() == {"status": "rejected", "message": services.MESSAGE_SIGNATURE_MISMATCH}
    assert Acceptance.objects.count() == 0


@pytest.mark.django_db
def test_acceptance_for_another_partners_order_is_refused(
    monkeypatch, client, user, reply_secret, subcontractor, customer, item, own_item, connections
):
    _fake, po, _inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    msg_id, raw = _acceptance_raw(po)

    # 注文回答の向きの鍵 = 協力企業 S001 の向き「受ける」のつながりで通るが、受信した注文の取引先は C001。
    response = _signed_post(client, raw, msg_id, reply_secret)

    assert response.status_code == 400
    assert "その取引先からの物ではありません" in response.json()["message"]
    assert Acceptance.objects.count() == 0


@pytest.mark.django_db
def test_acceptance_with_unknown_order_or_line_is_refused(
    monkeypatch, client, user, order_secret, subcontractor, customer, item, own_item, connections
):
    _fake, po, _inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)

    unknown_id, unknown = _acceptance_raw(po, order_id=str(uuid.uuid4()))
    broken_id, broken = _acceptance_raw(po, order_id="uuid-でない字")
    line_id, line = _acceptance_raw(po, line_no=99)

    responses = {
        "unknown": _signed_post(client, unknown, unknown_id, order_secret),
        "broken": _signed_post(client, broken, broken_id, order_secret),
        "line": _signed_post(client, line, line_id, order_secret),
    }

    assert {name: r.status_code for name, r in responses.items()} == dict.fromkeys(responses, 400)
    assert responses["unknown"].json()["message"] == services.MESSAGE_ACCEPTANCE_UNKNOWN_ORDER
    assert responses["broken"].json()["message"] == services.MESSAGE_ACCEPTANCE_UNKNOWN_ORDER
    assert "行番号 99" in responses["line"].json()["message"]
    assert Acceptance.objects.count() == 0


@pytest.mark.django_db
def test_acceptance_for_unaccepted_order_is_refused(
    monkeypatch, user, subcontractor, customer, item, connections
):
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    waiting_po = _send_order(subcontractor, item, user)
    rejected_po = _send_order(subcontractor, item, user)
    _waiting, rejected = Inbox.objects.order_by("pk")
    services.reject(rejected, "作り物の断りの理由", user)

    # 相手が承認する前 (届いた) と、断った後の受信した注文への検収
    receipts = [
        purchasing_services.receive(po.lines.get(), Decimal("4"), DAY, user)
        for po in (waiting_po, rejected_po)
    ]

    assert Acceptance.objects.count() == 0
    assert Receipt.objects.count() == 2
    for receipt in receipts:
        assert receipt.acceptance_sent_at is None
        assert receipt.acceptance_response.startswith("断られた: 検収の元の注文は受け入れていない")
        assert receipt.acceptance_response == f"断られた: {services.MESSAGE_ACCEPTANCE_NOT_ACCEPTED}"
        assert receipt.acceptance_state().startswith("断られた: ")


@pytest.mark.parametrize(
    ("changes", "message_name"),
    [
        ({"qty": "abc"}, "MESSAGE_ACCEPTANCE_QTY"),
        ({"qty": "0"}, "MESSAGE_ACCEPTANCE_QTY"),
        ({"qty": "-1"}, "MESSAGE_ACCEPTANCE_QTY"),
        ({"qty": 1.5}, "MESSAGE_ACCEPTANCE_SHAPE"),
        ({"accepted_on": "2026/11/05"}, "MESSAGE_ACCEPTANCE_DATE"),
        ({"line_no": "1"}, "MESSAGE_ACCEPTANCE_SHAPE"),
        ({"line_no": True}, "MESSAGE_ACCEPTANCE_SHAPE"),
        ({"line_no": 0}, "MESSAGE_ACCEPTANCE_SHAPE"),
        ({"order_id": 1}, "MESSAGE_ACCEPTANCE_SHAPE"),
    ],
)
@pytest.mark.django_db
def test_acceptance_with_bad_shape_qty_or_date_is_refused(
    monkeypatch, client, user, order_secret, subcontractor, customer, item, own_item, connections, changes, message_name
):
    _fake, po, _inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    msg_id, raw = _acceptance_raw(po, **changes)

    response = _signed_post(client, raw, msg_id, order_secret)

    assert response.status_code == 400
    assert response.json()["message"] == getattr(services, message_name)
    assert Acceptance.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_acceptance_is_sent_outside_atomic(
    monkeypatch, user, subcontractor, customer, item, own_item, connections
):
    fake, po, _inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)

    purchasing_services.receive(po.lines.get(), Decimal("4"), DAY, user)

    # 注文・注文回答・検収の 3 回。どれも atomic の外 (R17)。
    assert len(fake.calls) == 3
    assert [call["url"] for call in fake.calls] == [SUPPLIER_URL, BUYER_URL, SUPPLIER_URL]
    assert [call["in_atomic_block"] for call in fake.calls] == [False] * 3
    assert Acceptance.objects.count() == 1


@pytest.mark.django_db
def test_receipt_stands_when_acceptance_is_unreachable_and_resend_reuses_number(
    monkeypatch, user, subcontractor, customer, item, own_item, connections
):
    _fake, po, _inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))

    receipt = purchasing_services.receive(po.lines.get(), Decimal("4"), DAY, user)

    assert Receipt.objects.count() == 1
    assert StockMove.objects.filter(kind=KIND_RECEIVED).count() == 1
    assert receipt.acceptance_sent_at is None
    assert receipt.acceptance_response.startswith("届きません")
    assert receipt.acceptance_message_id is not None
    assert receipt.acceptance_state().startswith("届きません")
    assert Acceptance.objects.count() == 0

    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    saved = services.send_acceptance(receipt, user)

    (call,) = fake.calls
    assert json.loads(call["data"].decode("utf-8"))["id"] == str(receipt.acceptance_message_id)
    assert saved.acceptance_message_id == receipt.acceptance_message_id
    assert saved.acceptance_sent_at is not None
    assert saved.acceptance_response == "受け取った"
    acceptance = Acceptance.objects.get()
    assert acceptance.message_id == receipt.acceptance_message_id


@pytest.mark.django_db
def test_receipt_without_send_connection_records_reason(
    monkeypatch, user, reply_secret, subcontractor, customer, item
):
    # S001 への向き「送る」のつながりは作らない。送った注文の行だけを手で置く。
    reply_back = services.register_connection(customer, BUYER_URL, REPLY_ENV, "send", user)
    po = purchasing_services.create_purchase_order(
        subcontractor,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("25.5")}],
        user,
        order_date=date(2026, 11, 2),
    )
    Outbox.objects.create(
        message_id=uuid.uuid4(), connection=reply_back, purchase_order=po, response="受け取った"
    )
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    receipt = purchasing_services.receive(po.lines.get(), Decimal("4"), DAY, user)

    assert fake.calls == []
    assert "取引先との接続" in receipt.acceptance_response
    assert receipt.acceptance_message_id is not None
    assert receipt.acceptance_sent_at is None


@pytest.mark.django_db
def test_receipt_on_order_not_sent_by_edi_sends_nothing(
    monkeypatch, user, subcontractor, customer, item, connections
):
    other = masters_services.create_partner(
        {"code": "S002", "name": "作り物のつながりの無い協力企業", "role": "subcontractor"}, user
    )
    lines = [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("25.5")}]
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    # S001 には向き「送る」のつながりがあるが、送らずに入れた発注 (種の形)。S002 にはつながりが無い。
    not_sent = purchasing_services.create_purchase_order(
        subcontractor, lines, user, order_date=date(2026, 11, 2), send=False
    )
    no_link = purchasing_services.create_purchase_order(other, lines, user, order_date=date(2026, 11, 2))

    receipts = [
        purchasing_services.receive(po.lines.get(), Decimal("4"), DAY, user) for po in (not_sent, no_link)
    ]

    assert fake.calls == []
    for receipt in receipts:
        receipt.refresh_from_db()
        assert receipt.acceptance_message_id is None
        assert receipt.acceptance_sent_at is None
        assert receipt.acceptance_response == ""
        assert receipt.acceptance_state() == ""

    saved = services.send_acceptance(receipts[0], user)

    assert "seizou どうしで送った発注ではない" in saved.acceptance_response
    assert saved.acceptance_message_id is None
    assert fake.calls == []


@pytest.mark.django_db
def test_record_acceptance_refuses_bad_values(
    monkeypatch, user, subcontractor, customer, item, own_item, connections
):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    _send_order(subcontractor, item, user)
    _send_order(subcontractor, item, user)
    first, second = Inbox.objects.order_by("pk")
    services.accept(first, {"T-100": own_item}, user)
    services.accept(second, {"T-100": own_item}, user)
    first.refresh_from_db()
    second.refresh_from_db()
    line = first.order.lines.get()
    other_line = second.order.lines.get()

    def record(order_line, qty, accepted_on, message_id=None):
        return services.record_acceptance(
            first,
            order_line,
            qty,
            accepted_on,
            None,
            message_id=message_id or uuid.uuid4(),
            buyer_order_no="PO-0001",
            buyer_line_no=1,
        )

    with pytest.raises(ValueError, match="検収の数は 0 より大きい数にしてください。"):
        record(line, Decimal("0"), DAY)
    with pytest.raises(ValueError, match="検収日は日付にしてください。"):
        record(line, Decimal("4"), "2026-11-05")
    with pytest.raises(ValueError, match="その受信した注文から作った受注の明細ではありません。"):
        record(other_line, Decimal("4"), DAY)
    assert Acceptance.objects.count() == 0

    number = uuid.uuid4()
    once = record(line, Decimal("4"), DAY, number)
    twice = record(line, Decimal("4"), DAY, number)

    assert twice.pk == once.pk
    assert Acceptance.objects.count() == 1


@pytest.mark.django_db
def test_acceptance_secret_not_in_rows_or_logs(
    monkeypatch, caplog, user, order_secret, reply_secret, subcontractor, customer, item, own_item, connections
):
    caplog.set_level(logging.DEBUG, logger="seizou.connect")
    _fake, po, _inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    line = po.lines.get()
    sent = purchasing_services.receive(line, Decimal("4"), DAY, user)
    monkeypatch.setattr("requests.post", FakePost(raises=requests.Timeout("作り物の待ち切れ")))
    lost = purchasing_services.receive(line, Decimal("2"), DAY, user)

    assert "検収を送った" in caplog.text
    assert sent.acceptance_response == "受け取った"
    assert lost.acceptance_response.startswith("届きません")
    for receipt in Receipt.objects.all():
        for value in (order_secret, reply_secret):
            assert value not in receipt.acceptance_response
            assert value[len("whsec_"):] not in receipt.acceptance_response
    for value in (order_secret, reply_secret):
        assert value not in caplog.text
        assert value[len("whsec_"):] not in caplog.text
