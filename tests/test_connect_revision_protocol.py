"""会社どうしの JSON と送り合いの表の版の番号とコード (中小企業共通 EDI の注文変更の方式 2。C96 その 2)。

1 つの DB に注文を送る会社 (A = 協力企業 S001 へ送る) と受ける会社 (B = 得意先 C001 から受ける) の両方の役を置く
(tests/test_connect_reply.py の形。道具はそこから借り、fixture は同じ字で書く)。相手への HTTP は requests.post を偽の物に差し替える。
発注の版とコードは、送る側の入口 (変更と取消の画面) ができるまで表に直に書いて見る。取引先・品目・会社は作り物。
"""

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest

from seizou.connect import services, signing
from seizou.connect.models import Acceptance, Inbox, Outbox, ShippingNotice
from seizou.core import edi_codes
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrder, PurchaseOrderLine
from django.contrib.auth import get_user_model

from seizou.masters import services as masters_services
from tests.test_connect_reply import ORDER_ENV, REPLY_ENV, _connect, _into_receive
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret
from tests.test_r10_inbox_accept import _line, _payload


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
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の発注元", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def own_item(user):
    return masters_services.create_item({"part_no": "B-200", "name": "作り物の受ける側の部品", "unit": "個"}, user)


@pytest.fixture
def connections(user, subcontractor, customer, order_secret, reply_secret):
    _connect(user, subcontractor, customer)


def _send_order(subcontractor, item, user, *, lines=1):
    return purchasing_services.create_purchase_order(
        subcontractor,
        [
            {"item": item, "qty": Decimal("10"), "unit_price": Decimal("25.5"), "due_date": date(2026, 11, 20)}
            for _ in range(lines)
        ],
        user,
        order_date=date(2026, 11, 2),
    )


def _body(call):
    return json.loads(call["data"].decode("utf-8"))


def _signed_receive(data, secret):
    """相手の受け口の役 (署名して services.receive に渡す)。返す物は receive の返す物。"""
    msg_id = data.get("id") or str(uuid.uuid4())
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return services.receive(raw, signing.headers_for(secret, msg_id, raw))


def _as_revision(po, revision_no, purpose_code):
    PurchaseOrder.objects.filter(pk=po.pk).update(revision_no=revision_no, purpose_code=purpose_code)
    return PurchaseOrder.objects.get(pk=po.pk)


@pytest.mark.django_db
def test_first_revision_body_and_inbox(monkeypatch, user, subcontractor, customer, item, connections):
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    po = _send_order(subcontractor, item, user)

    sent = _body(fake.calls[0])
    assert sent["order_id"] == sent["id"]
    assert sent["order_revision_no"] == 1
    assert sent["purpose_code"] == "9"
    (line,) = sent["lines"]
    assert (line["line_status_code"], line["line_status_reason_code"]) == ("39", "")
    # コードは字 (数でない)
    assert isinstance(sent["purpose_code"], str) and isinstance(line["line_status_code"], str)
    inbox = Inbox.objects.get()
    assert (inbox.revision_no, inbox.purpose_code, inbox.revises) == (1, "9", None)
    outbox = Outbox.objects.get()
    assert (outbox.revision_no, outbox.purpose_code) == (1, "9")
    assert str(outbox).endswith("作り物の協力企業 が受け取った")
    assert po.status == PurchaseOrder.STATUS_SENT


@pytest.mark.django_db
def test_revision_two_gets_new_number_and_keeps_status(monkeypatch, user, subcontractor, customer, item, connections):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_order(subcontractor, item, user, lines=2)
    first = Outbox.objects.get()
    PurchaseOrder.objects.filter(pk=po.pk).update(status=PurchaseOrder.STATUS_ACCEPTED)
    po = _as_revision(po, 2, edi_codes.PURPOSE_CHANGE)
    PurchaseOrderLine.objects.filter(purchase_order=po, line_no=1).update(
        line_status_code=edi_codes.LINE_CHANGED, line_status_reason_code=edi_codes.REASON_QTY
    )
    PurchaseOrderLine.objects.filter(purchase_order=po, line_no=2).update(line_status_code=edi_codes.LINE_DELETED)

    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    outbox = services.send_order(po, user)

    sent = _body(fake.calls[0])
    assert outbox.message_id != first.message_id
    assert sent["id"] == str(outbox.message_id)
    assert sent["order_id"] == str(first.message_id)
    assert (sent["order_revision_no"], sent["purpose_code"]) == (2, "4")
    assert [(row["line_no"], row["line_status_code"], row["line_status_reason_code"]) for row in sent["lines"]] == [
        (1, "3", "3002"),
        (2, "2", ""),
    ]
    assert (outbox.revision_no, outbox.purpose_code) == (2, "4")
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ACCEPTED

    # 同じ版の送り直しは同じ番号
    again = services.send_order(po, user)
    assert again.message_id == outbox.message_id
    assert _body(fake.calls[1])["order_id"] == str(first.message_id)


@pytest.mark.django_db
def test_cancelled_order_is_sent_only_as_cancel_revision(monkeypatch, user, subcontractor, customer, item, connections):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_order(subcontractor, item, user)
    PurchaseOrder.objects.filter(pk=po.pk).update(status=PurchaseOrder.STATUS_CANCELLED)
    po = PurchaseOrder.objects.get(pk=po.pk)
    with pytest.raises(ValueError, match="^送れません。"):
        services.send_order(po, user)

    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    po = _as_revision(po, 2, edi_codes.PURPOSE_CANCEL)
    outbox = services.send_order(po, user)
    assert _body(fake.calls[0])["purpose_code"] == "1"
    assert (outbox.revision_no, outbox.purpose_code) == (2, "1")
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_CANCELLED
    assert str(outbox).startswith("第 2 版の取消 ")


@pytest.mark.django_db
def test_acceptance_body_carries_buyer_revision(monkeypatch, user, subcontractor, customer, item, own_item, connections):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_order(subcontractor, item, user)
    first = Outbox.objects.get()
    services.accept(Inbox.objects.get(), {"T-100": own_item}, user)
    po = _as_revision(po, 2, edi_codes.PURPOSE_CHANGE)
    Outbox.objects.create(
        message_id=uuid.uuid4(), connection=first.connection, purchase_order=po, response="受け取った", revision_no=2,
        purpose_code="4",
    )

    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    purchasing_services.receive(po.lines.get(), Decimal("3"), date(2026, 11, 5), user)

    sent = _body(fake.calls[0])
    assert sent["kind"] == "acceptance"
    assert sent["order_id"] == str(first.message_id)
    assert sent["order_revision_no"] == 2
    # 受ける側は前の版への検収も止めずに受け、版の番号を残す
    assert Acceptance.objects.get().order_revision_no == 2


@pytest.mark.django_db
def test_current_revision_no(user, customer):
    base = services.record_inbox(customer, _payload(str(uuid.uuid4()), [_line(1, "T-100")]), user)
    assert base.current_revision_no() == 1
    pending = Inbox.objects.create(
        message_id=uuid.uuid4(), partner=customer, kind=Inbox.KIND_ORDER, payload={}, revision_no=2,
        purpose_code="4", revises=base,
    )
    assert base.current_revision_no() == 1
    Inbox.objects.filter(pk=pending.pk).update(status=Inbox.STATUS_ACCEPTED)
    assert base.current_revision_no() == 2
    assert Inbox.objects.get(pk=pending.pk).current_revision_no() == 2


def _notice_payload(outbox, **extra):
    data = {
        "id": str(uuid.uuid4()),
        "kind": "shipping_notice",
        "sent_at": "2026-11-05T10:00:00+09:00",
        "order_id": str(outbox.message_id),
        "order_no": outbox.purchase_order.po_no,
        "line_no": 1,
        "supplier_order_no": "SO-0001",
        "shipment_no": f"SH-{uuid.uuid4().hex[:6]}",
        "shipped_at": "2026-11-05",
        "qty": "2",
        "supplier": {"id": "b-company", "name": "作り物の受ける会社"},
    }
    data.update(extra)
    return data


@pytest.mark.django_db
def test_shipping_notice_revision_shape(monkeypatch, user, subcontractor, customer, item, connections, reply_secret):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    _send_order(subcontractor, item, user)
    outbox = Outbox.objects.get()

    assert not isinstance(_signed_receive(_notice_payload(outbox), reply_secret), str)
    assert ShippingNotice.objects.get().order_revision_no == 1
    assert not isinstance(_signed_receive(_notice_payload(outbox, order_revision_no=2), reply_secret), str)
    assert sorted(ShippingNotice.objects.values_list("order_revision_no", flat=True)) == [1, 2]
    for bad in (0, "2", True):
        refused = _signed_receive(_notice_payload(outbox, order_revision_no=bad), reply_secret)
        assert refused == services.MESSAGE_SHIPPING_SHAPE
    assert ShippingNotice.objects.count() == 2


@pytest.mark.django_db
def test_acceptance_revision_shape(monkeypatch, user, subcontractor, customer, item, own_item, connections, order_secret):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_order(subcontractor, item, user)
    inbox = Inbox.objects.get()
    services.accept(inbox, {"T-100": own_item}, user)

    def payload(**extra):
        data = {
            "id": str(uuid.uuid4()),
            "kind": "acceptance",
            "sent_at": "2026-11-05T10:00:00+09:00",
            "order_id": str(inbox.message_id),
            "order_no": po.po_no,
            "line_no": 1,
            "accepted_on": "2026-11-05",
            "qty": "2",
            "buyer": {"id": "a-company", "name": "作り物の発注する会社"},
        }
        data.update(extra)
        return data

    assert not isinstance(_signed_receive(payload(), order_secret), str)
    assert Acceptance.objects.get().order_revision_no == 1
    for bad in (0, "2", True):
        assert _signed_receive(payload(order_revision_no=bad), order_secret) == services.MESSAGE_ACCEPTANCE_SHAPE
    assert Acceptance.objects.count() == 1


def _reply_payload(outbox, result, reason=""):
    return {
        "id": str(uuid.uuid4()),
        "kind": "order_reply",
        "sent_at": "2026-11-03T10:00:00+09:00",
        "order_id": str(outbox.message_id),
        "order_no": outbox.purchase_order.po_no,
        "order_revision_no": outbox.revision_no,
        "result": result,
        "supplier_order_no": "SO-0001",
        "reason": reason,
        "supplier": {"id": "b-company", "name": "作り物の受ける会社"},
    }


@pytest.mark.django_db
def test_reply_to_revision_is_written_on_outbox_row(monkeypatch, user, subcontractor, customer, item, connections, reply_secret):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_order(subcontractor, item, user)
    PurchaseOrder.objects.filter(pk=po.pk).update(status=PurchaseOrder.STATUS_ACCEPTED)
    po = _as_revision(po, 2, edi_codes.PURPOSE_CHANGE)
    monkeypatch.setattr("requests.post", FakePost())
    services.send_order(po, user)
    services.send_order(po, user)  # 同じ版の送り直し (行は 2 つ、番号は同じ)
    rows = list(Outbox.objects.filter(revision_no=2).order_by("pk"))
    assert len(rows) == 2

    data = _reply_payload(rows[0], "accepted")
    receipt = _signed_receive(data, reply_secret)
    assert receipt.message_id == uuid.UUID(data["id"])
    head = Outbox.objects.get(pk=rows[0].pk)
    assert head.reply_type == "AP"
    assert head.replied_at is not None
    assert head.reply_message_id == uuid.UUID(data["id"])
    assert Outbox.objects.get(pk=rows[1].pk).reply_type == ""
    assert "・相手が受け入れた" in str(head) and str(head).startswith("第 2 版の変更 ")
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ACCEPTED
    assert po.reply_message_id is None

    # 同じ番号の 2 回目は何もしない
    replied_at = head.replied_at
    _signed_receive(data, reply_secret)
    assert Outbox.objects.get(pk=rows[0].pk).replied_at == replied_at

    # 別の番号の答えは上書きする
    _signed_receive(_reply_payload(rows[0], "rejected", reason="作り物の断りの理由"), reply_secret)
    head = Outbox.objects.get(pk=rows[0].pk)
    assert (head.reply_type, head.reply_note) == ("RE", "作り物の断りの理由")
    assert "・相手が断った" in str(head)
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ACCEPTED


@pytest.mark.django_db
def test_reply_revision_number_shape(monkeypatch, user, subcontractor, customer, item, connections, reply_secret):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    _send_order(subcontractor, item, user)
    data = _reply_payload(Outbox.objects.get(), "accepted")
    data["order_revision_no"] = 0
    assert _signed_receive(data, reply_secret) == services.MESSAGE_REPLY_SHAPE


@pytest.mark.parametrize(
    "extra, line_extra, message",
    [
        ({"purpose_code": "4", "order_revision_no": 2, "order_id": str(uuid.UUID(int=1))}, {}, "まだ受けられません"),
        ({"purpose_code": "1", "order_revision_no": 2, "order_id": str(uuid.UUID(int=1))}, {}, "まだ受けられません"),
        ({"purpose_code": "X"}, {}, "注文書目的コードか明細状態コードが読めません。"),
        ({"purpose_code": 9}, {}, "注文書目的コードか明細状態コードが読めません。"),
        ({}, {"line_status_code": "7"}, "注文書目的コードか明細状態コードが読めません。"),
        ({}, {"line_status_code": ["39"]}, "注文書目的コードか明細状態コードが読めません。"),
        ({}, {"line_status_reason_code": "3004"}, "注文書目的コードか明細状態コードが読めません。"),
        ({"purpose_code": "9", "order_revision_no": 2}, {}, "注文履歴番号が読めません。"),
        ({"order_revision_no": True}, {}, "注文履歴番号が読めません。"),
    ],
)
@pytest.mark.django_db
def test_order_with_unreadable_codes_or_revision_is_refused(user, customer, connections, order_secret, extra, line_extra, message):
    msg_id = str(uuid.uuid4())
    data = _payload(msg_id, [{**_line(1, "T-100"), **line_extra}])
    data.update(extra)
    result = _signed_receive(data, order_secret)
    assert isinstance(result, str) and message in result
    assert not Inbox.objects.exists()


@pytest.mark.django_db
def test_order_without_codes_is_first_revision(user, customer, connections, order_secret):
    result = _signed_receive(_payload(str(uuid.uuid4()), [_line(1, "T-100")]), order_secret)
    assert isinstance(result, Inbox)
    assert (result.revision_no, result.purpose_code) == (1, "9")
