"""受信した注文の詳細の注文回答の「送り直す」の口 (connect/reply.py の resend_reply。精査 A1 の想定外 S8)。

承認か断りの後に注文回答が届かなかった受信した注文は、詳細の注文回答の枡に「送り直す」が出る。押すと services.send_reply を
呼び、受信した注文の詳細へ戻る。権限は承認と同じ connect.accept。
1 つの DB に 2 社の役を置く形と、偽の requests.post は test_connect_reply.py と同じ (外へは HTTP を送らない)。
鍵はテストのたびに作り、環境変数に置く。取引先・品目・会社は作り物。
"""

import pytest
import requests
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services
from seizou.connect.models import Inbox
from seizou.core import access
from seizou.masters import services as masters_services
from seizou.purchasing.models import PurchaseOrder
from tests.test_connect_reply import ORDER_ENV, REPLY_ENV, _connect, _into_receive, _send_order
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret
from tests.test_core_access import account


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


def _received(monkeypatch, subcontractor, item, user):
    """注文を送り、相手の役の受信した注文 (状態「届いた」) にするまで。"""
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_order(subcontractor, item, user)
    return po, Inbox.objects.get()


def _unreachable_reply(monkeypatch, subcontractor, item, own_item, user):
    """承認したが、注文回答が相手に届かなかった受信した注文。"""
    po, inbox = _received(monkeypatch, subcontractor, item, user)
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError()))
    services.accept(inbox, {"T-100": own_item}, user)
    inbox.refresh_from_db()
    assert inbox.reply_sent_at is None
    return po, inbox


def _messages(response):
    return [(message.level_tag, str(message)) for message in response.context["messages"]]


@pytest.mark.django_db
def test_resend_after_unreachable_reply(
    monkeypatch, client, user, subcontractor, customer, item, own_item, connections
):
    po, inbox = _unreachable_reply(monkeypatch, subcontractor, item, own_item, user)
    first_id = inbox.reply_message_id
    detail_url = reverse("connect:inbox_detail", args=[inbox.pk])
    resend_url = reverse("connect:inbox_reply_resend", args=[inbox.pk])
    client.force_login(user)
    assert resend_url in client.get(detail_url).content.decode()
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)

    response = client.post(resend_url, follow=True)

    assert response.redirect_chain == [(detail_url, 302)]
    assert ("success", "注文回答を送りました。") in _messages(response)
    assert len(fake.calls) == 1
    inbox.refresh_from_db()
    assert inbox.reply_sent_at is not None
    assert inbox.reply_message_id == first_id
    assert resend_url not in response.content.decode()
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ACCEPTED


@pytest.mark.django_db
def test_resend_before_decision_is_refused(monkeypatch, client, user, subcontractor, customer, item, connections):
    _po, inbox = _received(monkeypatch, subcontractor, item, user)
    resend_url = reverse("connect:inbox_reply_resend", args=[inbox.pk])
    client.force_login(user)
    assert resend_url not in client.get(reverse("connect:inbox_detail", args=[inbox.pk])).content.decode()
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    response = client.post(resend_url, follow=True)

    assert ("error", services.MESSAGE_REPLY_BEFORE_DECISION) in _messages(response)
    assert fake.calls == []
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_RECEIVED


@pytest.mark.django_db
def test_resend_needs_post_and_login(
    monkeypatch, client, user, subcontractor, customer, item, own_item, connections
):
    _po, inbox = _unreachable_reply(monkeypatch, subcontractor, item, own_item, user)
    resend_url = reverse("connect:inbox_reply_resend", args=[inbox.pk])
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    anonymous = client.post(resend_url)

    assert anonymous.status_code == 302
    assert reverse("core:login") in anonymous["Location"]

    client.force_login(user)

    assert client.get(resend_url).status_code == 405
    assert fake.calls == []
    inbox.refresh_from_db()
    assert inbox.reply_sent_at is None


@pytest.mark.django_db
def test_resend_follows_write_permission(
    monkeypatch, settings, client, user, subcontractor, customer, item, own_item, connections
):
    settings.SEIZOU = {"access_control": {"write": "on"}}
    _po, inbox = _unreachable_reply(monkeypatch, subcontractor, item, own_item, user)
    resend_url = reverse("connect:inbox_reply_resend", args=[inbox.pk])
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    # 受注の権限の無いアカウントは断られる (送らない)
    client.force_login(account("plain-clerk"))
    response = client.post(resend_url, follow=True)

    assert ("error", access.write_message("orders")) in _messages(response)
    assert fake.calls == []

    # システム管理者は通る
    client.force_login(get_user_model().objects.create_superuser("admin", password="pass-for-tests-only"))
    response = client.post(resend_url, follow=True)

    assert len(fake.calls) == 1
    assert ("success", "注文回答を送りました。") in _messages(response)
