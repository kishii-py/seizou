"""注文回答の「送り直す」は、受注を編集できる人なら、金額の閲覧が無くても押せる (依頼書 N438。本人の決め 11-12 14:3x)。

訳: 送り直しは、すでに決まった答えを同じ中身でもう一度送るだけで、金額を決める操作ではない。出荷の知らせと検収の送り直しは、
前から金額の閲覧が無くても押せる。受け入れた人が不在の時に、事務の人が送り直せる。
直す前は、送り直す口の操作が承認と同じ connect.accept (金額に関わる操作) で、金額の判定が有効の会社では、金額の閲覧の無い人に
「送り直す」が出ず、今日やることの「相手に届いていない注文回答」の行も出なかった。
「受け入れる」「条件を直して受け入れる」「断る」「保留」の権限は変えない。
2 社の役とつながりと、届かなかった注文回答の作り方は tests/test_connect_reply_resend.py の fixture と helper を使う (そのファイルは変えない)。
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import reply, services
from seizou.connect.models import Inbox
from seizou.core import access, dashboard
from tests.test_connect_reply_resend import (  # noqa: F401 (fixture)
    _messages,
    _received,
    _unreachable_reply,
    connections,
    customer,
    item,
    order_secret,
    own_item,
    reply_secret,
    subcontractor,
    user,
)
from tests.test_connect_send import FakePost
from tests.test_core_access import account

#: 編集の権限と金額の閲覧の判定が有効の会社 (部署の判定は切)
WRITE_AND_MONEY = {"department": "off", "write": "on", "money": "on"}
#: 作り物の発注の単価 (tests/test_connect_reply.py の _send_order)。金額の閲覧の無い人の画面に出ない事を見る字
UNIT_PRICE = "25.5"


@pytest.fixture
def judged(settings):
    settings.SEIZOU = {"access_control": dict(WRITE_AND_MONEY)}


def _detail(client, inbox):
    return client.get(reverse("connect:inbox_detail", args=[inbox.pk]))


def _resend_url(inbox):
    return reverse("connect:inbox_reply_resend", args=[inbox.pk])


def _keys(login_id):
    """その人の今日やることの行の key の並び (利用者の物は控えを持たないよう取り直す)。"""
    today = dashboard.today_tasks(get_user_model().objects.get(username=login_id))
    return [] if today is None else [row["key"] for row in today["rows"]]


# --- (a) 受注を編集できて、金額の閲覧の無い人 ---


@pytest.mark.django_db
def test_clerk_without_money_can_resend(judged, monkeypatch, client, user, subcontractor, customer, item, own_item, connections):  # noqa: F811
    _po, inbox = _unreachable_reply(monkeypatch, subcontractor, item, own_item, user)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    client.force_login(account("jimu", permissions=["orders"]))

    body = _detail(client, inbox).content.decode()
    assert f'action="{_resend_url(inbox)}"' in body and "送り直す" in body
    assert UNIT_PRICE not in body

    response = client.post(_resend_url(inbox), follow=True)

    assert len(fake.calls) == 1
    assert ("success", "注文回答を送りました。") in _messages(response)
    assert Inbox.objects.get(pk=inbox.pk).reply_sent_at is not None
    after = response.content.decode()
    assert UNIT_PRICE not in after  # 知らせと戻った詳細に単価が出ない
    assert f'action="{_resend_url(inbox)}"' not in after  # 届いたので「送り直す」は消える


# --- (b) 同じ人に、受け入れるの入口は出ない (金額に関わる操作のまま) ---


@pytest.mark.django_db
def test_clerk_without_money_still_cannot_accept(judged, monkeypatch, client, user, subcontractor, customer, item, own_item, connections):  # noqa: F811
    _po, inbox = _received(monkeypatch, subcontractor, item, user)
    client.force_login(account("jimu", permissions=["orders"]))
    accept_url = reverse("connect:inbox_accept", args=[inbox.pk])

    body = _detail(client, inbox).content.decode()
    assert accept_url not in body
    assert reverse("connect:inbox_accept_conditional", args=[inbox.pk]) not in body

    assert client.get(accept_url).status_code == 403  # 開いた時点で断る
    client.post(accept_url, {"item_1": own_item.pk})
    assert Inbox.objects.get(pk=inbox.pk).status == Inbox.STATUS_RECEIVED  # 直に送っても受け入れられない


# --- (c) 受注を編集できない人 ---


@pytest.mark.django_db
def test_viewer_cannot_resend(judged, monkeypatch, client, user, subcontractor, customer, item, own_item, connections):  # noqa: F811
    _po, inbox = _unreachable_reply(monkeypatch, subcontractor, item, own_item, user)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    client.force_login(account("miru"))

    assert f'action="{_resend_url(inbox)}"' not in _detail(client, inbox).content.decode()

    response = client.post(_resend_url(inbox), follow=True)

    assert ("error", access.write_message("orders")) in _messages(response)
    assert fake.calls == []
    assert Inbox.objects.get(pk=inbox.pk).reply_sent_at is None


# --- (d) 金額の閲覧のある人は今までどおり ---


@pytest.mark.django_db
def test_clerk_with_money_can_resend_as_before(judged, monkeypatch, client, user, subcontractor, customer, item, own_item, connections):  # noqa: F811
    _po, inbox = _unreachable_reply(monkeypatch, subcontractor, item, own_item, user)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    client.force_login(account("keiri", permissions=["orders"], see_money=True))

    body = _detail(client, inbox).content.decode()
    assert f'action="{_resend_url(inbox)}"' in body
    assert UNIT_PRICE in body  # 金額の閲覧のある人には単価が出る (上の (a) の「出ない」が空振りでない事)

    response = client.post(_resend_url(inbox), follow=True)

    assert len(fake.calls) == 1
    assert ("success", "注文回答を送りました。") in _messages(response)


# --- (e) 今日やることの行 ---


@pytest.mark.django_db
def test_today_row_follows_the_resend_permission(judged, monkeypatch, user, subcontractor, customer, item, own_item, connections):  # noqa: F811
    _unreachable_reply(monkeypatch, subcontractor, item, own_item, user)
    account("jimu", permissions=["orders"])
    account("miru")
    account("keiri", permissions=["orders"], see_money=True)

    assert "reply_undelivered" in _keys("jimu")  # 金額の閲覧が無くても、送り直せる人には出る
    assert "reply_undelivered" not in _keys("miru")  # 送り直せない人には出ない
    assert "reply_undelivered" in _keys("keiri")


# --- (f) 権限の表 ---


def test_send_reply_is_an_orders_action_without_money():
    name = access.action_name(services.send_reply)

    assert name == "connect.send_reply" == reply.ACTION
    assert access.ACTION_KINDS[name] == "orders"  # 受注の組 (受け入れると同じ種類の権限)
    assert name not in access.MONEY_ACTIONS  # 金額に関わる操作ではない
    assert access.action_name(services.accept) in access.MONEY_ACTIONS  # 受け入れるは金額に関わる操作のまま
