"""条件付で受けた発注の後の版を相手が受け入れたときの、発注の詳細の黄の 1 行と、発注の流れの並び (N418。本体の決め 11-11 14:2x)。

取引先と品番は作り物だけを使う。相手への HTTP は偽の requests.post (tests.test_connect_send の FakePost) に差し替え、鍵は試験のたびに作る。
発注は services で作り、送る (create_purchase_order)・条件付で受けた (mark_conditional)・変更の第 2 版を送る (change_purchase_order) の順に進める。
送った記録の日時 (Outbox.sent_at) と、第 2 版への相手の答え (Outbox.reply_type・reply_note・replied_at) は、試験の中で update で入れる
(送る口と受け口を通さずに、出来事の時刻の順だけを作るため)。
発注の詳細の HTML は Django の test client で取り、#po-alert の中の字と段 (alert-warning) を見る。
"""

import re
import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from seizou.connect import services as connect_services
from seizou.connect.models import Outbox
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder
from seizou.purchasing.timeline import events_for
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ENV_NAME = "SEIZOU_PARTNER_TESTSUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"
DAY = date(2026, 11, 10)
#: 今の条件付の黄の 1 行の字の一部 (合わせるときの案内)
ADJUST_TEXT = "合わせるときは「変更」で発注を直して送り"
SETTLED_TEXT = "相手は第 2 版の変更を受け入れました"


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def send_connection(monkeypatch, subcontractor, user):
    monkeypatch.setenv(ENV_NAME, _new_secret())
    monkeypatch.setattr("requests.post", FakePost())
    return connect_services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)


def _at(hour, minute):
    """DAY のその時刻 (試験の設定の時間帯 = Asia/Tokyo)。"""
    return timezone.make_aware(datetime(DAY.year, DAY.month, DAY.day, hour, minute))


def _conditional(user, subcontractor, item, *, sent_at=None, replied_at=None):
    """第 1 版を送り (既定は 10:00)、相手が単価 65 を 70 にして条件付で受けた (既定は 10:05) 発注。"""
    po = services.create_purchase_order(
        subcontractor, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("65")}], user, order_date=DAY
    )
    Outbox.objects.filter(purchase_order=po).update(sent_at=sent_at or _at(10, 0))
    rows = [{"line_no": 1, "reply_type": "CA", "unit_price": Decimal("70"), "qty": Decimal("10"), "due_date": None}]
    po = services.mark_conditional(
        PurchaseOrder.objects.get(pk=po.pk), user, lines=rows, replied_at=replied_at or _at(10, 5)
    )
    assert po.status == PurchaseOrder.STATUS_CONDITIONAL
    return po


def _second_revision(user, po, *, reply_type="", reply_note=""):
    """条件付の発注を「変更」で単価 70 に直して第 2 版を送り (10:10)、相手の答えを書く (reply_type が空なら答え待ち)。"""
    changed = services.change_purchase_order(po, {}, [{"line": po.lines.get(), "unit_price": Decimal("70")}], user)
    assert changed.revision_no == 2
    rows = Outbox.objects.filter(purchase_order=po, revision_no=2)
    assert [row.response for row in rows] == ["受け取った"]
    rows.update(sent_at=_at(10, 10))
    if reply_type:
        rows.update(reply_type=reply_type, reply_note=reply_note, replied_at=_at(10, 20))
    po = PurchaseOrder.objects.get(pk=po.pk)
    # 版 2 からの答えは発注の状態を変えない (connect.services._record_revision_reply の作り)
    assert po.status == PurchaseOrder.STATUS_CONDITIONAL
    return po


def _po_alert(client, po):
    """発注の詳細の #po-alert の中身 (入れ子の div を数え、#po-alert が閉じる所まで)。"""
    response = client.get(reverse("purchasing:purchaseorder_detail", args=[po.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    start = body.index('<div id="po-alert">')
    depth, pos = 0, start
    while True:
        opened = body.find("<div", pos)
        closed = body.find("</div>", pos)
        if opened != -1 and opened < closed:
            depth, pos = depth + 1, opened + len("<div")
        else:
            depth, pos = depth - 1, closed + len("</div>")
            if depth == 0:
                return body[start:pos]


def _flow_kinds(client, po):
    body = client.get(reverse("purchasing:purchaseorder_detail", args=[po.pk])).content.decode()
    start = body.index('<section id="po-flow"')
    section = body[start : body.index("</section>", start)]
    return re.findall(r'data-flow-kind="(\w+)"', section)


def _clock(event):
    return f"{timezone.localtime(event.when):%H:%M}" if event.has_time else str(event.when)


# --- (a) 第 2 版を相手が受け入れた条件付の発注 ---


@pytest.mark.django_db
def test_accepted_second_revision_replaces_conditional_alert(client_logged_in, user, subcontractor, item, send_connection):
    po = _second_revision(user, _conditional(user, subcontractor, item), reply_type="AP")

    alert = _po_alert(client_logged_in, po)

    assert (
        '<div class="alert alert-warning alert-soft mb-4" role="alert">' in alert
        and "<span>相手は第 2 版の変更を受け入れました (2026-11-10 10:20)。相手の受注は第 2 版の中身です。"
        "発注の状態の字は「相手が条件付で受けた」のまま変わりません。</span></div>" in alert
    )
    assert ADJUST_TEXT not in alert
    assert "data-revision-alert" not in alert
    assert alert.count('role="alert"') == 1


# --- (b) 答え待ち・版 1・断られた は今の字のまま ---


@pytest.mark.django_db
def test_other_conditional_orders_keep_current_alert(client_logged_in, user, subcontractor, item, send_connection):
    waiting = _second_revision(user, _conditional(user, subcontractor, item))
    first = _conditional(user, subcontractor, item)
    refused = _second_revision(
        user, _conditional(user, subcontractor, item), reply_type="RE", reply_note="作り物の断り"
    )

    for po in (waiting, first):
        alert = _po_alert(client_logged_in, po)
        assert "<span>相手が条件付で受けました (2026-11-10 10:05)。" in alert
        assert ADJUST_TEXT in alert
        assert SETTLED_TEXT not in alert
        assert "data-revision-alert" not in alert

    alert = _po_alert(client_logged_in, refused)
    assert ADJUST_TEXT in alert
    assert SETTLED_TEXT not in alert
    assert (
        '<div class="alert alert-warning alert-soft mb-4" role="alert" data-revision-alert>' in alert
        and "<span>相手は第 2 版の変更を断りました。理由: 作り物の断り。相手の受注は前の版の中身のままです。" in alert
    )


# --- (c) 同じ日の送ったと相手の答えは時刻の順 ---


@pytest.mark.django_db
def test_sent_and_reply_on_same_day_line_up_by_clock(client_logged_in, user, subcontractor, item, send_connection):
    po = _second_revision(user, _conditional(user, subcontractor, item))

    events = events_for(po)

    assert [(e.kind, _clock(e)) for e in events] == [
        ("order", "2026-11-10"),
        ("sent", "10:00"),
        ("reply", "10:05"),
        ("sent", "10:10"),
    ]
    assert _flow_kinds(client_logged_in, po) == ["order", "sent", "reply", "sent"]


# --- (d) 時刻まで同じなら送ったが先。ほかの種類は今の並び ---


@pytest.mark.django_db
def test_sent_comes_before_reply_at_the_same_clock(user, subcontractor, item, send_connection):
    po = _conditional(user, subcontractor, item, sent_at=_at(10, 0), replied_at=_at(10, 0))
    connect_services.record_shipping_notice(
        po.lines.get(), Decimal("4"), DAY, None, message_id=uuid.uuid4(), shipment_no="SH-0001"
    )

    events = events_for(PurchaseOrder.objects.get(pk=po.pk))

    # 日付だけの出荷の知らせ (0 時) は、時刻のある送ったと答えより前に出さない (仕事の順のまま)
    assert [(e.kind, _clock(e)) for e in events] == [
        ("order", "2026-11-10"),
        ("sent", "10:00"),
        ("reply", "10:00"),
        ("shipping_notice", "2026-11-10"),
    ]
