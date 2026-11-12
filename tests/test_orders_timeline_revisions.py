"""受注の詳細の「流れ」の枠に、得意先から届いた変更と取消の版と、その版へのこちらの答えが出る事を見る (依頼書 N440。本体の決め 11-12 23:3x)。

受注の元の受信した注文の後の版 (逆向きの関係 revisions) ごとに「第 n 版の変更が届いた」(取消は「取消が届いた」) を出し、
答えた版には「…を受け入れた」「…を保留した」「…を断った (理由: …)」を出す (字は発注の側の流れ purchasing.timeline と向かい合う形)。
元の行の 2 つの題 (「注文が届いた」「受信した注文を受け入れ」) と、ほかの出来事の題と並びは変えない。

2 社の役 (A = 協力企業 S001 へ発注する・B = 得意先 C001 から受注する) と、受信した注文と後の版の作り方は、既にある試験の道具と fixture を
借りる (tests/test_connect_revision_accept.py。借りるファイルは変えない)。相手への HTTP は借りた fixture が requests.post を偽の物に差し替える。
答えの日時の無い版 (注文回答が相手に届いていない保留と断り) と、明細の数の違う 2 つの受注は、受信した注文を record_inbox で入れ、
後の版の行を表に直に作る (つながりが無いので HTTP を呼ばない。呼んだら落とす)。取引先・品番・人は作り物だけ。
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.core import edi_codes
from seizou.notes import services as notes_services
from seizou.orders import services as orders_services
from seizou.orders.timeline import events_for
from tests import test_connect_revision_accept as revision_accept
from tests.test_connect_revision_accept import _accept_first, _cancel, _mapping, _revise, _send, _send_first
from tests.test_r10_inbox_accept import _line, _payload

# 版の受け入れの試験の fixture を借りる (pytest は module の名前で fixture を見つける。tests/test_orders_revision_notice.py と同じ形)。
user = revision_accept.user
client_logged_in = revision_accept.client_logged_in
order_secret = revision_accept.order_secret
reply_secret = revision_accept.reply_secret
subcontractor = revision_accept.subcontractor
customer = revision_accept.customer
items = revision_accept.items
connections = revision_accept.connections
fake = revision_accept.fake

#: 元の行の 2 つの題 (直す前と同じ字)
BASE_TITLES = ["注文が届いた", "受信した注文を受け入れ"]


@pytest.fixture
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


def _waiting(subcontractor, items, user, *, purpose=edi_codes.PURPOSE_CHANGE):
    """A の第 1 版を B が受け入れて受注にし、A が第 2 版 (変更か取消) を送って B の受信した注文に未処理で届いた所。返す物は (受注, 第 2 版の行)。"""
    po = _send_first(subcontractor, items, user)
    base, order = _accept_first(items, user)
    _send(_cancel(po) if purpose == edi_codes.PURPOSE_CANCEL else _revise(po, items), user)
    revision = base.revisions.get()
    assert (revision.revision_no, revision.purpose_code, revision.status) == (2, purpose, Inbox.STATUS_RECEIVED)
    return order, revision


def _inbox_titles(order):
    """流れの受信した注文の組 (種類 inbox) の出来事の題の並び。"""
    return [event.title for event in events_for(order) if event.kind == "inbox"]


def _event(order, title):
    found = [event for event in events_for(order) if event.title == title]
    assert len(found) == 1, (title, [event.title for event in events_for(order)])
    return found[0]


def _inbox_url(row):
    return reverse("connect:inbox_detail", args=[row.pk])


def _flow_section(body):
    start = body.index('<section id="order-flow"')
    return body[start : body.index("</section>", start)]


# ---------------------------------------------------------------- 変更の版: 届いた・受け入れた・保留した・断った


@pytest.mark.django_db
def test_change_received_shows_arrival_only(fake, user, subcontractor, items, client_logged_in):
    order, revision = _waiting(subcontractor, items, user)

    assert _inbox_titles(order) == [*BASE_TITLES, "第 2 版の変更が届いた"]
    arrived = _event(order, "第 2 版の変更が届いた")
    assert (arrived.kind, arrived.when, arrived.url) == ("inbox", revision.received_at, _inbox_url(revision))
    assert (arrived.line_no, arrived.amount) == (None, None)
    # 受注の詳細の流れの枠に、その版の受信した注文の詳細への link で出る
    section = _flow_section(client_logged_in.get(reverse("orders:order_detail", args=[order.pk])).content.decode())
    assert f'<a class="link link-primary" href="{_inbox_url(revision)}">第 2 版の変更が届いた</a>' in section


@pytest.mark.django_db
def test_accepted_change_comes_after_arrival(fake, user, subcontractor, items):
    order, revision = _waiting(subcontractor, items, user)

    connect_services.accept(revision, _mapping(items), user)

    revision.refresh_from_db()
    assert _inbox_titles(order) == [*BASE_TITLES, "第 2 版の変更が届いた", "第 2 版の変更を受け入れた"]
    accepted = _event(order, "第 2 版の変更を受け入れた")
    assert (accepted.kind, accepted.when, accepted.url) == ("inbox", revision.accepted_at, _inbox_url(revision))


@pytest.mark.django_db
def test_held_change(fake, user, subcontractor, items):
    order, revision = _waiting(subcontractor, items, user)

    connect_services.hold(revision, user)

    revision.refresh_from_db()
    assert revision.reply_sent_at is not None
    assert _inbox_titles(order) == [*BASE_TITLES, "第 2 版の変更が届いた", "第 2 版の変更を保留した"]
    held = _event(order, "第 2 版の変更を保留した")
    assert (held.kind, held.when, held.url) == ("inbox", revision.reply_sent_at, _inbox_url(revision))


@pytest.mark.django_db
def test_rejected_change_with_reason(fake, user, subcontractor, items):
    order, revision = _waiting(subcontractor, items, user)

    connect_services.reject(revision, "作り物の断り", user)

    revision.refresh_from_db()
    title = "第 2 版の変更を断った (理由: 作り物の断り)"
    assert _inbox_titles(order) == [*BASE_TITLES, "第 2 版の変更が届いた", title]
    rejected = _event(order, title)
    assert (rejected.kind, rejected.when, rejected.url) == ("inbox", revision.reply_sent_at, _inbox_url(revision))


@pytest.mark.django_db
def test_rejected_change_without_reason_has_no_brackets(fake, user, subcontractor, items):
    order, revision = _waiting(subcontractor, items, user)
    connect_services.reject(revision, "作り物の断り", user)
    # 断る処理は理由を要るので、理由の空の行 (前からある行への備え) は表に直に作る
    Inbox.objects.filter(pk=revision.pk).update(reason="")

    titles = _inbox_titles(order)

    assert titles == [*BASE_TITLES, "第 2 版の変更が届いた", "第 2 版の変更を断った"]
    assert not any("理由" in title for title in titles)


@pytest.mark.django_db
@pytest.mark.parametrize("answer", ["accept", "reject"])
def test_held_then_answered_shows_the_current_answer_only(fake, user, subcontractor, items, answer):
    """保留の後に受け入れるか断ると、行の答えのタイプと送った日時は今の答えに書き直される (connect.services._restart_reply) ので、今の答えだけを出す。"""
    order, revision = _waiting(subcontractor, items, user)
    connect_services.hold(revision, user)

    if answer == "accept":
        connect_services.accept(Inbox.objects.get(pk=revision.pk), _mapping(items), user)
        last = "第 2 版の変更を受け入れた"
    else:
        connect_services.reject(Inbox.objects.get(pk=revision.pk), "作り物の断り", user)
        last = "第 2 版の変更を断った (理由: 作り物の断り)"

    assert _inbox_titles(order) == [*BASE_TITLES, "第 2 版の変更が届いた", last]


# ---------------------------------------------------------------- 取消の版


@pytest.mark.django_db
def test_cancel_revision_received_then_accepted(fake, user, subcontractor, items):
    order, revision = _waiting(subcontractor, items, user, purpose=edi_codes.PURPOSE_CANCEL)
    assert _inbox_titles(order) == [*BASE_TITLES, "第 2 版の取消が届いた"]

    connect_services.accept(revision, {}, user)

    order.refresh_from_db()
    revision.refresh_from_db()
    assert order.status == order.STATUS_CANCELLED
    assert _inbox_titles(order) == [*BASE_TITLES, "第 2 版の取消が届いた", "第 2 版の取消を受け入れた"]
    assert _event(order, "第 2 版の取消を受け入れた").when == revision.accepted_at
    # 今ある受注の取消の出来事は、今のまま日時なしで最後に出る
    events = events_for(order)
    assert [(e.kind, e.title, e.when) for e in events[-1:]] == [("cancel", "受注を取消", None)]


# ---------------------------------------------------------------- 版が 2 つ


@pytest.mark.django_db
def test_two_revisions_line_up_in_revision_order(fake, user, subcontractor, items):
    po = _send_first(subcontractor, items, user)
    base, order = _accept_first(items, user)
    _send(_revise(po, items), user)
    connect_services.reject(Inbox.objects.get(revises=base, revision_no=2), "作り物の断り", user)
    _send(_revise(po, items, revision_no=3), user)
    third = Inbox.objects.get(revises=base, revision_no=3)
    connect_services.accept(third, _mapping(items), user)

    assert _inbox_titles(order) == [
        *BASE_TITLES,
        "第 2 版の変更が届いた",
        "第 2 版の変更を断った (理由: 作り物の断り)",
        "第 3 版の変更が届いた",
        "第 3 版の変更を受け入れた",
    ]
    assert _event(order, "第 3 版の変更が届いた").url == _inbox_url(third)


# ---------------------------------------------------------------- 答えの日時の無い版 (注文回答が相手に届いていない)


def _from_inbox(customer, item, user, *, lines=1, received_at=None):
    """受信した注文 (明細 lines 行。届いた日時は received_at、無ければ 1 時間前) を受け入れた受注。注文回答は送らない。返す物は (元の行, 受注)。"""
    payload = _payload(str(uuid.uuid4()), [_line(n, "A-100") for n in range(1, lines + 1)])
    payload["order_date"] = timezone.localdate().isoformat()
    received_at = received_at or timezone.now() - timedelta(hours=1)
    base = connect_services.record_inbox(customer, payload, user, received_at=received_at)
    order = connect_services.accept(base, {"A-100": item}, user, send=False)
    return Inbox.objects.get(pk=base.pk), order


def _later(base, revision_no, *, purpose=edi_codes.PURPOSE_CHANGE):
    """元の行を指す後の版の行を表に直に作る (受け口を通さない。中身は使わない)。"""
    return Inbox.objects.create(
        message_id=uuid.uuid4(),
        partner=base.partner,
        kind=Inbox.KIND_ORDER,
        payload={},
        revision_no=revision_no,
        purpose_code=purpose,
        revises=base,
    )


@pytest.mark.django_db
def test_undated_answers_come_right_after_their_arrival(no_http, user, customer, items):
    """注文回答が相手に届いていない保留と断りは、答えの日時を持つ列が空なので日時なしにし、その版の「届いた」の直後に並べる。"""
    _buyer, own = items
    base, order = _from_inbox(customer, own[0], user)
    second = _later(base, 2)
    # つながりが無いので、断りの注文回答は相手に届かない (送った日時は空)
    connect_services.reject(second, "作り物の断り", user)
    third = _later(base, 3, purpose=edi_codes.PURPOSE_CANCEL)
    connect_services.hold(third, user, send=False)
    notes_services.save_note(order, "<p>作り物の段取りの注意</p>", user)
    second.refresh_from_db()
    third.refresh_from_db()
    assert (second.reply_sent_at, third.reply_sent_at) == (None, None)

    events = events_for(order)

    assert [(e.kind, e.title) for e in events] == [
        ("inbox", "注文が届いた"),
        ("inbox", "受信した注文を受け入れ"),
        ("inbox", "第 2 版の変更が届いた"),
        ("inbox", "第 2 版の変更を断った (理由: 作り物の断り)"),
        ("inbox", "第 3 版の取消が届いた"),
        ("inbox", "第 3 版の取消を保留した"),
        ("order", "受注日 (相手の発注日)"),
        ("note", "メモを更新"),
    ]
    assert [e.when for e in events[2:6]] == [second.received_at, None, third.received_at, None]
    assert [e.url for e in events[2:6]] == [_inbox_url(second)] * 2 + [_inbox_url(third)] * 2


@pytest.mark.django_db
def test_accepted_revision_without_accepted_at_comes_right_after_arrival(no_http, user, customer, items):
    """承認した日時の列より前に受け入れた版の行 (accepted_at が空) も、日時なしで「届いた」の直後に並べる。"""
    _buyer, own = items
    base, order = _from_inbox(customer, own[0], user)
    second = _later(base, 2)
    Inbox.objects.filter(pk=second.pk).update(status=Inbox.STATUS_ACCEPTED)
    notes_services.save_note(order, "<p>作り物の段取りの注意</p>", user)

    titles = [(e.title, e.when) for e in events_for(order)]

    assert titles[2:4] == [("第 2 版の変更が届いた", second.received_at), ("第 2 版の変更を受け入れた", None)]
    assert titles[-1][0] == "メモを更新"


# ---------------------------------------------------------------- 直す前と同じ受注


@pytest.mark.django_db
def test_orders_without_revisions_keep_their_events(no_http, user, customer, items):
    _buyer, own = items
    by_hand = orders_services.create_order(
        customer, [{"item": own[0], "qty": Decimal("5"), "unit_price": Decimal("100")}], user, order_date=timezone.localdate()
    )
    _base, from_inbox = _from_inbox(customer, own[0], user)

    assert [(e.kind, e.title) for e in events_for(by_hand)] == [("order", "受注を登録")]
    assert [(e.kind, e.title) for e in events_for(from_inbox)] == [
        ("inbox", "注文が届いた"),
        ("inbox", "受信した注文を受け入れ"),
        ("order", "受注日 (相手の発注日)"),
    ]


# ---------------------------------------------------------------- 問い合わせの数


def _detail_queries(client, order) -> tuple[int, str]:
    url = reverse("orders:order_detail", args=[order.pk])
    assert client.get(url).status_code == 200  # 最初の 1 回の控え (session など) を外す
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    assert response.status_code == 200
    return len(queries.captured_queries), _flow_section(response.content.decode())


@pytest.mark.django_db
def test_detail_query_count_with_revisions_does_not_grow_with_lines(no_http, user, customer, items, client_logged_in):
    _buyer, own = items
    counts = []
    for lines in (1, 5):
        base, order = _from_inbox(customer, own[0], user, lines=lines)
        connect_services.hold(_later(base, 2), user, send=False)
        connect_services.reject(_later(base, 3), "作り物の断り", user)
        count, section = _detail_queries(client_logged_in, order)
        assert "第 2 版の変更を保留した" in section and "第 3 版の変更を断った (理由: 作り物の断り)" in section
        counts.append(count)

    assert counts[0] == counts[1], counts
