"""変更と取消の版の受け入れの画面には、「条件を直して受け入れる」を出さない (本人の決め 11-12 18:3x。516 回目の本体の直し)。

訳: 相手が発注を変更して (か取り消して) 送ってきた版の行にも、受け入れの画面に「条件を直して受け入れる」が出ていた。押すと単価・数量・
納期を直す欄が出るが、直した値は使われず、変更をそのまま受け入れた事になっていた (connect.services._accept が、版の行では答えを読まずに
_accept_revision へ渡す。511 回目の確かめで見つけた)。答え方は「受け入れる」「保留する」「断る」の 3 つにする。条件を変えたい時は、
断って理由を書くか、会話で伝える。変更の版にも条件付で答えられるように作る道は採らない。
「条件を直して受け入れる」の画面は、版の行では開かない (読み取りの行と同じく 404。connect/views.py の get_queryset)。
第 1 版 (初めの注文) の「条件を直して受け入れる」は今のまま。
2 社の役とつながりは tests/test_connect_reply.py と tests/test_connect_revision_accept.py の fixture と helper を使う (そのファイルは変えない)。
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.connect.models import Inbox
from tests.test_connect_reply import (  # noqa: F401 (fixture)
    _into_receive,
    customer,
    order_secret,
    reply_secret,
    subcontractor,
    user,
)
from tests.test_connect_revision_accept import (  # noqa: F401 (fixture)
    _accept_first,
    _cancel,
    _revise,
    _send,
    _send_first,
    connections,
    items,
)
from tests.test_connect_send import FakePost

#: 版の行の受け入れの画面に出す案内の字の一部
HINT = "変更と取消には「条件を直して受け入れる」はありません。"


def _urls(inbox):
    return {
        "accept": reverse("connect:inbox_accept", args=[inbox.pk]),
        "conditional": reverse("connect:inbox_accept_conditional", args=[inbox.pk]),
        "hold": reverse("connect:inbox_hold", args=[inbox.pk]),
        "reject": reverse("connect:inbox_reject", args=[inbox.pk]),
    }


@pytest.fixture
def sent_first(monkeypatch, user, subcontractor, items, connections):  # noqa: F811
    """A が送った第 1 版が、B の受信した注文 (未処理) に届いている所。返す物は (発注, 受信した注文)。"""
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_first(subcontractor, items, user)
    return po, Inbox.objects.get(revises=None)


@pytest.mark.django_db
def test_first_version_keeps_the_conditional_entry(client_logged_in, sent_first):
    _po, base = sent_first
    urls = _urls(base)

    body = client_logged_in.get(urls["accept"]).content.decode()

    assert f'href="{urls["conditional"]}"' in body  # 第 1 版は今のまま
    assert HINT not in body
    assert client_logged_in.get(urls["conditional"]).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["change", "cancel"])
def test_revision_has_three_answers_without_the_conditional_entry(client_logged_in, user, items, sent_first, kind):  # noqa: F811
    po, _base = sent_first
    base, _order = _accept_first(items, user)
    _send(_revise(po, items) if kind == "change" else _cancel(po), user)
    revision = Inbox.objects.get(revises=base)
    assert revision.status == Inbox.STATUS_RECEIVED
    urls = _urls(revision)

    body = client_logged_in.get(urls["accept"]).content.decode()

    assert urls["conditional"] not in body  # 「条件を直して受け入れる」の入口が無い
    assert f'href="{urls["hold"]}"' in body and f'href="{urls["reject"]}"' in body  # 保留すると断るは今のまま
    assert HINT in body


@pytest.mark.django_db
def test_conditional_screen_of_a_revision_is_not_found(client_logged_in, user, items, sent_first):  # noqa: F811
    """URL を直に開いても、版の行では「条件を直して受け入れる」の画面は無い (404)。送っても何も変わらない。"""
    po, _base = sent_first
    base, order = _accept_first(items, user)
    _send(_revise(po, items), user)
    revision = Inbox.objects.get(revises=base)
    url = _urls(revision)["conditional"]

    assert client_logged_in.get(url).status_code == 404
    posted = client_logged_in.post(url, {"line_1_unit_price": "900", "line_1_qty": "150", "line_1_due_date": ""})

    assert posted.status_code == 404
    assert Inbox.objects.get(pk=revision.pk).status == Inbox.STATUS_RECEIVED
    assert order.lines.get(line_no=1).qty == Decimal("300")  # 受注は前の版のまま
