"""注文回答が相手に届かなかったときの、受け入れ・保留・断るの後の黄の知らせ (connect.services._with_reply_notice) のテスト。

意図: 受信した注文に答えた後、注文回答が届かなかった事を、押した直後の知らせで分かるようにするため (本体の決め 11-11 14:2x。精査 Q3 の T8)。
赤にせず、相手に届いていない発注と同じ黄の知らせ (閉じるまで残る) にする。受け入れ・保留・断るそのものは成立する。

2 社の役とつながりと受信した注文の作り方は tests/test_connect_reply.py の helper と fixture を使う (1 つの DB に 2 社の役を置く)。
注文は偽の requests.post で相手の受け口に回して受信した注文にし、答える前に requests.post を ConnectionError を投げる物に差し替える。
鍵は tests/test_connect_signing.py の _new_secret で試験のたびに作って環境変数に置き (fixture の order_secret と reply_secret)、
鍵の値を assert の失敗の文に出さない。取引先・品番・会社は作り物。helper と fixture を借りたファイルは変えない。
"""

import pytest
import requests
from django.urls import reverse

from seizou.connect import services
from seizou.connect.models import Inbox
from tests.test_connect_inbox_intake import intake_row
from tests.test_connect_reply import (  # noqa: F401 (fixture)
    _connect,
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
from tests.test_connect_revision_accept import _accept_first, _cancel, _send, _send_first, items  # noqa: F401 (fixture)
from tests.test_connect_send import FakePost

UNDELIVERED_HEAD = "注文回答は相手に届いていません。"


@pytest.fixture
def received(monkeypatch, user, subcontractor, item, connections):  # noqa: F811
    """A 社の役が送った発注を、B 社の役の受信した注文 (入口 = 直接) にして返す。"""
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    _send_order(subcontractor, item, user)
    inbox = Inbox.objects.get()
    assert inbox.source == Inbox.SOURCE_DIRECT
    return inbox


@pytest.fixture
def unreachable(monkeypatch):
    """相手に届かない requests.post (つながらない)。"""
    fake = FakePost(raises=requests.ConnectionError("作り物のつながらない"))
    monkeypatch.setattr("requests.post", fake)
    return fake


@pytest.fixture
def reachable(monkeypatch):
    """相手が受け取る requests.post (FakePost の既定の 200)。"""
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    return fake


def _messages(response):
    return [(message.level_tag, str(message)) for message in response.context["messages"]]


def _is_undelivered(result, inbox):
    inbox.refresh_from_db()
    note = getattr(result, "saved_note", "")
    return (
        getattr(result, "saved_level", "") == "warning"
        and note.startswith(UNDELIVERED_HEAD)
        and inbox.reply_response in note
        and "「送り直す」" in note
    )


# ---------------------------------------------------------------- (i) 届かない


def test_accept_undelivered_is_warning(received, unreachable, user, own_item):  # noqa: F811
    order = services.accept(received, {"T-100": own_item}, user)

    assert len(unreachable.calls) == 1
    assert _is_undelivered(order, received)
    received.refresh_from_db()
    assert received.status == Inbox.STATUS_ACCEPTED
    assert received.reply_sent_at is None
    # 前からある saved_note (受注を作った知らせの 1 文) は後ろに残る
    assert order.saved_note.endswith("明細 1 行を手入力せずに受注にしました。")


def test_accept_with_conditions_undelivered_is_warning(received, unreachable, user, own_item):  # noqa: F811
    from decimal import Decimal

    order = services.accept_with_conditions(
        received, {"T-100": own_item}, {1: {"unit_price": Decimal("30"), "qty": Decimal("10"), "due_date": None}}, user
    )

    assert _is_undelivered(order, received)
    # 届いていないので「相手に返しました」と書かない字にする (後ろの黄の知らせ「注文回答は相手に届いていません。」と食い違わせない)
    assert order.saved_message == services.MESSAGE_CONDITIONAL_SAVED_UNDELIVERED
    assert "返しました" not in order.saved_message


def test_accept_with_conditions_unchanged_undelivered_does_not_say_replied(received, unreachable, user, own_item):  # noqa: F811
    """どの値も直さずに「単価を直して受け入れる」を通した時も、届いていなければ「相手に返しました」と書かない。"""
    order = services.accept_with_conditions(received, {"T-100": own_item}, {}, user)

    assert _is_undelivered(order, received)
    assert order.saved_message == services.MESSAGE_CONDITIONAL_UNCHANGED_UNDELIVERED
    assert "返しました" not in order.saved_message


def test_hold_undelivered_is_warning(received, unreachable, user):  # noqa: F811
    saved = services.hold(received, user)

    assert isinstance(saved, Inbox)
    assert _is_undelivered(saved, received)
    assert saved.saved_message == services.MESSAGE_HOLD_SAVED


def test_reject_undelivered_is_warning(received, unreachable, user):  # noqa: F811
    saved = services.reject(received, "作り物の断る理由", user)

    assert isinstance(saved, Inbox)
    assert _is_undelivered(saved, received)
    received.refresh_from_db()
    assert received.status == Inbox.STATUS_REJECTED


def test_accept_without_send_connection_is_warning(
    monkeypatch, user, subcontractor, customer, item, own_item, order_secret, reply_secret  # noqa: F811
):
    """送る向きのつながりが無いときも、注文回答は届いていないので黄の知らせ (返事は MESSAGE_REPLY_NO_CONNECTION)。"""
    _connect(user, subcontractor, customer, reply_back=False)
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    _send_order(subcontractor, item, user)
    inbox = Inbox.objects.get()

    order = services.accept(inbox, {"T-100": own_item}, user)

    assert _is_undelivered(order, inbox)
    assert services.MESSAGE_REPLY_NO_CONNECTION in order.saved_note


def test_accept_revision_undelivered_is_warning(monkeypatch, user, subcontractor, items, connections):  # noqa: F811
    """変更と取消の版 (_accept_revision) を受け入れた後も、注文回答が届かなければ返す受注に黄の知らせ。"""
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_first(subcontractor, items, user)
    base, _order = _accept_first(items, user)
    _send(_cancel(po), user)
    revision = Inbox.objects.get(revises=base)
    fake = FakePost(raises=requests.ConnectionError("作り物のつながらない"))
    monkeypatch.setattr("requests.post", fake)

    order = services.accept(revision, {}, user)

    assert len(fake.calls) == 1
    assert order.status == order.STATUS_CANCELLED
    assert _is_undelivered(order, revision)


# ---------------------------------------------------------------- (j) 届く・送らない・読み取りの行


def test_accept_delivered_has_no_notice(received, reachable, user, own_item):  # noqa: F811
    order = services.accept(received, {"T-100": own_item}, user)

    received.refresh_from_db()
    assert received.reply_sent_at is not None
    assert getattr(order, "saved_level", "") == ""
    assert UNDELIVERED_HEAD not in getattr(order, "saved_note", "")


def test_hold_and_reject_delivered_have_no_notice(received, reachable, user):  # noqa: F811
    saved = services.hold(received, user)
    assert getattr(saved, "saved_level", "") == ""
    assert saved.saved_message == services.MESSAGE_HOLD_SAVED

    saved = services.reject(received, "作り物の断る理由", user)
    assert getattr(saved, "saved_level", "") == ""
    assert UNDELIVERED_HEAD not in getattr(saved, "saved_note", "")


def test_send_false_has_no_notice(received, unreachable, user, own_item):  # noqa: F811
    order = services.accept(received, {"T-100": own_item}, user, send=False)

    assert unreachable.calls == []
    received.refresh_from_db()
    assert received.reply_response == services.MESSAGE_REPLY_SEED
    assert getattr(order, "saved_level", "") == ""


def test_hold_send_false_has_no_notice(received, unreachable, user):  # noqa: F811
    saved = services.hold(received, user, send=False)

    assert unreachable.calls == []
    assert getattr(saved, "saved_level", "") == ""
    assert saved.saved_message == services.MESSAGE_HOLD_SAVED


def test_intake_row_has_no_notice(unreachable, user, customer):  # noqa: F811
    intake = intake_row(customer, user)

    saved = services.reject(intake, "作り物の断る理由", user)

    assert unreachable.calls == []
    intake.refresh_from_db()
    assert intake.reply_response == services.MESSAGE_REPLY_INTAKE
    assert getattr(saved, "saved_level", "") == ""


def test_reject_screen_shows_yellow_notice(client, received, unreachable, user):  # noqa: F811
    client.force_login(user)

    response = client.post(reverse("connect:inbox_reject", args=[received.pk]), {"reason": "作り物の断る理由"}, follow=True)

    received.refresh_from_db()
    assert received.status == Inbox.STATUS_REJECTED
    messages = _messages(response)
    assert len(messages) == 1
    level, text = messages[0]
    assert level == "warning"
    assert text.startswith("保存しました。" + UNDELIVERED_HEAD)
    assert received.reply_response in text
    assert UNDELIVERED_HEAD in response.content.decode()


def test_reject_screen_delivered_is_green(client, received, reachable, user):  # noqa: F811
    client.force_login(user)

    response = client.post(reverse("connect:inbox_reject", args=[received.pk]), {"reason": "作り物の断る理由"}, follow=True)

    assert [level for level, _text in _messages(response)] == ["success"]
