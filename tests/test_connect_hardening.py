"""会社どうしのつながりの送る口と受け口を固めるテスト (エンタープライズ化の調べ 第一弾の候補 11)。

1 つの DB に、注文を送る会社と受ける会社の両方の役を置く (tests/test_connect_reply.py の _connect の形)。
- 1 送る口: 3xx の転送を追わず、200 の返事は受け取った印 (status と送った番号) が合うときだけ受け取ったにする。
- 2 鍵の長さ: ほどいた鍵が 24〜64 bytes の外なら使わない (送らない・受け口はそのつながりを飛ばす)。
- 3 同じ鍵の 2 社: 受ける向きの 2 つ以上の取引先の鍵で署名が合えば断る。
- 4 送る先を外の名前に絞る鍵 (settings の SEIZOU_CONNECT_PUBLIC_ONLY): 真のときだけ https の URL だけを保存し、送る前に名前を引く。
- 5 受けた会話の添付の数: 送る側と同じ上限 (MAX_CHAT_FILES = 1) より多い言葉は断る。
相手の受け口への HTTP は requests.post を偽の物に差し替えて見る (外へは出ない)。
鍵は試験のたびに signing.new_secret() で作って環境変数に置き、ファイルに書かない。取引先・部品・会社は作り物。
"""

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
import uuid

import pytest

from seizou.connect import services, signing
from seizou.connect.models import ChatFile, ChatMessage, Connection, Inbox, Outbox
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrder, SelfInvoice
from tests.test_connect_chat import _chat_raw
from tests.test_connect_reply import ORDER_ENV, _into_receive
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
from tests.test_connect_send import FakePost, _create
from tests.test_r12_signature import _order_body

#: 試験だけの鍵の置き場の名前 (値は試験の中で作って環境変数に置く)
SHORT_ENV = "SEIZOU_PARTNER_HARDSHORT_SECRET"
FIRST_ENV = "SEIZOU_PARTNER_HARDFIRST_SECRET"
SECOND_ENV = "SEIZOU_PARTNER_HARDSECOND_SECRET"
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


# ---------------------------------------------------------------- 3 同じ鍵の 2 社


@pytest.fixture
def two_customers(user):  # noqa: F811
    return [
        masters_services.create_partner({"code": code, "name": name, "role": "customer"}, user)
        for code, name in (("C101", "作り物の得意先 1"), ("C102", "作り物の得意先 2"))
    ]


@pytest.mark.django_db
def test_same_secret_value_for_two_partners_is_refused(monkeypatch, user, two_customers):  # noqa: F811
    """受ける向きの 2 つのつながり (別の取引先・別の置き場の名前) に同じ値の鍵を置くと、署名つきの注文を断る。"""
    value = signing.new_secret()
    monkeypatch.setenv(FIRST_ENV, value)
    monkeypatch.setenv(SECOND_ENV, value)
    services.register_connection(two_customers[0], "", FIRST_ENV, "receive", user)
    services.register_connection(two_customers[1], "", SECOND_ENV, "receive", user)
    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)

    result = services.receive(body, signing.headers_for(value, msg_id, body))

    assert result == services.MESSAGE_SAME_SECRET
    assert Inbox.objects.count() == 0
    assert "作り物の得意先" not in result


@pytest.mark.django_db
def test_same_secret_name_for_two_partners_is_refused_on_receive(monkeypatch, user, two_customers):  # noqa: F811
    """2 社の受ける向きに同じ置き場の名前を使っても (保存の時には止めていない)、受けるときに断る。"""
    value = signing.new_secret()
    monkeypatch.setenv(FIRST_ENV, value)
    for partner in two_customers:
        services.register_connection(partner, "", FIRST_ENV, "receive", user)
    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)

    assert services.receive(body, signing.headers_for(value, msg_id, body)) == services.MESSAGE_SAME_SECRET
    assert Inbox.objects.count() == 0


@pytest.mark.django_db
def test_one_matching_partner_is_received(monkeypatch, user, two_customers):  # noqa: F811
    """鍵が取引先ごとに分かれていれば、今のまま署名の合う 1 社の注文として受ける。"""
    first, second = signing.new_secret(), signing.new_secret()
    monkeypatch.setenv(FIRST_ENV, first)
    monkeypatch.setenv(SECOND_ENV, second)
    services.register_connection(two_customers[0], "", FIRST_ENV, "receive", user)
    services.register_connection(two_customers[1], "", SECOND_ENV, "receive", user)
    msg_id = str(uuid.uuid4())
    body = _order_body(msg_id)

    result = services.receive(body, signing.headers_for(second, msg_id, body))

    assert isinstance(result, Inbox)
    assert result.partner == two_customers[1]


@pytest.mark.django_db
def test_same_secret_name_for_two_send_connections_is_saved(user, two_customers):  # noqa: F811
    """送る向きどうしの同じ置き場の名前は、今のまま保存できる。"""
    for partner in two_customers:
        services.register_connection(partner, "https://buyer.example.test/connect/receive/order/", FIRST_ENV, "send", user)
    assert Connection.objects.filter(secret_env=FIRST_ENV).count() == 2


# ---------------------------------------------------------------- 4 送る先を外の名前に絞る鍵 (SEIZOU_CONNECT_PUBLIC_ONLY)

LOCAL_URL = "http://127.0.0.1:8002/connect/receive/order/"
#: 外の番地 (is_global が真)。文書用の 192.0.2.0/24・198.51.100.0/24・203.0.113.0/24 は is_global が偽なので使わない
PUBLIC_ADDRESS = "93.184.215.14"
#: 名前が引けないときの返事の欄の字 (今の届かない字)
UNREACHABLE = "届きません: 相手につながりません。つながりの URL と、相手が立っているかを確かめてください。"


def _resolved(*addresses):
    """socket.getaddrinfo の代わり。決めた番地を返し、引いた名前と番号を控える。"""
    asked = []

    def lookup(host, port, *args, **kwargs):
        asked.append((host, port))
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, port, 0, 0))
            if ":" in address
            else (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            for address in addresses
        ]

    lookup.asked = asked
    return lookup


def _no_lookup(*args, **kwargs):
    raise AssertionError("送る先を絞らない設定で名前を引いた")


@pytest.mark.django_db
def test_public_only_off_keeps_local_http(monkeypatch, settings, user, subcontractor, item, connections):  # noqa: F811
    """鍵が偽 (既定) のとき、http://127.0.0.1 の URL を保存でき、送るときに名前を引かない (今のまま)。"""
    assert getattr(settings, "SEIZOU_CONNECT_PUBLIC_ONLY", False) is False
    connection = Connection.objects.get(partner=subcontractor, direction=Connection.DIRECTION_SEND)
    services.update_connection(connection, subcontractor, LOCAL_URL, ORDER_ENV, "send", user)
    monkeypatch.setattr("socket.getaddrinfo", _no_lookup)
    fake = RecordingPost(_received_with())
    monkeypatch.setattr("requests.post", fake)

    po = _create(subcontractor, item, user)

    assert [call["url"] for call in fake.calls] == [LOCAL_URL]
    assert po.status == PurchaseOrder.STATUS_SENT


@pytest.mark.django_db
@pytest.mark.parametrize(
    "addresses",
    [("10.0.0.5",), ("169.254.169.254",), ("127.0.0.1",), ("::1",), ("fe80::1%eth0",), ("192.0.2.10",), (PUBLIC_ADDRESS, "10.0.0.5")],
)
def test_public_only_does_not_send_to_private_address(
    monkeypatch, settings, user, subcontractor, item, connections, addresses  # noqa: F811
):
    """鍵が真のとき、名前の番地のどれか 1 つでも外の番地でなければ送らず (偽物の呼び出し 0)、返事の欄に番地の字。"""
    settings.SEIZOU_CONNECT_PUBLIC_ONLY = True
    lookup = _resolved(*addresses)
    monkeypatch.setattr("socket.getaddrinfo", lookup)
    fake = RecordingPost(_received_with())
    monkeypatch.setattr("requests.post", fake)

    po = _create(subcontractor, item, user)

    assert fake.calls == []
    assert lookup.asked == [("supplier.example.test", 443)]
    assert po.status == PurchaseOrder.STATUS_DRAFT
    shown = addresses[-1].split("%")[0]
    assert Outbox.objects.get().response == (
        f"送っていません: 受け口の URL の名前が社内か手元の番地 ({shown}) を指しています。つながりの URL を確かめてください。"
    )


@pytest.mark.django_db
def test_public_only_sends_to_public_address(monkeypatch, settings, user, subcontractor, item, connections):  # noqa: F811
    """鍵が真でも、名前の番地が全部外の番地なら今のまま送る。"""
    assert ipaddress.ip_address(PUBLIC_ADDRESS).is_global
    settings.SEIZOU_CONNECT_PUBLIC_ONLY = True
    monkeypatch.setattr("socket.getaddrinfo", _resolved(PUBLIC_ADDRESS))
    fake = RecordingPost(_received_with())
    monkeypatch.setattr("requests.post", fake)

    po = _create(subcontractor, item, user)

    assert len(fake.calls) == 1 and fake.calls[0]["allow_redirects"] is False
    assert po.status == PurchaseOrder.STATUS_SENT


@pytest.mark.django_db
def test_public_only_does_not_send_when_name_is_not_found(monkeypatch, settings, user, subcontractor, item, connections):  # noqa: F811
    """鍵が真で名前が引けなければ、今の届かない字で送らない。"""
    settings.SEIZOU_CONNECT_PUBLIC_ONLY = True

    def missing(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "作り物の引けない名前")

    monkeypatch.setattr("socket.getaddrinfo", missing)
    fake = RecordingPost(_received_with())
    monkeypatch.setattr("requests.post", fake)

    po = _create(subcontractor, item, user)

    assert fake.calls == []
    assert po.status == PurchaseOrder.STATUS_DRAFT
    assert Outbox.objects.get().response == UNREACHABLE


@pytest.mark.django_db
def test_public_only_saves_https_only(settings, user, two_customers):  # noqa: F811
    """鍵が真のとき、送る向きの http の URL の保存は ValueError (http://127.0.0.1 も)。https と受ける向きの空の URL は通る。"""
    settings.SEIZOU_CONNECT_PUBLIC_ONLY = True
    first, second = two_customers
    for url in ("http://buyer.example.test/connect/receive/order/", LOCAL_URL, "http://localhost:8002/connect/receive/order/"):
        with pytest.raises(ValueError, match="https:// で始まる URL にしてください"):
            services.register_connection(first, url, FIRST_ENV, "send", user)
    saved = services.register_connection(first, "https://buyer.example.test/connect/receive/order/", FIRST_ENV, "send", user)
    assert saved.url == "https://buyer.example.test/connect/receive/order/"
    assert services.register_connection(second, "", SECOND_ENV, "receive", user).url == ""


# ---------------------------------------------------------------- 5 受けた会話の添付の数


def _chat_file_item():
    """受けた会話の言葉の添付の項目 1 つ (作り物の名前と印)。"""
    return {"id": str(uuid.uuid4()), "name": "作り物の図面.pdf", "mime": "application/pdf", "size": 10, "sha256": "c" * 64}


@pytest.mark.django_db
def test_received_chat_with_more_files_than_the_limit_is_refused(
    monkeypatch, user, subcontractor, item, order_secret, connections  # noqa: F811
):
    """添付 2 件以上の会話の言葉は断られ、会話の行と添付の行は増えない。1 件は今のまま通る。"""
    assert services.MAX_CHAT_FILES == 1
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    _create(subcontractor, item, user)
    order_id = str(Inbox.objects.get().message_id)

    for count in (2, 50):
        msg_id, raw = _chat_raw(order_id, body="", attachments=[_chat_file_item() for _ in range(count)])
        assert services.receive(raw, signing.headers_for(order_secret, msg_id, raw)) == services.MESSAGE_CHAT_SHAPE
    assert not ChatMessage.objects.exists() and not ChatFile.objects.exists()

    msg_id, raw = _chat_raw(order_id, body="", attachments=[_chat_file_item()])
    receipt = services.receive(raw, signing.headers_for(order_secret, msg_id, raw))
    assert isinstance(receipt, services.ChatReceipt)
    assert ChatMessage.objects.count() == 1 and ChatFile.objects.count() == 1
