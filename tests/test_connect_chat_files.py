"""会話の添付 (connect.services の post_chat_message の file・受け口の attachments と kind = chat_file・fetch_chat_file と、取りに行く口と開く口) のテスト。

意図: 本人の決め CHAT-3 = a の「取りに行けるのは接続を登録した相手だけ」「取りに行けなかった添付は「まだ受け取っていない」と出し、取り直せる」と、
中小企業共通 EDI 本編 6.4.1 のアクセス制御を、試験で崩れないようにするため。

2 社の役とつながりは tests/test_connect_reply.py と同じ (1 つの DB に両方の役を置く)。相手への HTTP は requests.post を下の偽の物に差し替え、
受けた本文を services.receive に渡す。添付の要求 (kind = chat_file) で返してよい添付なら、そのファイルの bytes を iter_content で返す。
ファイルは作り物の小さな PDF の bytes。media は試験ごとの仮の置き場。
"""

import hashlib
import json
import os
import uuid

import pytest
import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection as db_connection
from django.urls import reverse

from seizou.attachments import services as attachment_services
from seizou.connect import services, signing
from seizou.connect.models import ChatFile, ChatMessage, Inbox
from tests.test_connect_chat import _chat_raw
from tests.test_connect_reply import (  # noqa: F401 (fixture)
    ORDER_ENV,
    REPLY_ENV,
    _send_order,
    connections,
    customer,
    item,
    order_secret,
    own_item,
    reply_secret,
    subcontractor,
    user,
)

#: 送る JSON の attachments の 1 つの鍵 (docs/edi-items.md の kind = chat_message の attachments[] の行と同じ)
CHAT_FILE_KEYS = {"id", "name", "mime", "size", "sha256"}
PDF = b"%PDF-1.4\n% \xe4\xbd\x9c\xe3\x82\x8a\xe7\x89\xa9\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class FakeResponse:
    """偽の requests の返事 (少しずつ読む iter_content と、断りの JSON)。"""

    def __init__(self, status_code, content=b"", data=None):
        self.status_code = status_code
        self.content = content
        self._data = data
        self.closed = False

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]

    def json(self):
        if self._data is None:
            raise ValueError("JSON ではない")
        return self._data

    def close(self):
        self.closed = True


class RoundTrip:
    """相手の受け口の役 (注文・会話の言葉・添付の要求)。tamper があれば、返す添付の bytes を差し替える。raises があれば投げる。"""

    def __init__(self, tamper=None, raises=None):
        self.tamper = tamper
        self.raises = raises
        self.calls = []

    def __call__(self, url, data=None, headers=None, timeout=None, stream=False, **kwargs):
        self.calls.append(
            {"url": url, "data": data, "timeout": timeout, "stream": stream, "in_atomic_block": db_connection.in_atomic_block}
        )
        if self.raises is not None:
            raise self.raises
        result = services.receive(data, headers)
        if isinstance(result, str):
            return FakeResponse(400, data={"status": "rejected", "message": result})
        if isinstance(result, services.ChatFileGrant):
            with result.chat_file.file.open("rb") as handle:
                content = handle.read()
            return FakeResponse(200, self.tamper(content) if self.tamper else content)
        return FakeResponse(200, data={"status": "received", "id": str(result.message_id)})


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def _pdf(name="作り物の図面.pdf", content=PDF):
    return SimpleUploadedFile(name, content, content_type="application/pdf")


def _trip(monkeypatch, **options):
    fake = RoundTrip(**options)
    monkeypatch.setattr("requests.post", fake)
    return fake


def _sent_with_file(monkeypatch, user, subcontractor, item, **post):  # noqa: F811
    """発注を送り、発注の側から添付つきの言葉を送る。返す物は (発注, 受信した注文, 送った言葉, 偽の物)。"""
    _trip(monkeypatch)
    po = _send_order(subcontractor, item, user)
    inbox = Inbox.objects.get()
    fake = _trip(monkeypatch)
    saved = services.post_chat_message(po, post.get("body", "図面の版を確かめてください。"), user, file=post.get("file", _pdf()))
    return po, inbox, saved, fake


def _received_file(saved):
    return ChatFile.objects.get(file_id=saved.files.get().file_id, message__direction=ChatMessage.DIRECTION_RECEIVED)


def _storage_files(media):
    return [path for path in media.rglob("*") if path.is_file()] if media.exists() else []


# ---------------------------------------------------------------- (a) 添付つきで書く


@pytest.mark.django_db
def test_post_with_file_sends_only_the_items(monkeypatch, media, user, subcontractor, item, connections):  # noqa: F811
    _po, inbox, saved, fake = _sent_with_file(monkeypatch, user, subcontractor, item)

    mine = saved.files.get()
    assert mine.file and mine.file.name.startswith("chat/")
    assert mine.sha256 == hashlib.sha256(PDF).hexdigest()
    assert mine.mime == "application/pdf" and mine.size == len(PDF) and mine.name == "作り物の図面.pdf"
    sent = json.loads(fake.calls[0]["data"].decode("utf-8"))
    (attachment,) = sent["attachments"]
    assert set(attachment) == CHAT_FILE_KEYS
    assert attachment["id"] == str(mine.file_id)
    assert "http" not in json.dumps(attachment) and "/connect/" not in json.dumps(attachment)
    other = _received_file(saved)
    assert other.message.inbox == inbox
    assert not other.file and other.fetch_response == ""
    assert other.mime == "application/pdf"


# ---------------------------------------------------------------- (b) 取りに行く


@pytest.mark.django_db
def test_fetch_keeps_the_same_bytes(monkeypatch, media, user, subcontractor, item, connections):  # noqa: F811
    _po, _inbox, saved, _fake = _sent_with_file(monkeypatch, user, subcontractor, item)
    fake = _trip(monkeypatch)

    fetched = services.fetch_chat_file(_received_file(saved), user)

    assert fetched.fetch_response == "受け取った" and fetched.fetched_at is not None
    with fetched.file.open("rb") as handle:
        assert handle.read() == PDF
    assert fake.calls[0]["stream"] is True and fake.calls[0]["timeout"] == (10, 60)
    request = json.loads(fake.calls[0]["data"].decode("utf-8"))
    assert set(request) == {"id", "kind", "file_id"} and request["kind"] == "chat_file"
    # 2 回目は「もう受け取った」で断る
    with pytest.raises(ValueError) as again:
        services.fetch_chat_file(fetched, user)
    assert str(again.value).startswith("保存できません。")
    with pytest.raises(ValueError):
        services.fetch_chat_file(saved.files.get(), user)


@pytest.mark.django_db(transaction=True)
def test_fetch_request_is_sent_outside_atomic(monkeypatch, media, user, subcontractor, item, connections):  # noqa: F811
    _po, _inbox, saved, _fake = _sent_with_file(monkeypatch, user, subcontractor, item)
    fake = _trip(monkeypatch)

    services.fetch_chat_file(_received_file(saved), user)

    assert [call["in_atomic_block"] for call in fake.calls] == [False]


# ---------------------------------------------------------------- (c) 取りに行かれる口の断り


def _file_request(secret, file_id):
    msg_id = str(uuid.uuid4())
    raw = json.dumps({"id": msg_id, "kind": "chat_file", "file_id": file_id}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return services.receive(raw, signing.headers_for(secret, msg_id, raw))


@pytest.mark.django_db
def test_file_request_access_control(monkeypatch, media, user, subcontractor, item, connections):  # noqa: F811
    _po, inbox, saved, _fake = _sent_with_file(monkeypatch, user, subcontractor, item)
    file_id = str(saved.files.get().file_id)

    # 署名の取引先 S001 (注文回答の鍵) は発注の相手なので返す。C001 (注文の鍵) は、その添付の会話の相手でないので「ありません」
    assert isinstance(_file_request(os.environ[REPLY_ENV], file_id), services.ChatFileGrant)
    assert _file_request(os.environ[ORDER_ENV], file_id) == services.MESSAGE_CHAT_FILE_UNKNOWN
    assert _file_request(os.environ[REPLY_ENV], str(uuid.uuid4())) == services.MESSAGE_CHAT_FILE_UNKNOWN
    assert _file_request(os.environ[REPLY_ENV], "not-a-uuid") == services.MESSAGE_CHAT_FILE_SHAPE

    # 相手から届いた言葉の添付 (自社の送った側の行でない) は返さない
    received_only = str(uuid.uuid4())
    msg_id, raw = _chat_raw(
        str(inbox.message_id),
        body="",
        attachments=[{"id": received_only, "name": "届いた.pdf", "mime": "application/pdf", "size": 10, "sha256": "a" * 64}],
    )
    assert isinstance(services.receive(raw, signing.headers_for(os.environ[ORDER_ENV], msg_id, raw)), services.ChatReceipt)
    assert _file_request(os.environ[ORDER_ENV], received_only) == services.MESSAGE_CHAT_FILE_UNKNOWN
    assert _file_request(os.environ[REPLY_ENV], received_only) == services.MESSAGE_CHAT_FILE_UNKNOWN


# ---------------------------------------------------------------- (d) 中身の確かめ・(e) 届かない


@pytest.mark.django_db
def test_fetch_refuses_bytes_that_do_not_match(monkeypatch, media, user, subcontractor, item, connections):  # noqa: F811
    _po, _inbox, saved, _fake = _sent_with_file(monkeypatch, user, subcontractor, item)
    target = _received_file(saved)

    _trip(monkeypatch, tamper=lambda content: content[:-1] + b"X")
    tampered = services.fetch_chat_file(target, user)
    assert tampered.fetch_response.startswith("まだ受け取っていない: 届いた中身が、")
    assert not tampered.file and tampered.fetched_at is None

    big = _trip(monkeypatch, tamper=lambda content: b"0" * (attachment_services.MAX_BYTES + 1))
    too_big = services.fetch_chat_file(target, user)
    assert too_big.fetch_response.startswith("まだ受け取っていない: 届いた中身が、")
    assert not too_big.file
    assert len(big.calls) == 1


@pytest.mark.django_db
def test_unreachable_fetch_can_be_retried(monkeypatch, media, user, subcontractor, item, connections):  # noqa: F811
    _po, _inbox, saved, _fake = _sent_with_file(monkeypatch, user, subcontractor, item)
    target = _received_file(saved)

    _trip(monkeypatch, raises=requests.ConnectionError())
    down = services.fetch_chat_file(target, user)
    assert down.fetch_response.startswith("まだ受け取っていない: 届きません: ")

    _trip(monkeypatch)
    assert services.fetch_chat_file(down, user).fetch_response == "受け取った"


# ---------------------------------------------------------------- (f) 書くときの断り


@pytest.mark.django_db
def test_post_refuses_bad_files_and_keeps_nothing(monkeypatch, media, user, subcontractor, item, connections):  # noqa: F811
    _trip(monkeypatch)
    po = _send_order(subcontractor, item, user)
    fake = _trip(monkeypatch)
    big = SimpleUploadedFile("大きい.pdf", b"0" * (attachment_services.MAX_BYTES + 1), content_type="application/pdf")
    cases = [
        (big, attachment_services.MESSAGE_TOO_LARGE),
        (_pdf("絵.svg"), attachment_services.MESSAGE_BAD_EXTENSION),
        (_pdf("空.pdf", b""), attachment_services.MESSAGE_EMPTY),
        (_pdf("名" * 97 + ".pdf"), attachment_services.MESSAGE_NAME_TOO_LONG),
    ]
    for file, message in cases:
        with pytest.raises(ValueError) as refused:
            services.post_chat_message(po, "作り物の言葉", user, file=file)
        assert str(refused.value) == "送れません。" + message
    assert not ChatMessage.objects.exists() and not ChatFile.objects.exists()
    assert _storage_files(media) == []
    assert fake.calls == []
    with pytest.raises(ValueError) as empty:
        services.post_chat_message(po, "  ", user)
    assert str(empty.value).startswith("送れません。言葉か添付がありません。")

    # 言葉が空で添付だけは送れて、受けた側にも言葉の空の行と添付の行ができる
    only_file = services.post_chat_message(po, "", user, file=_pdf())
    assert only_file.sent_at is not None
    other = _received_file(only_file)
    assert other.message.body == ""


# ---------------------------------------------------------------- (g) 受け口の断り


@pytest.mark.django_db
def test_receive_checks_attachment_items(monkeypatch, media, user, subcontractor, item, order_secret, connections):  # noqa: F811
    _trip(monkeypatch)
    _send_order(subcontractor, item, user)
    order_id = str(Inbox.objects.get().message_id)

    def attachment(**changes):
        return {"id": str(uuid.uuid4()), "name": "作り物.pdf", "mime": "application/pdf", "size": 10, "sha256": "b" * 64, **changes}

    bad = [
        {"attachments": [attachment(size=0)]},
        {"attachments": [attachment(size=attachment_services.MAX_BYTES + 1)]},
        {"attachments": [attachment(size=True)]},
        {"attachments": [attachment(name="絵.svg")]},
        {"attachments": [attachment(sha256="b" * 63)]},
        {"attachments": "作り物"},
        {"body": ""},
        {"body": "  ", "attachments": []},
    ]
    for changes in bad:
        msg_id, raw = _chat_raw(order_id, **changes)
        assert services.receive(raw, signing.headers_for(order_secret, msg_id, raw)) == services.MESSAGE_CHAT_SHAPE, changes
    assert not ChatMessage.objects.exists()

    msg_id, raw = _chat_raw(order_id, body="", attachments=[attachment(mime="text/html")])
    receipt = services.receive(raw, signing.headers_for(order_secret, msg_id, raw))
    assert isinstance(receipt, services.ChatReceipt)
    # mime は読まず、自社の形式の表の値を入れる
    assert receipt.chat_message.files.get().mime == "application/pdf"
    # 同じ番号の 2 回目は添付の行も作らない
    services.receive(raw, signing.headers_for(order_secret, msg_id, raw))
    assert ChatFile.objects.count() == 1


# ---------------------------------------------------------------- (h) 口


@pytest.mark.django_db
def test_fetch_and_download_views(monkeypatch, client, media, user, subcontractor, item, connections):  # noqa: F811
    _po, _inbox, saved, _fake = _sent_with_file(monkeypatch, user, subcontractor, item)
    target = _received_file(saved)
    download = reverse("connect:chatfile_download", args=[target.pk])

    assert client.get(download).status_code == 302  # ログインの前は login へ
    client.force_login(user)
    assert client.get(download).status_code == 404  # 受け取る前

    _trip(monkeypatch, raises=requests.ConnectionError())
    fetch = reverse("connect:chatfile_fetch", args=[target.pk])
    response = client.post(fetch)
    assert response.status_code == 200 and "HX-Trigger" not in response
    assert "取り直す" in response.content.decode()

    _trip(monkeypatch)
    response = client.post(fetch, {"manual": "1"})
    assert response.status_code == 200
    assert json.loads(response["HX-Trigger"]) == {"seizou:saved": {"message": "添付を受け取りました。", "level": "success"}}
    assert 'href="' + download + '"' in response.content.decode()
    # もう受け取った添付は、今の行の断片をそのまま返す (知らせは出さない)
    again = client.post(fetch, {"manual": "1"})
    assert again.status_code == 200 and "HX-Trigger" not in again

    opened = client.get(download)
    assert opened.status_code == 200
    assert opened["Content-Type"] == "application/pdf"
    assert opened["Content-Disposition"].startswith("inline")
    assert b"".join(opened.streaming_content) == PDF
    # 自社が付けた添付の行は取りに行く口の外 (404)
    assert client.post(reverse("connect:chatfile_fetch", args=[saved.files.get().pk])).status_code == 404


@pytest.mark.django_db
def test_chat_panel_shows_file_rows(monkeypatch, client, media, user, subcontractor, item, connections):  # noqa: F811
    po, inbox, saved, _fake = _sent_with_file(monkeypatch, user, subcontractor, item)
    client.force_login(user)

    # 自社が付けた添付は、吹き出しの中に開く口への link
    html = client.get(reverse("purchasing:purchaseorder_detail", args=[po.pk])).content.decode()
    assert 'href="' + reverse("connect:chatfile_download", args=[saved.files.get().pk]) + '"' in html
    assert 'enctype="multipart/form-data"' in html and 'name="file"' in html
    # 相手から届いてまだ取りに行っていない添付は、出た時に取りに行く
    target = _received_file(saved)
    html = client.get(reverse("connect:inbox_detail", args=[inbox.pk])).content.decode()
    assert 'hx-post="' + reverse("connect:chatfile_fetch", args=[target.pk]) + '" hx-trigger="load"' in html
    assert "受け取っています" in html
