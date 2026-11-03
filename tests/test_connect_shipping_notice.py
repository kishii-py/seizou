"""出荷の知らせ (connect.services.send_shipping_notice と、受け口が発注の明細に付ける所) のテスト。EDI の 3 段目。

注文を受けた側が出荷すると、届いた注文で作った受注の出荷なら、その出荷を「出荷の知らせ」にして相手の受け口へ署名して送り、
相手の受け口は元の送った注文の発注の明細に 1 回だけ付ける。届かなくても出荷は成立する。

1 つの DB に、注文を送る会社と受ける会社の両方の役を置く (test_connect_reply.py の形)。
出荷の知らせは注文回答と同じ向き (得意先 C001 への「送る」→ 協力企業 S001 からの「受ける」) を通る (検収とは逆の向き)。
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
from seizou.connect.models import Inbox, ShippingNotice
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.shipping import services as shipping_services
from seizou.shipping.models import Shipment
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

NOTICE_KEYS = {
    "id",
    "kind",
    "sent_at",
    "order_id",
    "order_no",
    "line_no",
    "supplier_order_no",
    "shipment_no",
    "shipped_at",
    "qty",
    "supplier",
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


def _notice_raw(inbox, shipment_no="SH-9001", **changes):
    """手で作る出荷の知らせの本文 (送る側と同じ bytes の作り方)。返す物は (番号, 本文の bytes)。"""
    msg_id = str(uuid.uuid4())
    data = {
        "id": msg_id,
        "kind": "shipping_notice",
        "sent_at": "2026-11-05T10:00:00+09:00",
        "order_id": str(inbox.message_id),
        "order_no": inbox.payload["order_no"],
        "line_no": 1,
        "supplier_order_no": inbox.order.order_no,
        "shipment_no": shipment_no,
        "shipped_at": "2026-11-05",
        "qty": "4.00",
        "supplier": {"id": "b-company", "name": "作り物の受ける会社"},
    }
    data.update(changes)
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return msg_id, raw


@pytest.mark.django_db
def test_ship_sends_notice_and_marks_purchase_order_line(
    monkeypatch, user, reply_secret, subcontractor, customer, item, own_item, connections
):
    fake, po, inbox, order = _accepted(monkeypatch, subcontractor, item, own_item, user)

    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), DAY, user)

    assert len(fake.calls) == 3
    call = fake.calls[2]
    assert call["url"] == BUYER_URL
    assert call["timeout"] == 10
    assert call["headers"]["Content-Type"] == "application/json"
    assert shipment.notice_message_id is not None
    # 見出しの 3 つで署名が通り、番号は出荷の知らせの番号と同じ
    assert signing.verify(reply_secret, call["headers"], call["data"]) == str(shipment.notice_message_id)

    sent = json.loads(call["data"].decode("utf-8"))
    assert set(sent) == NOTICE_KEYS
    assert sent["id"] == str(shipment.notice_message_id)
    assert sent["kind"] == "shipping_notice"
    assert sent["order_id"] == str(inbox.message_id)
    assert sent["order_no"] == po.po_no
    assert sent["line_no"] == 1
    assert sent["supplier_order_no"] == order.order_no
    assert sent["shipment_no"] == shipment.shipment_no
    assert sent["shipped_at"] == "2026-11-05"
    assert sent["qty"] == "4.00"
    assert set(sent["supplier"]) == {"id", "name"}
    assert "+09:00" in sent["sent_at"]
    # 本文は鍵の並びを固定した JSON の bytes そのまま
    assert call["data"] == json.dumps(
        sent, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    notice = ShippingNotice.objects.get()
    assert notice.line == po.lines.get()
    assert notice.qty == Decimal("4")
    assert notice.shipped_at == DAY
    assert notice.shipment_no == shipment.shipment_no
    assert notice.supplier_order_no == order.order_no
    assert notice.message_id == shipment.notice_message_id

    assert isinstance(shipment, Shipment)
    assert shipment.notice_sent_at is not None
    assert shipment.notice_response == "受け取った"
    assert shipment.notice_state().startswith("送った ")
    # 出荷の知らせは届いた注文の行にしない。受注の状態は今までどおり進む。
    assert Inbox.objects.count() == 1
    order.refresh_from_db()
    assert order.status == Order.STATUS_PARTIAL


@pytest.mark.django_db
def test_two_shipments_make_two_notices(
    monkeypatch, user, subcontractor, customer, item, own_item, connections
):
    _fake, po, _inbox, order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    line = order.lines.get()

    first = shipping_services.ship(line, Decimal("4"), DAY, user)
    second = shipping_services.ship(line, Decimal("6"), date(2026, 11, 7), user)

    assert first.notice_message_id != second.notice_message_id
    assert first.shipment_no != second.shipment_no
    notices = list(ShippingNotice.objects.all())
    assert len(notices) == 2
    assert {notice.line for notice in notices} == {po.lines.get()}
    assert {notice.message_id for notice in notices} == {first.notice_message_id, second.notice_message_id}
    assert [notice.qty for notice in notices] == [Decimal("4"), Decimal("6")]


@pytest.mark.django_db
def test_same_notice_twice_makes_one_row(
    monkeypatch, client, user, reply_secret, subcontractor, customer, item, own_item, connections
):
    _fake, po, inbox, order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    # 出荷の知らせは相手に渡さずに控える (受け口へは test client で 2 回送る)
    held = FakePost()
    monkeypatch.setattr("requests.post", held)
    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), DAY, user)
    (call,) = held.calls
    headers = _reply_call_headers(call)

    first = _post(client, call["data"], headers)
    second = _post(client, call["data"], headers)

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"] == headers[signing.HEADER_ID]
    notice = ShippingNotice.objects.get()
    assert str(notice.message_id) == headers[signing.HEADER_ID]

    # 番号を失って送り直した物 (番号が違い、出荷番号が同じ) も 1 件のまま (自然な鍵)。返す番号は 1 回目の行の物。
    other_id, other = _notice_raw(inbox, shipment_no=shipment.shipment_no)
    third = _signed_post(client, other, other_id, reply_secret)

    assert third.status_code == 200
    assert third.json()["id"] == str(notice.message_id)
    assert ShippingNotice.objects.count() == 1

    # 同じ番号と、同じ明細 × 同じ出荷番号は DB が止める
    with pytest.raises(IntegrityError), transaction.atomic():
        ShippingNotice.objects.create(
            message_id=notice.message_id, line=po.lines.get(), shipment_no="SH-OTHER", shipped_at=DAY, qty=Decimal("1")
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        ShippingNotice.objects.create(
            message_id=uuid.uuid4(), line=po.lines.get(), shipment_no=notice.shipment_no, shipped_at=DAY, qty=Decimal("1")
        )


@pytest.mark.django_db
def test_notice_without_valid_signature_is_refused(
    monkeypatch, client, user, subcontractor, customer, item, own_item, connections
):
    _fake, _po, inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    msg_id, raw = _notice_raw(inbox)

    missing = _post(client, raw, {})
    wrong = _signed_post(client, raw, msg_id, _new_secret())

    assert missing.status_code == 400
    assert missing.json() == {"status": "rejected", "message": services.MESSAGE_MISSING_HEADER}
    assert wrong.status_code == 400
    assert wrong.json() == {"status": "rejected", "message": services.MESSAGE_SIGNATURE_MISMATCH}
    assert ShippingNotice.objects.count() == 0


@pytest.mark.django_db
def test_notice_for_another_partners_order_is_refused(
    monkeypatch, client, user, order_secret, subcontractor, customer, item, own_item, connections
):
    _fake, _po, inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    msg_id, raw = _notice_raw(inbox)

    # 注文の向きの鍵 = 得意先 C001 の向き「受ける」のつながりで通るが、元の発注の取引先は S001。
    response = _signed_post(client, raw, msg_id, order_secret)

    assert response.status_code == 400
    assert "その取引先への物ではありません" in response.json()["message"]
    assert response.json()["message"] == services.MESSAGE_SHIPPING_OTHER_PARTNER
    assert ShippingNotice.objects.count() == 0


@pytest.mark.django_db
def test_notice_with_unknown_order_or_line_is_refused(
    monkeypatch, client, user, reply_secret, subcontractor, customer, item, own_item, connections
):
    _fake, _po, inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)

    unknown_id, unknown = _notice_raw(inbox, order_id=str(uuid.uuid4()))
    broken_id, broken = _notice_raw(inbox, order_id="uuid-でない字")
    line_id, line = _notice_raw(inbox, line_no=99)

    responses = {
        "unknown": _signed_post(client, unknown, unknown_id, reply_secret),
        "broken": _signed_post(client, broken, broken_id, reply_secret),
        "line": _signed_post(client, line, line_id, reply_secret),
    }

    assert {name: r.status_code for name, r in responses.items()} == dict.fromkeys(responses, 400)
    assert responses["unknown"].json()["message"] == services.MESSAGE_SHIPPING_UNKNOWN_ORDER
    assert responses["broken"].json()["message"] == services.MESSAGE_SHIPPING_UNKNOWN_ORDER
    assert "行番号 99" in responses["line"].json()["message"]
    assert ShippingNotice.objects.count() == 0


@pytest.mark.parametrize(
    ("changes", "message_name"),
    [
        ({"qty": "abc"}, "MESSAGE_SHIPPING_QTY"),
        ({"qty": "0"}, "MESSAGE_SHIPPING_QTY"),
        ({"qty": "-1"}, "MESSAGE_SHIPPING_QTY"),
        ({"qty": 1.5}, "MESSAGE_SHIPPING_SHAPE"),
        ({"shipped_at": "2026/11/05"}, "MESSAGE_SHIPPING_DATE"),
        ({"line_no": "1"}, "MESSAGE_SHIPPING_SHAPE"),
        ({"line_no": True}, "MESSAGE_SHIPPING_SHAPE"),
        ({"line_no": 0}, "MESSAGE_SHIPPING_SHAPE"),
        ({"shipment_no": ""}, "MESSAGE_SHIPPING_SHAPE"),
        ({"shipment_no": "S" * 36}, "MESSAGE_SHIPPING_SHAPE"),
        ({"order_id": 1}, "MESSAGE_SHIPPING_SHAPE"),
    ],
)
@pytest.mark.django_db
def test_notice_with_bad_shape_qty_or_date_is_refused(
    monkeypatch, client, user, reply_secret, subcontractor, customer, item, own_item, connections, changes, message_name
):
    _fake, _po, inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    msg_id, raw = _notice_raw(inbox, **changes)

    response = _signed_post(client, raw, msg_id, reply_secret)

    assert response.status_code == 400
    assert response.json()["message"] == getattr(services, message_name)
    assert ShippingNotice.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_notice_is_sent_outside_atomic(
    monkeypatch, user, subcontractor, customer, item, own_item, connections
):
    fake, _po, _inbox, order = _accepted(monkeypatch, subcontractor, item, own_item, user)

    shipping_services.ship(order.lines.get(), Decimal("4"), DAY, user)

    # 注文・注文回答・出荷の知らせの 3 回。どれも atomic の外 (R17)。
    assert len(fake.calls) == 3
    assert [call["url"] for call in fake.calls] == [SUPPLIER_URL, BUYER_URL, BUYER_URL]
    assert [call["in_atomic_block"] for call in fake.calls] == [False] * 3
    assert ShippingNotice.objects.count() == 1


@pytest.mark.django_db
def test_shipment_stands_when_notice_is_unreachable_and_resend_reuses_number(
    monkeypatch, user, subcontractor, customer, item, own_item, connections
):
    _fake, _po, _inbox, order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))

    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), DAY, user)

    assert Shipment.objects.count() == 1
    assert shipment.notice_sent_at is None
    assert shipment.notice_response.startswith("届きません")
    assert shipment.notice_message_id is not None
    assert shipment.notice_state().startswith("届きません")
    assert ShippingNotice.objects.count() == 0

    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    saved = services.send_shipping_notice(shipment, user)

    (call,) = fake.calls
    assert json.loads(call["data"].decode("utf-8"))["id"] == str(shipment.notice_message_id)
    assert saved.notice_message_id == shipment.notice_message_id
    assert saved.notice_sent_at is not None
    assert saved.notice_response == "受け取った"
    notice = ShippingNotice.objects.get()
    assert notice.message_id == shipment.notice_message_id


@pytest.mark.django_db
def test_shipment_without_send_connection_records_reason(
    monkeypatch, user, order_secret, reply_secret, subcontractor, customer, item, own_item
):
    # C001 への向き「送る」(注文回答と出荷の知らせを返す向き) を置かない
    _connect(user, subcontractor, customer, reply_back=False)
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    _send_order(subcontractor, item, user)
    order = services.accept(Inbox.objects.get(), {"T-100": own_item}, user)

    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), DAY, user)

    # 注文の 1 回だけ。注文回答と出荷の知らせは「取引先との接続が無い」で送らない。
    assert len(fake.calls) == 1
    assert "取引先との接続" in shipment.notice_response
    assert shipment.notice_response == services.MESSAGE_SHIPPING_NO_CONNECTION
    assert shipment.notice_message_id is not None
    assert shipment.notice_sent_at is None
    assert ShippingNotice.objects.count() == 0


@pytest.mark.django_db
def test_manual_order_shipment_sends_nothing(monkeypatch, user, customer, own_item, connections):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    # C001 には向き「送る」のつながりがあるが、手入力の受注 (入口は既定の手入力)
    order = orders_services.create_order(
        customer,
        [{"item": own_item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=date(2026, 11, 2),
    )

    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), DAY, user)

    assert fake.calls == []
    shipment.refresh_from_db()
    assert shipment.notice_message_id is None
    assert shipment.notice_sent_at is None
    assert shipment.notice_response == ""
    assert shipment.notice_state() == ""

    saved = services.send_shipping_notice(shipment, user)

    assert "届いた注文で作った受注ではない" in saved.notice_response
    assert saved.notice_response == services.MESSAGE_SHIPPING_NOT_EDI_ORDER
    assert saved.notice_message_id is None
    assert fake.calls == []


@pytest.mark.django_db
def test_record_shipping_notice_refuses_bad_values(
    monkeypatch, user, subcontractor, customer, item, own_item, connections
):
    _fake, po, _inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    line = po.lines.get()

    def record(qty, shipped_at, shipment_no="SH-0001", message_id=None):
        return services.record_shipping_notice(
            line,
            qty,
            shipped_at,
            None,
            message_id=message_id or uuid.uuid4(),
            shipment_no=shipment_no,
            supplier_order_no="SO-0001",
        )

    with pytest.raises(ValueError, match="出荷の知らせの数は 0 より大きい数にしてください。"):
        record(Decimal("0"), DAY)
    with pytest.raises(ValueError, match="出荷日は日付にしてください。"):
        record(Decimal("4"), "2026-11-05")
    with pytest.raises(ValueError, match="相手の出荷番号は 1〜35 字の字にしてください。"):
        record(Decimal("4"), DAY, shipment_no="")
    assert ShippingNotice.objects.count() == 0

    number = uuid.uuid4()
    once = record(Decimal("4"), DAY, message_id=number)
    twice = record(Decimal("4"), DAY, message_id=number)
    # 番号が違っても、同じ明細 × 同じ出荷番号は 1 回目の行
    other = record(Decimal("4"), DAY, message_id=uuid.uuid4())

    assert twice.pk == once.pk
    assert other.pk == once.pk
    assert ShippingNotice.objects.count() == 1


@pytest.mark.django_db
def test_notice_secret_not_in_rows_or_logs(
    monkeypatch, caplog, user, order_secret, reply_secret, subcontractor, customer, item, own_item, connections
):
    caplog.set_level(logging.DEBUG, logger="seizou.connect")
    _fake, _po, _inbox, order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    line = order.lines.get()
    sent = shipping_services.ship(line, Decimal("4"), DAY, user)
    monkeypatch.setattr("requests.post", FakePost(raises=requests.Timeout("作り物の待ち切れ")))
    lost = shipping_services.ship(line, Decimal("2"), DAY, user)

    assert "出荷の知らせを送った" in caplog.text
    assert sent.notice_response == "受け取った"
    assert lost.notice_response.startswith("届きません")
    for shipment in Shipment.objects.all():
        for value in (order_secret, reply_secret):
            assert value not in shipment.notice_response
            assert value[len("whsec_"):] not in shipment.notice_response
    for value in (order_secret, reply_secret):
        assert value not in caplog.text
        assert value[len("whsec_"):] not in caplog.text
