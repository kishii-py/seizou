"""変更と取消の版の注文回答が届かなかった時に、受信した注文の詳細から送り直せる事を見る (508 回目の本体の直し。N419 の記録の要望)。

訳: 変更と取消の版を受け入れた・断った時の注文回答は、注文回答のタイプ (reply_type) を書かずに送る (send_reply が状態から決める)。
詳細の「送り直す」は、タイプが空でない行にだけ出していたので、その注文回答が届かなかった時、保存した後の黄の知らせと
今日やることの行は「送り直す」を案内するのに、詳細に「送り直す」が出なかった。届いていない注文回答 (黄の知らせと同じ見方 =
connect.services の _reply_undelivered) の行にも出す。
2 社の役とつながりは tests/test_connect_reply.py と tests/test_connect_revision_accept.py の helper と fixture を使う (そのファイルは変えない)。
"""

import pytest
import requests
from django.urls import reverse

from seizou.connect import services
from seizou.connect.models import Inbox
from tests.test_connect_reply import (  # noqa: F401 (fixture)
    _into_receive,
    connections,
    customer,
    order_secret,
    reply_secret,
    subcontractor,
    user,
)
from tests.test_connect_revision_accept import _accept_first, _cancel, _send, _send_first, items  # noqa: F401 (fixture)
from tests.test_connect_send import FakePost


@pytest.fixture
def undelivered_revision(monkeypatch, user, subcontractor, items, connections):  # noqa: F811
    """取消の版を受け入れたが、注文回答が相手に届かなかった受信した注文 (版の行。注文回答のタイプは空)。"""
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_first(subcontractor, items, user)
    base, _order = _accept_first(items, user)
    _send(_cancel(po), user)
    revision = Inbox.objects.get(revises=base)
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物のつながらない")))
    services.accept(revision, {}, user)
    revision.refresh_from_db()
    return revision


def test_undelivered_revision_reply_can_be_resent_from_the_detail(monkeypatch, client, user, undelivered_revision):  # noqa: F811
    revision = undelivered_revision
    assert revision.reply_type == "" and revision.reply_sent_at is None
    assert services._reply_undelivered(revision)
    client.force_login(user)
    detail = reverse("connect:inbox_detail", args=[revision.pk])
    resend = reverse("connect:inbox_reply_resend", args=[revision.pk])

    body = client.get(detail).content.decode()
    assert f'action="{resend}"' in body  # 詳細に「送り直す」が出る

    # 相手が戻った後に「送り直す」を押すと届き、ボタンが消える
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    response = client.post(resend)
    assert response.status_code == 302
    revision.refresh_from_db()
    assert revision.reply_sent_at is not None
    assert not services._reply_undelivered(revision)
    assert f'action="{resend}"' not in client.get(detail).content.decode()


def test_seed_reply_without_type_gets_no_resend_button(client, user, undelivered_revision):  # noqa: F811
    """注文回答のタイプが空で、返事の欄が作り物の種の字の行 (送る物でない) には、「送り直す」を出さない (届いていない物に数えないのと同じ見方)。"""
    revision = undelivered_revision
    Inbox.objects.filter(pk=revision.pk).update(reply_response=services.MESSAGE_REPLY_SEED)
    client.force_login(user)
    body = client.get(reverse("connect:inbox_detail", args=[revision.pk])).content.decode()
    assert f'action="{reverse("connect:inbox_reply_resend", args=[revision.pk])}"' not in body
