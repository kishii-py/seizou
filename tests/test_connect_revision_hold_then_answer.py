"""変更の版を保留した後に、受け入れるか断ると、その答えが新しい注文回答として相手に届く事を見る (511 回目の本体の直し)。

訳: 受信した注文の受け入れの画面は、変更と取消の版の行にも「保留する」を出す。保留 (AJ) を答えた後に版を受け入れると、
直す前は、保留の答えのタイプと注文回答の番号が行に残ったまま送り直され、相手の受け口は同じ番号の 2 回目と見て当てなかった
(相手の発注には「相手が保留した」のまま残り、受け入れた事が伝わらない)。第 1 版の受け入れと断り (connect.services の _accept と
reject) は、前から、保留の後の答えを新しい番号で送る (_restart_reply)。版の受け入れ (_accept_revision) だけが抜けていた。
2 社の役とつながりは tests/test_connect_reply.py と tests/test_connect_revision_accept.py の helper と fixture を使う (そのファイルは変えない)。
"""

import pytest

from seizou.connect import services
from seizou.connect.models import Inbox, Outbox
from seizou.core import edi_codes
from tests.test_connect_reply import (  # noqa: F401 (fixture)
    _into_receive,
    connections,
    customer,
    order_secret,
    reply_secret,
    subcontractor,
    user,
)
from tests.test_connect_revision_accept import (  # noqa: F401 (fixture)
    _accept_first,
    _bodies,
    _mapping,
    _revise,
    _send,
    _send_first,
    items,
)
from tests.test_connect_send import FakePost


@pytest.fixture
def held_revision(monkeypatch, user, subcontractor, items, connections):  # noqa: F811
    """第 1 版を受け入れた後に届いた変更の版を、保留にした所 (相手の発注の送った注文の行には保留が届いている)。"""
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    po = _send_first(subcontractor, items, user)
    base, _order = _accept_first(items, user)
    _send(_revise(po, items), user)
    revision = Inbox.objects.get(revises=base)
    services.hold(revision, user)
    revision.refresh_from_db()
    assert (revision.status, revision.reply_type) == (Inbox.STATUS_RECEIVED, edi_codes.REPLY_PENDING)
    assert revision.reply_sent_at is not None
    row = Outbox.objects.filter(purchase_order=po, revision_no=2).order_by("pk").first()
    assert row.reply_type == edi_codes.REPLY_PENDING
    return fake, revision, row


@pytest.mark.django_db
def test_held_revision_then_accepted_reaches_the_buyer_as_acceptance(user, items, held_revision):  # noqa: F811
    fake, revision, row = held_revision
    held_id = revision.reply_message_id

    services.accept(revision, _mapping(items, 4), user)

    revision.refresh_from_db()
    row.refresh_from_db()
    assert revision.status == Inbox.STATUS_ACCEPTED
    # 版の受諾は答えのタイプを書かずに送る (send_reply が状態から受諾と読む)。保留のタイプと番号は残さない
    assert revision.reply_type == ""
    assert revision.reply_message_id is not None and revision.reply_message_id != held_id
    assert revision.reply_sent_at is not None and revision.reply_response == "受け取った"
    # 相手 (発注した側) の送った注文の行に、受諾が届いている
    assert row.reply_type == edi_codes.REPLY_ACCEPTED
    assert str(row).endswith("・相手が受け入れた")
    # 送った注文回答は 第 1 版の受諾・版の保留・版の受諾 の 3 通で、最後の 2 通は番号が違う
    replies = _bodies(fake, "order_reply")
    assert [body["reply_type"] for body in replies] == ["AP", "AJ", "AP"]
    assert replies[1]["id"] != replies[2]["id"]
    assert replies[2]["order_revision_no"] == 2


@pytest.mark.django_db
def test_held_revision_then_rejected_reaches_the_buyer_as_refusal(user, held_revision):  # noqa: F811
    """保留の後の断りは、前から新しい番号で届く (reject の作り)。版の行でも同じである事を固定する。"""
    fake, revision, row = held_revision
    held_id = revision.reply_message_id

    services.reject(revision, "作り物の断り", user)

    revision.refresh_from_db()
    row.refresh_from_db()
    assert (revision.status, revision.reply_type) == (Inbox.STATUS_REJECTED, edi_codes.REPLY_REJECTED)
    assert revision.reply_message_id != held_id
    assert (row.reply_type, row.reply_note) == (edi_codes.REPLY_REJECTED, "作り物の断り")
    assert [body["reply_type"] for body in _bodies(fake, "order_reply")] == ["AP", "AJ", "RE"]
