"""会社どうしのつながりの送る口と受け口を固めるテスト (エンタープライズ化の調べ 第一弾の候補 11)。

1 つの DB に、注文を送る会社と受ける会社の両方の役を置く (tests/test_connect_reply.py の _connect の形)。
- 1 送る口: 3xx の転送を追わず、200 の返事は受け取った印 (status と送った番号) が合うときだけ受け取ったにする。
- 2 鍵の長さ: ほどいた鍵が 24〜64 bytes の外なら使わない (送らない・受け口はそのつながりを飛ばす)。
相手の受け口への HTTP は requests.post を偽の物に差し替えて見る (外へは出ない)。
鍵は試験のたびに signing.new_secret() で作って環境変数に置き、ファイルに書かない。取引先・部品・会社は作り物。
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid

import pytest

from seizou.connect import services, signing
from seizou.connect.models import Inbox, Outbox
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrder, SelfInvoice
from tests.test_connect_reply import ORDER_ENV
from tests.test_connect_self_invoice import (  # noqa: F401 (fixture)
    ISSUED,
    _accepted,
    connections,
    customer,
    item,
    order_secret,
    own_item,
    reply_secret,
    subcontractor,
    user,
)
from tests.test_connect_send import _create
from tests.test_r12_signature import _order_body

#: 試験だけの鍵の置き場の名前 (値は試験の中で作って環境変数に置く)
SHORT_ENV = "SEIZOU_PARTNER_HARDSHORT_SECRET"
#: 3xx の返事の欄の頭の字
REDIRECT_HEAD = "届きません: 相手の受け口の URL が変わっています (HTTP "
#: 200 でも受け取った印が合わない返事の欄の字
MARK_MISMATCH = "返事が読めません (HTTP 200。受け取った印が合いません)"


class Reply:
    """偽の requests の返事。本文は bytes で持ち、json と iter_content (少しずつ) と close で読む。"""

    def __init__(self, status_code, body=b""):
        self.status_code = status_code
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.closed = False

    def json(self):
        return json.loads(self.body)

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start : start + chunk_size]

    def close(self):
        self.closed = True


class RecordingPost:
    """requests.post の代わり。名前の引数を全部控える。answer は見出しを受けて返事を返す関数 (送った番号を返すため)。"""

    def __init__(self, answer=None):
        self.answer = answer or (lambda headers: Reply(200, {"status": "received"}))
        self.calls = []

    def __call__(self, url, data=None, headers=None, **kwargs):
        self.calls.append({"url": url, "data": data, "headers": headers, **kwargs})
        return self.answer(headers)


def _received_with(id_text=None):
    """送った番号 (見出しの webhook-id) を返す受け取った印。id_text があれば、その字を id に返す。"""

    def answer(headers):
        return Reply(200, {"status": "received", "id": id_text or headers["webhook-id"]})

    return answer


# ---------------------------------------------------------------- 1 送る口の転送と返事の印


@pytest.mark.django_db
def test_post_does_not_follow_redirects(monkeypatch, user, subcontractor, item, connections):  # noqa: F811
    """(a) 偽の requests.post に allow_redirects=False が渡る。送った番号を返す 200 は受け取った。"""
    fake = RecordingPost(_received_with())
    monkeypatch.setattr("requests.post", fake)

    po = _create(subcontractor, item, user)

    assert len(fake.calls) == 1
    assert fake.calls[0]["allow_redirects"] is False
    assert po.status == PurchaseOrder.STATUS_SENT
    assert Outbox.objects.get().response == "受け取った"


@pytest.mark.django_db
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_is_not_received(monkeypatch, user, subcontractor, item, connections, status):  # noqa: F811
    """(b) 3xx の返事では発注が「作成」のまま (送った印にならない) で、返事の欄に 3xx の字。"""
    monkeypatch.setattr("requests.post", RecordingPost(lambda headers: Reply(status)))

    po = _create(subcontractor, item, user)

    assert po.status == PurchaseOrder.STATUS_DRAFT
    response = Outbox.objects.get().response
    assert response == f"{REDIRECT_HEAD}{status})。つながりの URL を直してください。"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "answer",
    [
        _received_with("00000000-0000-4000-8000-000000000000"),
        lambda headers: Reply(200, {"status": "rejected", "id": headers["webhook-id"]}),
        lambda headers: Reply(200, {"id": headers["webhook-id"]}),
        lambda headers: Reply(200, b"<html>login</html>"),
        lambda headers: Reply(200, b""),
        lambda headers: Reply(200, [{"status": "received"}]),
    ],
    ids=["other-id", "other-status", "no-status", "html", "empty", "list"],
)
def test_200_without_received_mark_is_unreadable(monkeypatch, user, subcontractor, item, connections, answer):  # noqa: F811
    """(c) 200 でも本文の id が送った番号と違う・status が違う・JSON でないときは「受け取った」にしない。"""
    monkeypatch.setattr("requests.post", RecordingPost(answer))

    po = _create(subcontractor, item, user)

    assert po.status == PurchaseOrder.STATUS_DRAFT
    assert Outbox.objects.get().response == MARK_MISMATCH


@pytest.mark.django_db
def test_200_without_id_is_still_received(monkeypatch, user, subcontractor, item, connections):  # noqa: F811
    """(d) id の無い {"status": "received"} は今のまま受け取った (既にある試験の偽物の既定の返事の形)。"""
    monkeypatch.setattr("requests.post", RecordingPost())

    po = _create(subcontractor, item, user)

    assert po.status == PurchaseOrder.STATUS_SENT
    assert Outbox.objects.get().response == "受け取った"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "answer",
    [lambda headers: Reply(302), _received_with("00000000-0000-4000-8000-000000000000")],
    ids=["redirect", "other-id"],
)
def test_self_invoice_is_not_sent_on_redirect_or_other_id(
    monkeypatch, user, subcontractor, customer, item, own_item, connections, answer  # noqa: F811
):
    """(e) 仕入明細 (C111) も、3xx と id の違う 200 では送った印にならない。"""
    _fake, _po, _order, receipts = _accepted(monkeypatch, subcontractor, item, own_item, user)
    monkeypatch.setattr("requests.post", RecordingPost(answer))

    si = purchasing_services.create_self_invoice(subcontractor, receipts, user, issued_on=ISSUED)

    assert si.status == SelfInvoice.STATUS_DRAFT and si.sent_at is None
    assert si.saved_level == "warning"


# ---------------------------------------------------------------- 2 鍵の長さ


def _secret_of(length):
    """ほどくと length bytes になる鍵の字 (試験の中で乱数から作る)。"""
    return "whsec_" + base64.b64encode(secrets.token_bytes(length)).decode()


@pytest.mark.parametrize("length", [0, 16, 23, 65])
def test_secret_outside_24_to_64_bytes_is_refused(length):
    """whsec_ (0 bytes)・16 bytes・65 bytes の鍵は ValueError。例外の文に鍵の値を入れない。"""
    value = _secret_of(length)
    with pytest.raises(ValueError) as caught:
        signing._decode_secret(value)
    assert "24〜64 bytes" in str(caught.value)
    if length:
        assert value[len("whsec_") :] not in str(caught.value)
    with pytest.raises(ValueError):
        signing.headers_for(value, str(uuid.uuid4()), b"{}")


@pytest.mark.parametrize("length", [24, 32, 64])
def test_secret_of_24_to_64_bytes_is_used(length):
    value = _secret_of(length)
    assert len(signing._decode_secret(value)) == length
    msg_id = str(uuid.uuid4())
    assert signing.verify(value, signing.headers_for(value, msg_id, b"{}"), b"{}") == msg_id


def test_new_secret_is_inside_the_range():
    assert len(signing._decode_secret(signing.new_secret())) == 32


@pytest.mark.django_db
def test_short_secret_is_not_sent(monkeypatch, user, subcontractor, item, connections):  # noqa: F811
    """16 bytes の鍵の送る向きのつながりでは送らず (偽物の呼び出し 0)、返事の欄は今の鍵の形の字。"""
    monkeypatch.setenv(ORDER_ENV, _secret_of(16))
    fake = RecordingPost(_received_with())
    monkeypatch.setattr("requests.post", fake)

    po = _create(subcontractor, item, user)

    assert fake.calls == []
    assert po.status == PurchaseOrder.STATUS_DRAFT
    assert Outbox.objects.get().response == (
        f"鍵の置き場 {ORDER_ENV} の値が鍵の形 (接頭辞つきの base64) でないので送っていません。.env の値を確かめてください。"
    )


def _short_signed_headers(value, msg_id, body):
    """長さの外の鍵で、送る側と同じ形の署名の見出しを作る (signing.headers_for は短い鍵を断るので、ここで HMAC を作る)。"""
    timestamp = int(time.time())
    key = base64.b64decode(value[len("whsec_") :])
    digest = hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return {
        signing.HEADER_ID: msg_id,
        signing.HEADER_TIMESTAMP: str(timestamp),
        signing.HEADER_SIGNATURE: "v1," + base64.b64encode(digest).decode(),
    }


@pytest.mark.django_db
def test_short_secret_receive_connection_is_skipped(monkeypatch, user, customer, caplog):  # noqa: F811
    """受ける向きの鍵が 16 bytes なら、そのつながりを飛ばし (名前だけをログに出す)、署名の合わない注文として断る。"""
    caplog.set_level(logging.WARNING, logger="seizou.connect")
    value = _secret_of(16)
    monkeypatch.setenv(SHORT_ENV, value)
    services.register_connection(customer, "", SHORT_ENV, "receive", user)
    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)

    result = services.receive(body, _short_signed_headers(value, msg_id, body))

    assert result == services.MESSAGE_SIGNATURE_MISMATCH
    assert Inbox.objects.count() == 0
    assert any(SHORT_ENV in record.getMessage() and "鍵の形が読めない" in record.getMessage() for record in caplog.records)
    assert value[len("whsec_") :] not in caplog.text

