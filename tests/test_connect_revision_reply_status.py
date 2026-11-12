"""条件付で受けた発注は、変更を相手が受け入れたら「相手が受け入れた」に進む (依頼書 N437。本人の決め 11-12 14:1x)。

訳: 相手が条件付で受けた発注を、こちらが条件に合わせて直して変更を送り、相手がその変更を受け入れたら、条件の話し合いは済んでいる。
この動きは 11-07 にも決まっていて (作者の決め 11-07 06:42)、状態を進める関数 purchasing.services.mark_change_accepted も在ったが、
変更の版への答えを受ける所 (connect の注文回答の受け口) が呼んでいなかった。発注の一覧で、話がまとまった発注と、まだ条件を詰めている
発注の見分けが付かなかった。
進めるのは、今の版の変更への受諾が届いた時だけ (断られた時・古い版への遅れた答え・取消の版・条件付でない発注は変えない)。
2 社の役とつながりは tests/test_connect_reply.py と tests/test_connect_revision_accept.py の fixture と helper、版への答えの作り方は
tests/test_connect_revision_protocol.py の helper を使う (そのファイルは変えない)。作り物の取引先と品番だけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.connect import services
from seizou.connect.models import Inbox, Outbox
from seizou.core import edi_codes
from seizou.core.models import ChangeLog
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrder
from tests.test_connect_reply import (  # noqa: F401 (fixture)
    _into_receive,
    customer,
    order_secret,
    reply_secret,
    subcontractor,
    user,
)
from tests.test_connect_revision_accept import _mapping, _send_first, connections, items  # noqa: F401 (fixture)
from tests.test_connect_revision_protocol import _reply_payload, _signed_receive
from tests.test_connect_send import FakePost

#: 相手の条件 (明細 1 の単価を 1000 → 900 に直して受けた)
CONDITIONS = [{"line_no": 1, "reply_type": "CA", "unit_price": Decimal("900"), "qty": Decimal("300"), "due_date": None}]
#: 条件付の発注の詳細の黄の 1 行の字の一部
CONDITIONAL_TEXT = "相手が条件付で受けました"


def _conditional(subcontractor, items, user):  # noqa: F811
    """第 1 版を送り、相手が明細 1 の単価を直して条件付で受けた発注 (明細の答えが入っている)。"""
    po = _send_first(subcontractor, items, user)
    po = purchasing_services.mark_conditional(PurchaseOrder.objects.get(pk=po.pk), user, lines=CONDITIONS)
    assert po.status == PurchaseOrder.STATUS_CONDITIONAL
    assert po.lines.get(line_no=1).reply_unit_price == Decimal("900")
    return po


def _change(po, user, unit_price="900", line_no=1):  # noqa: F811
    """相手の条件に合わせて明細の単価を直し (既定は明細 1)、変更の版を送る。ほかの明細は今の値のまま渡す (渡さない明細は取消になるため)。"""
    po = PurchaseOrder.objects.get(pk=po.pk)
    lines = []
    for line in po.lines.exclude(line_status_code=edi_codes.LINE_DELETED).order_by("line_no"):
        price = Decimal(unit_price) if line.line_no == line_no else line.unit_price
        lines.append({"line": line, "qty": line.qty, "unit_price": price, "due_date": line.due_date, "note": line.note})
    return purchasing_services.change_purchase_order(po, {}, lines, user)


def _row(po, revision_no):
    """その版の最初の送った注文の行 (相手の答えが書かれる行)。"""
    return Outbox.objects.filter(purchase_order=po, revision_no=revision_no).order_by("pk").first()


def _status(po):
    return PurchaseOrder.objects.get(pk=po.pk).status


def _answers(po):
    """明細ごとの相手の答えの 4 つの値。"""
    return [
        (line.reply_type, line.reply_unit_price, line.reply_qty, line.reply_due_date)
        for line in PurchaseOrder.objects.get(pk=po.pk).lines.order_by("line_no")
    ]


@pytest.fixture
def sent(monkeypatch, connections):  # noqa: F811
    """送る口を偽の requests.post にする (相手の受け口へは渡さない。答えは試験が署名して受け口へ入れる)。"""
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    return fake


# --- (a) 今の版の変更への受諾で進む ---


@pytest.mark.django_db
def test_accepted_change_moves_conditional_order_to_accepted(sent, user, subcontractor, items, reply_secret):  # noqa: F811
    po = _conditional(subcontractor, items, user)
    assert _change(po, user).revision_no == 2
    assert _status(po) == PurchaseOrder.STATUS_CONDITIONAL  # 変更を送っただけでは進まない
    count = ChangeLog.objects.count()

    _signed_receive(_reply_payload(_row(po, 2), "accepted"), reply_secret)

    assert _status(po) == PurchaseOrder.STATUS_ACCEPTED
    assert _answers(po) == [("", None, None, None)] * 3  # 明細の答えは空に戻る
    row = _row(po, 2)
    assert row.reply_type == edi_codes.REPLY_ACCEPTED and row.replied_at is not None  # 送った記録には受諾が残る
    # 変更の履歴: 発注の行が 1 つ (状態)、人の字は相手の会社
    head = [log for log in ChangeLog.objects.order_by("pk")[count:] if log.model == "purchasing.purchaseorder"]
    assert len(head) == 1 and head[0].actor_label == "作り物の協力企業 の seizou"


# --- (b) 受入のある発注は「一部納品」へ ---


@pytest.mark.django_db
def test_accepted_change_moves_order_with_receipt_to_partial(sent, user, subcontractor, items, reply_secret):  # noqa: F811
    po = _conditional(subcontractor, items, user)
    purchasing_services.receive(po.lines.get(line_no=1), Decimal("40"), date(2026, 11, 10), user)
    # 受入のある明細の単価は変えられない (今ある決まり) ので、受入の無い明細 2 を直して変更を送る
    _change(po, user, "990", line_no=2)

    _signed_receive(_reply_payload(_row(po, 2), "accepted"), reply_secret)

    assert _status(po) == PurchaseOrder.STATUS_PARTIAL


# --- (c) 古い版への遅れた受諾では進まない ---


@pytest.mark.django_db
def test_late_acceptance_of_an_old_revision_does_not_move(sent, user, subcontractor, items, reply_secret):  # noqa: F811
    po = _conditional(subcontractor, items, user)
    _change(po, user, "900")
    assert _change(po, user, "950").revision_no == 3

    _signed_receive(_reply_payload(_row(po, 2), "accepted"), reply_secret)
    assert _status(po) == PurchaseOrder.STATUS_CONDITIONAL
    assert _answers(po)[0][0] == "CA"  # 明細の答えも残る

    _signed_receive(_reply_payload(_row(po, 3), "accepted"), reply_secret)
    assert _status(po) == PurchaseOrder.STATUS_ACCEPTED


# --- (d) 取消の版への受諾では進まない ---


@pytest.mark.django_db
def test_accepted_cancel_revision_keeps_order_cancelled(sent, user, subcontractor, items, reply_secret):  # noqa: F811
    po = _conditional(subcontractor, items, user)
    purchasing_services.cancel_purchase_order(PurchaseOrder.objects.get(pk=po.pk), user)
    row = _row(po, 2)
    assert row.purpose_code == edi_codes.PURPOSE_CANCEL

    _signed_receive(_reply_payload(row, "accepted"), reply_secret)

    assert _status(po) == PurchaseOrder.STATUS_CANCELLED


# --- (e) 断られた時は条件付のまま ---


@pytest.mark.django_db
def test_refused_change_keeps_order_conditional(sent, client_logged_in, user, subcontractor, items, reply_secret):  # noqa: F811
    po = _conditional(subcontractor, items, user)
    _change(po, user)

    _signed_receive(_reply_payload(_row(po, 2), "rejected", reason="作り物の断り"), reply_secret)

    assert _status(po) == PurchaseOrder.STATUS_CONDITIONAL
    assert _answers(po)[0] == ("CA", Decimal("900"), Decimal("300"), None)
    body = client_logged_in.get(reverse("purchasing:purchaseorder_detail", args=[po.pk])).content.decode()
    assert "相手は第 2 版の変更を断りました。理由: 作り物の断り。" in body


# --- (f) 条件付でない発注は変えない ---


@pytest.mark.django_db
def test_accepted_change_of_an_accepted_order_stays_accepted(sent, user, subcontractor, items, reply_secret):  # noqa: F811
    po = purchasing_services.mark_accepted(PurchaseOrder.objects.get(pk=_send_first(subcontractor, items, user).pk), user)
    _change(po, user)
    count = ChangeLog.objects.filter(model="purchasing.purchaseorder").count()

    _signed_receive(_reply_payload(_row(po, 2), "accepted"), reply_secret)

    assert _status(po) == PurchaseOrder.STATUS_ACCEPTED
    assert ChangeLog.objects.filter(model="purchasing.purchaseorder").count() == count  # 状態を書き直さない


# --- (g) 同じ番号の受諾が 2 回届いても 1 回分 (R11) ---


@pytest.mark.django_db
def test_same_acceptance_twice_writes_history_once(sent, user, subcontractor, items, reply_secret):  # noqa: F811
    po = _conditional(subcontractor, items, user)
    _change(po, user)
    data = _reply_payload(_row(po, 2), "accepted")

    _signed_receive(data, reply_secret)
    count = ChangeLog.objects.count()
    _signed_receive(data, reply_secret)

    assert ChangeLog.objects.count() == count
    assert _status(po) == PurchaseOrder.STATUS_ACCEPTED


# --- (g2) 保留の後に受諾が届く (511 回目の直しの道) ---


@pytest.mark.django_db
def test_pending_then_accepted_moves_only_on_the_acceptance(sent, user, subcontractor, items, reply_secret):  # noqa: F811
    """相手が変更の版を保留にした後で受け入れると、受諾は保留とは別の番号で届く。保留では進まず、受諾で進む。"""
    po = _conditional(subcontractor, items, user)
    _change(po, user)
    row = _row(po, 2)

    _signed_receive({**_reply_payload(row, "accepted"), "reply_type": edi_codes.REPLY_PENDING}, reply_secret)
    assert _status(po) == PurchaseOrder.STATUS_CONDITIONAL
    assert Outbox.objects.get(pk=row.pk).reply_type == edi_codes.REPLY_PENDING

    _signed_receive(_reply_payload(row, "accepted"), reply_secret)
    assert _status(po) == PurchaseOrder.STATUS_ACCEPTED
    assert Outbox.objects.get(pk=row.pk).reply_type == edi_codes.REPLY_ACCEPTED


# --- (h) 2 社を 1 つの DB に置いた通しの形 ---


@pytest.mark.django_db
def test_two_companies_round_trip(monkeypatch, client_logged_in, user, subcontractor, items, connections):  # noqa: F811
    """受ける会社が条件を直して受け入れる → 発注した会社が変更を送る → 受ける会社が変更を受け入れる → 発注が「相手が受け入れた」。"""
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send_first(subcontractor, items, user)
    base = Inbox.objects.get(revises=None)
    answers = {1: {"unit_price": Decimal("900"), "qty": Decimal("300"), "due_date": None}}
    services.accept_with_conditions(base, _mapping(items, 3), answers, user)
    assert _status(po) == PurchaseOrder.STATUS_CONDITIONAL
    detail_url = reverse("purchasing:purchaseorder_detail", args=[po.pk])
    before = client_logged_in.get(detail_url).content.decode()
    assert CONDITIONAL_TEXT in before and "data-reply-line" in before

    _change(po, user, "900")
    revision = Inbox.objects.get(revises=base)
    services.accept(revision, _mapping(items, 3), user)

    assert _status(po) == PurchaseOrder.STATUS_ACCEPTED
    assert _answers(po) == [("", None, None, None)] * 3
    body = client_logged_in.get(detail_url).content.decode()
    assert CONDITIONAL_TEXT not in body  # 条件付の黄の 1 行が無い
    assert "data-reply-line" not in body  # 明細の下の黄の行 (相手の回答単価・回答数量・回答納期) が無い
    listing = client_logged_in.get(reverse("purchasing:purchaseorder_list")).content.decode()
    row = listing[listing.index(po.po_no) :]
    assert "相手が受け入れた" in row[: row.index("</tr>")]
