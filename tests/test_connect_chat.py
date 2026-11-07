"""受注と発注の 1 件ごとの会話 (connect.services の post_chat_message・resend_chat_message・chat_peer と、受け口の kind = chat_message) のテスト。

意図: 本人の決め CHAT-1「両方の会社の seizou に同じ会話を残す」「届かなかった言葉は送り直せる」と、CHAT-2「つながっていない取引では押せない」が、
1 つの DB に 2 社の役を置く今の試験の形で崩れないことを見るため。

2 社の役とつながりと偽の requests.post は tests/test_connect_reply.py と同じ (発注を送り、届いた受信した注文を services.accept で受注にする)。
相手への HTTP は requests.post を偽の物に差し替え、受けた本文を services.receive に渡す (外へは出ない)。取引先・品目・言葉は作り物。
"""

import json
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
import requests
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.connect import services, signing
from seizou.connect.models import ChatMessage, Connection, Inbox, Outbox
from seizou.core import access
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from tests.test_connect_reply import (  # noqa: F401 (fixture)
    REPLY_ENV,
    _into_receive,
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
from tests.test_connect_send import FakePost, FakeResponse
from tests.test_r10_inbox_accept import _line, _payload

#: 会話の言葉の送る JSON の鍵 (発注した側が送る物。受けた側が送る物は buyer の代わりに supplier)。docs/edi-items.md の kind = chat_message の表と同じ
CHAT_KEYS = {"id", "kind", "sent_at", "order_id", "order_no", "sender_role", "buyer", "author", "written_at", "body"}


def _round_trip(monkeypatch):
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    return fake


def _sent_order(monkeypatch, subcontractor, item, user):  # noqa: F811
    """発注を送って、届いた受信した注文と一緒に返す。"""
    _round_trip(monkeypatch)
    po = _send_order(subcontractor, item, user)
    return po, Inbox.objects.get()


def _received(message_id):
    return ChatMessage.objects.get(message_id=message_id, direction=ChatMessage.DIRECTION_RECEIVED)


# ---------------------------------------------------------------- (a) 発注から書く


@pytest.mark.django_db
def test_post_from_purchase_order_leaves_the_same_message_on_both_sides(monkeypatch, user, subcontractor, item, connections):  # noqa: F811
    po, inbox = _sent_order(monkeypatch, subcontractor, item, user)
    fake = _round_trip(monkeypatch)

    saved = services.post_chat_message(po, "作り物の言葉。図面の版を確かめたいです。", user)

    assert saved.direction == ChatMessage.DIRECTION_SENT
    assert saved.purchase_order == po and saved.inbox is None
    assert saved.sent_at is not None
    assert saved.response == "受け取った"
    assert saved.saved_message == "相手に送りました。"
    assert getattr(saved, "saved_level", "") != "warning"
    other = _received(saved.message_id)
    assert other.inbox == inbox and other.purchase_order is None
    assert other.body == saved.body
    assert other.written_at == saved.written_at
    assert other.received_at is not None
    assert other.delivery_state() == ""
    assert saved.delivery_state().startswith("届いた ")
    assert len(fake.calls) == 1


# ---------------------------------------------------------------- (f) HTTP は atomic の外


@pytest.mark.django_db(transaction=True)
def test_chat_http_is_sent_outside_atomic(monkeypatch, user, subcontractor, item, connections):  # noqa: F811
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    down = FakePost(raises=requests.ConnectionError())
    monkeypatch.setattr("requests.post", down)

    saved = services.post_chat_message(po, "作り物の言葉", user)
    fake = _round_trip(monkeypatch)
    services.resend_chat_message(saved, user)

    # 書く時の送りも、送り直しも atomic の外 (R17)
    assert [call["in_atomic_block"] for call in down.calls + fake.calls] == [False, False]


# ---------------------------------------------------------------- (b) 受けた側から書く


@pytest.mark.django_db
def test_post_from_order_and_inbox_reaches_the_purchase_order(monkeypatch, user, subcontractor, item, own_item, connections):  # noqa: F811
    po, inbox = _sent_order(monkeypatch, subcontractor, item, user)
    order = services.accept(inbox, {"T-100": own_item}, user)
    _round_trip(monkeypatch)

    from_order = services.post_chat_message(order, "受注の画面から書いた作り物の言葉", user)
    from_inbox = services.post_chat_message(inbox, "受信した注文の画面から書いた作り物の言葉", user)

    assert from_order.inbox == order.inbox_message
    assert from_inbox.inbox == inbox
    for saved in (from_order, from_inbox):
        assert saved.sent_at is not None
        other = _received(saved.message_id)
        assert other.purchase_order == po and other.inbox is None
    assert list(services.chat_messages_for(order)) == [from_order, from_inbox]
    assert list(services.chat_messages_for(po)) == [_received(from_order.message_id), _received(from_inbox.message_id)]


# ---------------------------------------------------------------- (c) 送る JSON の鍵


@pytest.mark.django_db
def test_sent_json_keys_and_author(monkeypatch, user, subcontractor, item, own_item, connections):  # noqa: F811
    po, inbox = _sent_order(monkeypatch, subcontractor, item, user)
    fake = _round_trip(monkeypatch)

    services.post_chat_message(po, "作り物の言葉", user)

    call = fake.calls[0]
    sent = json.loads(call["data"].decode("utf-8"))
    assert set(sent) == CHAT_KEYS
    assert sent["kind"] == "chat_message"
    assert sent["sender_role"] == "buyer"
    assert sent["order_id"] == str(Outbox.objects.filter(purchase_order=po).order_by("pk").first().message_id)
    assert sent["order_no"] == po.po_no
    assert sent["author"] == ""
    assert "tester" not in call["data"].decode("utf-8")
    assert set(sent["buyer"]) == {"id", "name"}
    # 本文は鍵の並びを固定した JSON の bytes そのまま
    assert call["data"] == json.dumps(sent, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    # 受けた側から書いた物は buyer の代わりに supplier。氏名のある利用者は author がその氏名
    user.first_name, user.last_name = "作り物の", "担当"
    user.save()
    services.accept(inbox, {"T-100": own_item}, user)
    fake = _round_trip(monkeypatch)
    services.post_chat_message(inbox, "作り物の返事", user)
    sent = json.loads(fake.calls[0]["data"].decode("utf-8"))
    assert set(sent) == (CHAT_KEYS - {"buyer"}) | {"supplier"}
    assert sent["sender_role"] == "supplier"
    assert sent["order_id"] == str(inbox.message_id)
    assert sent["author"] == "作り物の 担当"
    assert _received(sent["id"]).author_name == "作り物の 担当"


# ---------------------------------------------------------------- (d) 届かない・送り直す


@pytest.mark.django_db
def test_unreachable_message_is_kept_and_can_be_resent_once(monkeypatch, user, subcontractor, item, connections):  # noqa: F811
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError()))

    saved = services.post_chat_message(po, "届かない作り物の言葉", user)

    assert saved.sent_at is None
    assert saved.response.startswith("届きません: ")
    assert saved.saved_level == "warning"
    assert saved.saved_message.startswith("書いた言葉は残しましたが、相手に届いていません。")
    assert saved.delivery_state().startswith("届きません: ")
    assert not ChatMessage.objects.filter(direction=ChatMessage.DIRECTION_RECEIVED).exists()

    _round_trip(monkeypatch)
    again = services.resend_chat_message(saved, user)
    assert again.sent_at is not None
    assert again.saved_message == "送り直しました。相手に届きました。"
    assert ChatMessage.objects.filter(message_id=saved.message_id, direction=ChatMessage.DIRECTION_RECEIVED).count() == 1

    with pytest.raises(ValueError) as delivered:
        services.resend_chat_message(again, user)
    assert str(delivered.value).startswith("送れません。")
    with pytest.raises(ValueError) as received:
        services.resend_chat_message(_received(saved.message_id), user)
    assert str(received.value).startswith("送れません。")


@pytest.mark.django_db
def test_delivery_state_for_unreadable_reply(monkeypatch, user, subcontractor, item, connections):  # noqa: F811
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    monkeypatch.setattr("requests.post", FakePost(reply=FakeResponse(500)))

    saved = services.post_chat_message(po, "作り物の言葉", user)

    assert saved.response == "返事が読めません (HTTP 500)"
    assert saved.delivery_state() == "届いていない: 返事が読めません (HTTP 500)"


# ---------------------------------------------------------------- (e) 同じ番号の 2 回目


@pytest.mark.django_db
def test_same_chat_message_twice_is_kept_once(monkeypatch, user, subcontractor, item, connections):  # noqa: F811
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    held = FakePost()
    monkeypatch.setattr("requests.post", held)
    services.post_chat_message(po, "作り物の言葉", user)
    (call,) = held.calls
    headers = {key: value for key, value in call["headers"].items() if key != "Content-Type"}

    first = services.receive(call["data"], headers)
    second = services.receive(call["data"], headers)

    assert isinstance(first, services.ChatReceipt)
    assert first.chat_message.pk == second.chat_message.pk
    assert ChatMessage.objects.filter(direction=ChatMessage.DIRECTION_RECEIVED).count() == 1


# ---------------------------------------------------------------- (g) 使えない 4 つの訳


def _assert_refused(monkeypatch, target, user, reason):  # noqa: F811
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    assert services.chat_peer(target).reason == reason
    before = ChatMessage.objects.count()
    with pytest.raises(ValueError) as refused:
        services.post_chat_message(target, "作り物の言葉", user)
    assert str(refused.value) == "送れません。" + reason
    assert ChatMessage.objects.count() == before
    assert fake.calls == []


@pytest.mark.django_db
def test_not_connected_purchase_order_uses_the_chat2_words(monkeypatch, user, item):  # noqa: F811
    lonely = masters_services.create_partner({"code": "S009", "name": "作り物のつながっていない協力企業", "role": "subcontractor"}, user)
    po = _send_order(lonely, item, user)

    _assert_refused(
        monkeypatch,
        po,
        user,
        "この取引先は seizou とつながっていないので、会話は使えません。社内の書き込みはメモに書いてください。",
    )


@pytest.mark.django_db
def test_manual_order_and_intake_inbox_are_not_linked(monkeypatch, user, customer, item, connections):  # noqa: F811
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("3"), "unit_price": Decimal("100")}], user, order_date=date(2026, 11, 2)
    )
    intake = services.record_inbox(
        customer, _payload(str(uuid.uuid4()), [_line(1, "T-100")]), user, source=Inbox.SOURCE_INTAKE
    )

    for target in (order, intake):
        _assert_refused(monkeypatch, target, user, services.CHAT_REASON_NOT_LINKED)


@pytest.mark.django_db
def test_receive_only_partner_and_unsent_purchase_order(monkeypatch, user, subcontractor, item, connections):  # noqa: F811
    receive_only = masters_services.create_partner(
        {"code": "S008", "name": "作り物の受ける向きだけの協力企業", "role": "subcontractor"}, user
    )
    services.register_connection(receive_only, "", "SEIZOU_PARTNER_RECEIVEONLY_SECRET", Connection.DIRECTION_RECEIVE, user)
    po = _send_order(receive_only, item, user)
    _assert_refused(monkeypatch, po, user, services.CHAT_REASON_NO_SEND)

    unsent = purchasing_services.create_purchase_order(
        subcontractor,
        [{"item": item, "qty": Decimal("1"), "unit_price": Decimal("10"), "due_date": date(2026, 11, 20)}],
        user,
        order_date=date(2026, 11, 2),
        send=False,
    )
    _assert_refused(monkeypatch, unsent, user, services.CHAT_REASON_NOT_SENT)


# ---------------------------------------------------------------- 書く言葉の断り


@pytest.mark.django_db
def test_empty_and_too_long_words_are_refused(monkeypatch, user, subcontractor, item, connections):  # noqa: F811
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    for body in ("", "  \n ", "あ" * 2001):
        with pytest.raises(ValueError) as refused:
            services.post_chat_message(po, body, user)
        assert str(refused.value).startswith("送れません。")
    assert not ChatMessage.objects.exists()
    assert fake.calls == []
    # 2000 字ちょうどは送れる。前後の空白も変えずに残す (送ったとおりに残す)
    _round_trip(monkeypatch)
    assert services.post_chat_message(po, "あ" * 2000, user).sent_at is not None
    assert services.post_chat_message(po, " 前後の空白 ", user).body == " 前後の空白 "


# ---------------------------------------------------------------- (h) 断る処理を足していない


@pytest.mark.django_db
def test_cancelled_purchase_order_and_rejected_inbox_still_talk(monkeypatch, user, subcontractor, item, connections):  # noqa: F811
    po, inbox = _sent_order(monkeypatch, subcontractor, item, user)
    services.reject(inbox, "作り物の断る理由", user)
    purchasing_services.cancel_purchase_order(po.__class__.objects.get(pk=po.pk), user)
    _round_trip(monkeypatch)

    from_po = services.post_chat_message(po, "取消の後の作り物の言葉", user)
    from_inbox = services.post_chat_message(inbox, "断った後の作り物の言葉", user)

    assert from_po.sent_at is not None and from_inbox.sent_at is not None
    assert _received(from_po.message_id).inbox == inbox
    assert _received(from_inbox.message_id).purchase_order == po


# ---------------------------------------------------------------- (i) 受け口の断り


def _chat_raw(base_order_id, **changes):
    msg_id = str(uuid.uuid4())
    data = {
        "id": msg_id,
        "kind": "chat_message",
        "sent_at": "2026-11-07T10:00:00+09:00",
        "order_id": base_order_id,
        "order_no": "PO-0001",
        "sender_role": "buyer",
        "buyer": {"id": "a-company", "name": "作り物の発注する会社"},
        "author": "",
        "written_at": "2026-11-07T09:59:00+09:00",
        "body": "作り物の言葉",
    }
    data.update(changes)
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return msg_id, raw


@pytest.mark.django_db
def test_receive_refuses_unreadable_chat_messages(monkeypatch, user, subcontractor, item, order_secret, connections):  # noqa: F811
    _po, inbox = _sent_order(monkeypatch, subcontractor, item, user)
    order_id = str(inbox.message_id)
    cases = [
        ({"body": "   "}, services.MESSAGE_CHAT_SHAPE),
        ({"body": "あ" * 2001}, services.MESSAGE_CHAT_SHAPE),
        ({"body": 12}, services.MESSAGE_CHAT_SHAPE),
        ({"sender_role": "maker"}, services.MESSAGE_CHAT_SHAPE),
        ({"order_id": "not-a-uuid"}, services.MESSAGE_CHAT_SHAPE),
        ({"written_at": "きのう"}, services.MESSAGE_CHAT_SHAPE),
        ({"written_at": "2026-11-07T09:59:00"}, services.MESSAGE_CHAT_SHAPE),
        ({"order_id": str(uuid.uuid4())}, services.MESSAGE_CHAT_UNKNOWN_ORDER),
    ]
    for changes, expected in cases:
        msg_id, raw = _chat_raw(order_id, **changes)
        assert services.receive(raw, signing.headers_for(order_secret, msg_id, raw)) == expected, changes
    assert not ChatMessage.objects.exists()

    # ほかの取引先の注文の番号 (受信した注文の取引先は C001、署名は S001 の注文回答の鍵)
    msg_id, raw = _chat_raw(order_id)
    other = services.receive(raw, signing.headers_for(os.environ[REPLY_ENV], msg_id, raw))
    assert other == services.MESSAGE_CHAT_OTHER_PARTNER
    assert not ChatMessage.objects.exists()

    # 後の版が足す鍵 (attachments など) は読み捨てて、言葉は受ける。author は 100 字で切る
    msg_id, raw = _chat_raw(order_id, attachments=[{"id": "x"}], author="名" * 120)
    receipt = services.receive(raw, signing.headers_for(order_secret, msg_id, raw))
    assert isinstance(receipt, services.ChatReceipt)
    assert receipt.chat_message.inbox == inbox
    assert receipt.chat_message.author_name == "名" * 100


# ---------------------------------------------------------------- (k) 書く画面


MODAL = {"HX-Request": "true", "HX-Target": "seizou-modal-box"}


def _form(po, body, **extra):
    return {"target_kind": "purchase_order", "target_id": po.pk, "body": body, **extra}


@pytest.mark.django_db
def test_add_view_saves_in_modal_and_on_the_full_page(monkeypatch, client, user, subcontractor, item, connections):  # noqa: F811
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    _round_trip(monkeypatch)
    client.force_login(user)
    url = reverse("connect:chatmessage_add")

    response = client.post(url, _form(po, "作り物の言葉"), headers=MODAL)
    assert response.status_code == 204
    assert json.loads(response["HX-Trigger"]) == {"seizou:saved": {"message": "相手に送りました。", "level": "success"}}

    # 空の言葉は小窓の形の入力のカードを 200 で返し (小窓の上の赤い帯)、行を作らない
    response = client.post(url, _form(po, "   "), headers=MODAL)
    assert response.status_code == 200
    html = response.content.decode()
    assert "「言葉」の欄に誤りがあります。" in html
    assert "言葉を書いてください。" in html
    assert ChatMessage.objects.filter(direction=ChatMessage.DIRECTION_SENT).count() == 1

    # 画面の全体の POST は next の詳細へ 302 で、次の GET に右上の知らせ
    detail = reverse("purchasing:purchaseorder_detail", args=[po.pk])
    response = client.post(url, _form(po, "画面の全体から書いた作り物の言葉", next=detail))
    assert response.status_code == 302
    assert response["Location"] == detail
    assert "相手に送りました。" in client.get(detail).content.decode()


@pytest.mark.django_db
def test_add_view_refuses_unknown_target_and_unusable_trade(monkeypatch, client, user, item, connections):  # noqa: F811
    lonely = masters_services.create_partner({"code": "S009", "name": "作り物のつながっていない協力企業", "role": "subcontractor"}, user)
    po = _send_order(lonely, item, user)
    client.force_login(user)
    url = reverse("connect:chatmessage_add")

    response = client.post(url, {"target_kind": "purchase_order", "target_id": po.pk + 1000, "body": "作り物"}, headers=MODAL)
    assert response.status_code == 200
    assert "書く先の注文が見つかりません。" in response.content.decode()
    response = client.post(url, _form(po, "作り物の言葉"), headers=MODAL)
    assert response.status_code == 200
    assert services.CHAT_REASON_NOT_CONNECTED in response.content.decode()
    assert not ChatMessage.objects.exists()


# ---------------------------------------------------------------- (l) 送り直す口


@pytest.mark.django_db
def test_resend_view(monkeypatch, client, user, subcontractor, item, connections):  # noqa: F811
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError()))
    saved = services.post_chat_message(po, "届かない作り物の言葉", user)
    client.force_login(user)
    url = reverse("connect:chatmessage_resend", args=[saved.pk])

    assert client.get(url).status_code == 405
    response = client.post(url, headers={"HX-Request": "true"})
    assert response.status_code == 204
    assert json.loads(response["HX-Trigger"])["seizou:saved"]["level"] == "warning"

    _round_trip(monkeypatch)
    response = client.post(url, headers={"HX-Request": "true"})
    assert response.status_code == 204
    assert json.loads(response["HX-Trigger"]) == {
        "seizou:saved": {"message": "送り直しました。相手に届きました。", "level": "success"}
    }
    # 相手から届いた行は 404。もう届いた行は断りの字を右上の赤い知らせにして詳細へ移る
    assert client.post(reverse("connect:chatmessage_resend", args=[_received(saved.message_id).pk])).status_code == 404
    response = client.post(url)
    assert response.status_code == 302
    assert response["Location"] == reverse("purchasing:purchaseorder_detail", args=[po.pk])
    assert "この言葉は相手に届いています。" in client.get(response["Location"]).content.decode()


# ---------------------------------------------------------------- (n) 編集の権限で絞らない


@pytest.mark.django_db
def test_no_write_permission_still_talks(monkeypatch, client, settings, user, subcontractor, item, connections):  # noqa: F811
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    settings.SEIZOU = {"access_control": {"department": "on", "write": "on", "money": "on"}}
    department = accounts_services.create_department("会話", ["connect", "orders", "purchasing"], [], None)
    accounts_services.issue_account("chat-only", "kumo-tests-2611", [department], [], {}, None)
    plain = get_user_model().objects.get(username="chat-only")
    access.require_write(plain, "connect.post_chat_message")
    client.force_login(plain)

    _round_trip(monkeypatch)
    response = client.post(reverse("connect:chatmessage_add"), _form(po, "権限の無い人の作り物の言葉"), headers=MODAL)
    assert response.status_code == 204
    assert ChatMessage.objects.filter(direction=ChatMessage.DIRECTION_SENT, author=plain).count() == 1

    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError()))
    saved = services.post_chat_message(po, "届かない作り物の言葉", plain)
    _round_trip(monkeypatch)
    response = client.post(reverse("connect:chatmessage_resend", args=[saved.pk]), headers={"HX-Request": "true"})
    assert response.status_code == 204
