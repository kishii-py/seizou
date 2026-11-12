"""発注の流れの枠に、変更と取消の版を送った事と、その版への相手の答えを出す (N435。本体の決め 11-12 13:4x)。

字は発注の詳細の「送った記録」のタブの行 (connect の Outbox.__str__ の「第 n 版の変更」「相手が受け入れた」「相手が断った」) とそろえる。
版 1 の題 (「注文を送った (相手が受け取った)」と、発注の列の注文回答の題) は今のまま。
取引先と品番は作り物だけを使う。発注と第 2 版は tests/test_purchasing_settled_revision.py の helper で作る
(送る 10:00 → 条件付で受けた 10:05 → 変更の第 2 版を送る 10:10 → 第 2 版への答え 10:20)。
相手への HTTP は偽の requests.post (tests.test_connect_send の FakePost) に差し替える。
版 2 からの答えは、connect.services._record_revision_reply と同じく、その版の最初の送った注文の行 (pk の一番小さい行) に update で書く。
"""

from decimal import Decimal

import pytest
import requests
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.connect.models import Outbox
from seizou.core import edi_codes
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder
from seizou.purchasing.timeline import events_for, lines_for
from tests.test_connect_send import FakePost
from tests.test_purchasing_settled_revision import (  # noqa: F401 (fixture)
    _at,
    _clock,
    _conditional,
    _second_revision,
    item,
    send_connection,
    subcontractor,
)

#: 版 1 の 3 つの出来事 (_conditional の発注。題は今のまま)
FIRST_REVISION = [
    ("order", "2026-11-10", "発注を登録"),
    ("sent", "10:00", "注文を送った (相手が受け取った)"),
    ("reply", "10:05", "相手が条件付で受けた"),
]


def _flow(po):
    return [(e.kind, _clock(e), e.title) for e in events_for(PurchaseOrder.objects.get(pk=po.pk))]


def _reply_titles(po):
    return [e.title for e in events_for(PurchaseOrder.objects.get(pk=po.pk)) if e.kind == "reply"]


def _answer(po, revision_no, reply_type, reply_note="", replied_at=None):
    """その版の最初の送った注文の行 (pk の一番小さい行) に、相手の答えを書く (connect.services._record_revision_reply と同じ行)。"""
    first = Outbox.objects.filter(purchase_order=po, revision_no=revision_no).order_by("pk").first()
    Outbox.objects.filter(pk=first.pk).update(
        reply_type=reply_type, reply_note=reply_note, replied_at=replied_at or _at(10, 20)
    )


def _fail_sending(monkeypatch):
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))


def _change(po, user, unit_price="70"):
    """発注の明細の単価を直して、変更の版を相手へ送る (services.change_purchase_order)。"""
    po = PurchaseOrder.objects.get(pk=po.pk)
    return services.change_purchase_order(po, {}, [{"line": po.lines.get(), "unit_price": Decimal(unit_price)}], user)


# --- (a) 第 2 版の変更を送り、相手が受け入れた ---


@pytest.mark.django_db
def test_accepted_change_shows_sent_revision_and_answer(client_logged_in, user, subcontractor, item, send_connection):  # noqa: F811
    po = _second_revision(user, _conditional(user, subcontractor, item), reply_type=edi_codes.REPLY_ACCEPTED)

    assert _flow(po) == FIRST_REVISION + [
        ("sent", "10:10", "第 2 版の変更を送った (相手が受け取った)"),
        ("reply", "10:20", "相手が第 2 版の変更を受け入れた"),
    ]
    # 版への答えの絵記号は、版 1 の答えと同じ (種類が同じ reply)
    replies = [e for e in events_for(PurchaseOrder.objects.get(pk=po.pk)) if e.kind == "reply"]
    assert [e.icon for e in replies] == ["inbox", "inbox"]
    body = client_logged_in.get(reverse("purchasing:purchaseorder_detail", args=[po.pk])).content.decode()
    section = body[body.index('<section id="po-flow"') :]
    section = section[: section.index("</section>")]
    assert "第 2 版の変更を送った (相手が受け取った)" in section
    assert "相手が第 2 版の変更を受け入れた" in section


# --- (b) 第 2 版の変更を相手が断った (理由つきと、理由が空) ---


@pytest.mark.django_db
def test_refused_change_shows_reason_only_when_given(user, subcontractor, item, send_connection):  # noqa: F811
    with_reason = _second_revision(
        user, _conditional(user, subcontractor, item), reply_type=edi_codes.REPLY_REJECTED, reply_note="作り物の断り"
    )
    without_reason = _second_revision(
        user, _conditional(user, subcontractor, item), reply_type=edi_codes.REPLY_REJECTED
    )

    assert _reply_titles(with_reason) == ["相手が条件付で受けた", "相手が第 2 版の変更を断った (理由: 作り物の断り)"]
    assert _reply_titles(without_reason) == ["相手が条件付で受けた", "相手が第 2 版の変更を断った"]


# --- (b2) 条件付と保留の答え (511 回目。流れの枠・送った記録の行・詳細の頭の札の 3 か所で同じ言葉) ---


@pytest.mark.django_db
@pytest.mark.parametrize("reply_type, words", [
    (edi_codes.REPLY_CONDITIONAL, "条件付で受けた"),
    (edi_codes.REPLY_PENDING, "保留した"),
])
def test_conditional_and_pending_answers_have_their_own_words(client_logged_in, user, subcontractor, item, send_connection, reply_type, words):  # noqa: F811
    """相手が第 2 版に条件付 (CA) か保留 (AJ) で答えた時、「答えた」「答え待ち」でなく、第 1 版の答えと同じ言葉で出す。

    訳: 相手の seizou は、変更の版にも「条件を直して受け入れる」と「保留する」で答えられる (connect の受け口は版 2 からの答えの種類をそのまま書く)。
    直す前は、流れの枠が「相手が第 2 版の変更に答えた」、詳細の頭の札が「相手の答え待ち」、送った記録の行は答えの字なしで、答えの中身が読めなかった。
    """
    po = _second_revision(user, _conditional(user, subcontractor, item), reply_type=reply_type)

    # 流れの枠
    assert _reply_titles(po) == ["相手が条件付で受けた", f"相手が第 2 版の変更を{words}"]
    # 送った記録のタブの行 (connect の Outbox の字)
    row = Outbox.objects.filter(purchase_order=po, revision_no=2).order_by("pk").first()
    assert str(row).startswith("第 2 版の変更 ") and str(row).endswith(f"・相手が{words}")
    # 詳細の頭の札
    body = client_logged_in.get(reverse("purchasing:purchaseorder_detail", args=[po.pk])).content.decode()
    assert f"第 2 版 (変更)・相手が{words}" in body
    assert "相手の答え待ち" not in body


@pytest.mark.django_db
def test_unknown_answer_code_keeps_the_plain_words(user, subcontractor, item, send_connection):  # noqa: F811
    """4 つの種類の外の値 (今の受け口は書かない) は、流れの枠だけ「相手が第 2 版の変更に答えた」と出す (落とさない)。"""
    po = _second_revision(user, _conditional(user, subcontractor, item), reply_type="ZZ")

    assert _reply_titles(po) == ["相手が条件付で受けた", "相手が第 2 版の変更に答えた"]


# --- (c) 取消の版 ---


@pytest.mark.django_db
def test_cancel_revision_shows_cancel_words(user, subcontractor, item, send_connection):  # noqa: F811
    accepted = services.cancel_purchase_order(_conditional(user, subcontractor, item), user)
    refused = services.cancel_purchase_order(_conditional(user, subcontractor, item), user)
    for po in (accepted, refused):
        (row,) = Outbox.objects.filter(purchase_order=po, revision_no=2)
        assert (row.purpose_code, row.response) == (edi_codes.PURPOSE_CANCEL, "受け取った")
        Outbox.objects.filter(pk=row.pk).update(sent_at=_at(10, 10))
    _answer(accepted, 2, edi_codes.REPLY_ACCEPTED)
    _answer(refused, 2, edi_codes.REPLY_REJECTED, "作り物の断り")

    assert _flow(accepted) == FIRST_REVISION + [
        ("sent", "10:10", "第 2 版の取消を送った (相手が受け取った)"),
        ("reply", "10:20", "相手が第 2 版の取消を受け入れた"),
        ("cancel", "None", "発注を取消"),
    ]
    assert _reply_titles(refused) == ["相手が条件付で受けた", "相手が第 2 版の取消を断った (理由: 作り物の断り)"]


# --- (d) 第 2 版を送れなかった (相手に届かない) ---


@pytest.mark.django_db
def test_failed_revision_send_has_no_answer(monkeypatch, user, subcontractor, item, send_connection):  # noqa: F811
    changed = _conditional(user, subcontractor, item)
    cancelled = _conditional(user, subcontractor, item)
    _fail_sending(monkeypatch)
    _change(changed, user)
    services.cancel_purchase_order(PurchaseOrder.objects.get(pk=cancelled.pk), user)
    rows = {}
    for po in (changed, cancelled):
        (row,) = Outbox.objects.filter(purchase_order=po, revision_no=2)
        assert row.response != "受け取った"
        Outbox.objects.filter(pk=row.pk).update(sent_at=_at(10, 10))
        rows[po.pk] = row

    assert _flow(changed) == FIRST_REVISION + [
        ("sent", "10:10", f"第 2 版の変更を送れなかった ({rows[changed.pk].response})"),
    ]
    assert _flow(cancelled) == FIRST_REVISION + [
        ("sent", "10:10", f"第 2 版の取消を送れなかった ({rows[cancelled.pk].response})"),
        ("cancel", "None", "発注を取消"),
    ]


# --- (e) 第 2 版を送り直した (同じ版の送った記録が 2 行) ---


@pytest.mark.django_db
def test_resent_revision_has_one_answer(monkeypatch, user, subcontractor, item, send_connection):  # noqa: F811
    po = _conditional(user, subcontractor, item)
    _fail_sending(monkeypatch)
    _change(po, user)
    monkeypatch.setattr("requests.post", FakePost())
    services.resend_purchase_order(PurchaseOrder.objects.get(pk=po.pk), user)
    failed, delivered = Outbox.objects.filter(purchase_order=po, revision_no=2).order_by("pk")
    assert delivered.response == "受け取った" and failed.response != "受け取った"
    Outbox.objects.filter(pk=failed.pk).update(sent_at=_at(10, 10))
    Outbox.objects.filter(pk=delivered.pk).update(sent_at=_at(10, 15))
    _answer(po, 2, edi_codes.REPLY_ACCEPTED)

    assert _flow(po) == FIRST_REVISION + [
        ("sent", "10:10", f"第 2 版の変更を送れなかった ({failed.response})"),
        ("sent", "10:15", "第 2 版の変更を送った (相手が受け取った)"),
        ("reply", "10:20", "相手が第 2 版の変更を受け入れた"),
    ]


# --- (f) 流れの出来事を作る時の問い合わせの数は、版の数で増えない ---


def _event_queries(po):
    po = PurchaseOrder.objects.get(pk=po.pk)
    lines = lines_for(po)
    with CaptureQueriesContext(connection) as queries:
        events = events_for(po, lines)
    return len(queries.captured_queries), events


@pytest.mark.django_db
def test_event_queries_do_not_grow_with_revisions(user, subcontractor, item, send_connection):  # noqa: F811
    first_only = _conditional(user, subcontractor, item)
    third = _second_revision(user, _conditional(user, subcontractor, item), reply_type=edi_codes.REPLY_ACCEPTED)
    assert _change(third, user, unit_price="75").revision_no == 3
    Outbox.objects.filter(purchase_order=third, revision_no=3).update(sent_at=_at(10, 30))
    _answer(third, 3, edi_codes.REPLY_REJECTED, "作り物の断り", replied_at=_at(10, 40))

    small, small_events = _event_queries(first_only)
    large, large_events = _event_queries(third)

    assert small == large, (small, large)
    assert [e.title for e in large_events if e.kind == "reply"] == [
        "相手が条件付で受けた",
        "相手が第 2 版の変更を受け入れた",
        "相手が第 3 版の変更を断った (理由: 作り物の断り)",
    ]
    assert len([e for e in small_events if e.kind == "sent"]) == 1
    assert len([e for e in large_events if e.kind == "sent"]) == 3
