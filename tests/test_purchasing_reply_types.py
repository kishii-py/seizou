"""注文回答の 4 つの答え (受諾 AP・条件付受諾 CA・辞退 RE・保留 AJ) を発注する側が読み、発注の状態の組をそろえるテスト (候補 C97)。

1 つの DB に、注文を送る会社と受ける会社の両方の役を置く (tests/test_connect_reply.py と同じ形。つながりと偽の requests.post もそこから使う)。
相手への HTTP は requests.post を偽の物に差し替え、受けた本文を services.receive に渡す (外へは出ない)。
答えの JSON は、受ける側の画面がまだ送らない形 (条件付受諾・保留) も、署名して受け口へ直に送る。
鍵はテストのたびに作り、環境変数に置く。取引先・品目・会社は作り物。
"""

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.connect import services
from seizou.connect.models import Inbox, Outbox
from seizou.core import edi_codes
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrder, PurchaseOrderLine, Receipt
from seizou.purchasing.timeline import events_for
from tests.test_connect_reply import _connect, _into_receive, _signed_post
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret
from tests.test_core_status_colors import _fake, _render

ORDER_ENV = "SEIZOU_PARTNER_REPLYCO_ORDER_SECRET"
REPLY_ENV = "SEIZOU_PARTNER_REPLYCO_REPLY_SECRET"
PASSWORD = "kumo-tests-2611"
#: 金額の閲覧だけを効かせる (部署と書く権限の判定はしない。tests/test_billing_invoice_lines_tab.py と同じ)
MONEY_ON = {"department": "off", "write": "off", "money": "on"}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def reply_secret(monkeypatch):
    monkeypatch.setenv(ORDER_ENV, _new_secret())
    value = _new_secret()
    monkeypatch.setenv(REPLY_ENV, value)
    return value


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の発注元", "role": "customer"}, user)


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user),
    ]


@pytest.fixture
def own_items(user):
    return [
        masters_services.create_item({"part_no": "B-100", "name": "作り物の受ける側の軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "B-200", "name": "作り物の受ける側の板", "unit": "枚"}, user),
    ]


@pytest.fixture
def fake(monkeypatch, user, subcontractor, customer, reply_secret):
    """2 社の役のつながりを置き、相手への HTTP を受け口に回す偽の requests.post。"""
    _connect(user, subcontractor, customer)
    post = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", post)
    return post


def _lines(items):
    return [
        {"item": items[0], "qty": Decimal("10"), "unit_price": Decimal("25.5"), "due_date": date(2026, 11, 20)},
        {"item": items[1], "qty": Decimal("5"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 27)},
    ]


def _send_order(subcontractor, items, user):
    """明細 2 行の発注を相手へ送る (相手の受け口に受信した注文ができる)。"""
    return purchasing_services.create_purchase_order(subcontractor, _lines(items), user, order_date=date(2026, 11, 2))


def _sent(subcontractor, items, user):
    """相手へ送らずに「送った」にした明細 2 行の発注 (状態の移り方だけを見るため)。"""
    po = purchasing_services.create_purchase_order(
        subcontractor, _lines(items), user, order_date=date(2026, 11, 2), send=False
    )
    return purchasing_services.mark_sent(po, user)


def _raw(po, reply_type="AP", *, lines=None, reason="", msg_id=None, drop=()):
    """手で作る注文回答の本文 (送る側と同じ bytes の作り方)。返す物は (番号, 本文の bytes)。drop の鍵は送らない。"""
    msg_id = msg_id or str(uuid.uuid4())
    data = {
        "id": msg_id,
        "kind": "order_reply",
        "sent_at": "2026-11-07T10:00:00+09:00",
        "order_id": str(Outbox.objects.filter(purchase_order=po).order_by("pk").first().message_id),
        "order_no": po.po_no,
        "reply_type": reply_type,
        "lines": [] if lines is None else lines,
        "supplier_order_no": "SO-0001" if reply_type in ("AP", "CA") else "",
        "reason": reason,
        "supplier": {"id": "b-company", "name": "作り物の受ける会社"},
    }
    for key in drop:
        data.pop(key)
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return msg_id, raw


#: 1 行目だけを条件付にした明細の答え (単価を下げ、数量と納期も直す)
CA_LINES = [
    {"line_no": 1, "reply_type": "CA", "reply_unit_price": "24.00", "reply_qty": "8", "reply_due_date": "2026-11-25"},
    {"line_no": 2, "reply_type": "AP"},
]


def _post(client, po, secret, reply_type="AP", **kwargs):
    msg_id, raw = _raw(po, reply_type, **kwargs)
    return _signed_post(client, raw, msg_id, secret), msg_id


def _detail(client, po):
    response = client.get(reverse("purchasing:purchaseorder_detail", args=[po.pk]))
    assert response.status_code == 200
    return response.content.decode()


def _reply_title(po):
    return [event.title for event in events_for(po) if event.kind == "reply"]


# --- (a) (b) 受け入れると断るの往復 ---


@pytest.mark.django_db
def test_accept_round_trip_sends_ap_and_empty_lines(fake, user, subcontractor, items, own_items):
    po = _send_order(subcontractor, items, user)
    inbox = Inbox.objects.get()

    services.accept(inbox, {"T-100": own_items[0], "T-200": own_items[1]}, user)

    sent = json.loads(fake.calls[-1]["data"].decode("utf-8"))
    assert sent["reply_type"] == "AP"
    assert sent["lines"] == []
    assert "result" not in sent
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ACCEPTED
    assert po.reply_type == "AP"
    assert _reply_title(po) == ["相手が受け入れた"]


@pytest.mark.django_db
def test_reject_round_trip_sends_re_with_reason(fake, user, subcontractor, items):
    po = _send_order(subcontractor, items, user)

    services.reject(Inbox.objects.get(), "納期に間に合わないため", user)

    sent = json.loads(fake.calls[-1]["data"].decode("utf-8"))
    assert sent["reply_type"] == "RE"
    assert sent["reason"] == "納期に間に合わないため"
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_REJECTED
    assert po.reply_type == "RE"
    assert po.reply_note == "納期に間に合わないため"


# --- (c) 条件付受諾 ---


@pytest.mark.django_db
def test_conditional_reply_keeps_order_values_and_shows_lines(client, settings, fake, user, subcontractor, items, reply_secret):
    po = _send_order(subcontractor, items, user)

    response, _msg_id = _post(client, po, reply_secret, "CA", lines=CA_LINES)

    assert response.status_code == 200, response.json()
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_CONDITIONAL
    assert po.get_status_display() == "相手が条件付で受けた"
    assert po.reply_type == "CA"
    first, second = po.lines.order_by("line_no")
    assert first.reply_type == "CA"
    assert (first.reply_unit_price, first.reply_qty, first.reply_due_date) == (Decimal("24"), Decimal("8"), date(2026, 11, 25))
    assert second.reply_type == "AP"
    assert (second.reply_unit_price, second.reply_qty, second.reply_due_date) == (None, None, None)
    # 発注の値は変えない (直すのは発注元が変更を送るとき)
    assert (first.unit_price, first.qty, first.due_date) == (Decimal("25.5"), Decimal("10"), date(2026, 11, 20))
    assert _reply_title(po) == ["相手が条件付で受けた"]

    client.force_login(user)
    body = _detail(client, po)
    alert = body.split('<div id="po-alert">', 1)[1].split("</div>\n", 1)[0]
    assert "相手が条件付で受けました" in alert
    assert f'data-reply-line="{first.pk}"' in body
    assert "相手の回答: 単価 ¥24 (注文 ¥25.5)・数量 8 個 (注文 10)・納期 2026-11-25 (注文 2026-11-20)" in body
    assert f'data-reply-line="{second.pk}"' not in body

    # 金額の閲覧の無い人には回答単価を出さない
    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    accounts_services.issue_account("jimu", PASSWORD, [], [], {}, None)
    client.force_login(get_user_model().objects.get(username="jimu"))
    body = _detail(client, po)
    assert f'data-reply-line="{first.pk}"' in body
    assert "相手の回答: 数量 8 個 (注文 10)・納期 2026-11-25 (注文 2026-11-20)" in body
    assert "¥24" not in body


# --- (d) 保留 ---


@pytest.mark.django_db
def test_hold_then_answer_and_old_hold_again_is_refused(client, fake, user, subcontractor, items, reply_secret):
    po = _send_order(subcontractor, items, user)

    first, hold_id = _post(client, po, reply_secret, "AJ")
    assert first.status_code == 200, first.json()
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ON_HOLD
    assert po.get_status_display() == "相手が保留した"
    assert po.reply_type == "AJ"
    assert po.replied_at is not None
    assert _reply_title(po) == ["相手が保留した"]

    # 同じ番号の 2 回目は何もしない (R11 の考え)
    again, _same = _post(client, po, reply_secret, "AJ", msg_id=hold_id)
    assert again.status_code == 200

    accepted, _accept_id = _post(client, po, reply_secret, "AP")
    assert accepted.status_code == 200, accepted.json()
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ACCEPTED
    assert po.reply_type == "AP"

    # 答えが進んだ後に前の保留の知らせがもう 1 回届くと、答えを待っている状態の外なので断り、全部戻す
    late, _late_id = _post(client, po, reply_secret, "AJ", msg_id=hold_id)
    assert late.status_code == 400
    assert late.json()["message"] == (
        "状態が「相手が受け入れた」の発注は「相手が保留した」にできません。"
        "「送った」の発注だけを進めてください (「相手が保留した」の発注も進められます)。"
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ACCEPTED
    assert po.reply_type == "AP"


@pytest.mark.django_db
def test_hold_then_conditional_or_rejected(user, subcontractor, items):
    conditional = purchasing_services.mark_on_hold(_sent(subcontractor, items, user), user)
    rows = [{"line_no": 2, "reply_type": "CA", "unit_price": Decimal("90"), "qty": Decimal("5"), "due_date": None}]
    conditional = purchasing_services.mark_conditional(conditional, user, lines=rows)
    assert conditional.status == PurchaseOrder.STATUS_CONDITIONAL
    assert conditional.lines.get(line_no=1).reply_type == "AP"
    assert conditional.lines.get(line_no=2).reply_unit_price == Decimal("90")

    rejected = purchasing_services.mark_on_hold(_sent(subcontractor, items, user), user)
    rejected = purchasing_services.mark_rejected(rejected, user, reason="作り物の理由")
    assert rejected.status == PurchaseOrder.STATUS_REJECTED
    assert rejected.reply_type == "RE"


# --- (e) 断る形 ---


def _bad_lines():
    good = {"line_no": 1, "reply_type": "CA", "reply_unit_price": "24", "reply_qty": "8", "reply_due_date": None}
    return {
        "empty": [],
        "line_no_text": [{**good, "line_no": "1"}],
        "line_no_twice": [good, {"line_no": 1, "reply_type": "AP"}],
        "qty_zero": [{**good, "reply_qty": "0"}],
        "price_too_long": [{**good, "reply_unit_price": "1000000000000"}],
        "line_pending": [{"line_no": 1, "reply_type": "AJ"}],
        "due_not_date": [{**good, "reply_due_date": "11/25"}],
    }


@pytest.mark.django_db
def test_bad_replies_are_refused_and_order_stays(client, fake, user, subcontractor, items, reply_secret):
    po = _send_order(subcontractor, items, user)

    unknown, _ = _post(client, po, reply_secret, "ZZ")
    missing, _ = _post(client, po, reply_secret, drop=("reply_type",))
    assert unknown.status_code == missing.status_code == 400
    assert unknown.json()["message"] == services.MESSAGE_REPLY_TYPE
    assert missing.json()["message"] == services.MESSAGE_REPLY_SHAPE

    for name, lines in _bad_lines().items():
        response, _ = _post(client, po, reply_secret, "CA", lines=lines)
        assert response.status_code == 400, name
        assert response.json()["message"] == services.MESSAGE_REPLY_LINES, name

    # 発注に無い行番号は、明細を書かずに全部戻す
    response, _ = _post(client, po, reply_secret, "CA", lines=[{**CA_LINES[0], "line_no": 9}])
    assert response.status_code == 400
    assert response.json()["message"] == f"注文回答の明細の行番号 9 が発注 {po.po_no} にありません。"

    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_SENT
    assert po.reply_type == ""
    assert set(po.lines.values_list("reply_type", flat=True)) == {""}


# --- (f) 一部納品 ---


@pytest.mark.django_db
def test_partial_receipt_moves_only_accepted_order(user, subcontractor, items):
    accepted = purchasing_services.mark_accepted(_sent(subcontractor, items, user), user)
    first, second = accepted.lines.order_by("line_no")
    purchasing_services.receive(first, Decimal("4"), date(2026, 11, 10), user)
    accepted.refresh_from_db()
    assert accepted.status == PurchaseOrder.STATUS_PARTIAL
    assert accepted.get_status_display() == "一部納品"
    purchasing_services.receive(first, Decimal("6"), date(2026, 11, 11), user)
    purchasing_services.receive(second, Decimal("5"), date(2026, 11, 11), user)
    accepted.refresh_from_db()
    assert accepted.status == PurchaseOrder.STATUS_DELIVERED

    draft = purchasing_services.create_purchase_order(
        subcontractor, _lines(items), user, order_date=date(2026, 11, 2), send=False
    )
    sent = _sent(subcontractor, items, user)
    on_hold = purchasing_services.mark_on_hold(_sent(subcontractor, items, user), user)
    conditional = purchasing_services.mark_conditional(_sent(subcontractor, items, user), user, lines=[])
    for po, status in (
        (draft, PurchaseOrder.STATUS_DRAFT),
        (sent, PurchaseOrder.STATUS_SENT),
        (on_hold, PurchaseOrder.STATUS_ON_HOLD),
        (conditional, PurchaseOrder.STATUS_CONDITIONAL),
    ):
        purchasing_services.receive(po.lines.get(line_no=1), Decimal("4"), date(2026, 11, 10), user)
        po.refresh_from_db()
        assert po.status == status

    # 受入が先に入った「送った」発注に後から受諾が届くと一部納品
    late = purchasing_services.mark_accepted(sent, user)
    assert late.status == PurchaseOrder.STATUS_PARTIAL
    assert late.reply_type == "AP"


# --- (g) 状態の印の色 ---


@pytest.mark.parametrize(
    "value, label",
    [("partial", "一部納品"), ("conditional", "相手が条件付で受けた"), ("on_hold", "相手が保留した")],
)
def test_new_purchase_order_statuses_are_warning(value, label):
    assert _render(_fake(value, label, "purchasing")) == f'<span class="badge badge-soft badge-warning">{label}</span>'


# --- (i) コードの値と名前 ---


def test_edi_reply_codes():
    assert edi_codes.ORDER_REPLY_TYPES == (
        ("AP", "受諾回答"),
        ("CA", "条件付受諾回答"),
        ("RE", "辞退回答"),
        ("AJ", "保留"),
    )
    assert edi_codes.ORDER_REPLY_LINE_TYPES == (
        ("AP", "明細行受諾"),
        ("CA", "明細行条件付き受諾"),
        ("RE", "明細行受託不可"),
    )
    assert "AJ" not in dict(edi_codes.ORDER_REPLY_LINE_TYPES)
    assert services.REPLY_TYPES == {"AP", "CA", "RE", "AJ"}
    assert services.REPLY_LINE_TYPES == {"AP", "CA", "RE"}


# --- (l) 条件付で受けた発注の受入の金額 ---


@pytest.mark.django_db
def test_receipt_on_conditional_order_uses_order_price(client, user, subcontractor, items):
    rows = [{"line_no": 1, "reply_type": "CA", "unit_price": Decimal("20"), "qty": Decimal("10"), "due_date": None}]
    po = purchasing_services.mark_conditional(_sent(subcontractor, items, user), user, lines=rows)
    line = po.lines.get(line_no=1)

    purchasing_services.receive(line, Decimal("4"), date(2026, 11, 10), user)

    receipt = Receipt.objects.get()
    assert receipt.amount == Decimal("102")  # 発注の単価 25.5 × 4 (回答単価 20 で数えない)
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_CONDITIONAL
    assert PurchaseOrderLine.objects.get(pk=line.pk).unit_price == Decimal("25.5")

    client.force_login(user)
    body = client.get(reverse("purchasing:receipt_create"), {"line": line.pk}).content.decode()
    assert "条件付" not in body
    assert "回答単価" not in body


# --- 変更 (第 2 版から) への受諾 ---


@pytest.mark.django_db
def test_change_accepted_moves_only_conditional_order(user, subcontractor, items):
    rows = [{"line_no": 1, "reply_type": "CA", "unit_price": Decimal("20"), "qty": Decimal("10"), "due_date": None}]
    conditional = purchasing_services.mark_conditional(_sent(subcontractor, items, user), user, lines=rows)

    moved = purchasing_services.mark_change_accepted(conditional, user)

    assert moved.status == PurchaseOrder.STATUS_ACCEPTED
    assert set(moved.lines.values_list("reply_type", flat=True)) == {""}
    assert moved.lines.get(line_no=1).reply_unit_price is None

    received = purchasing_services.mark_conditional(_sent(subcontractor, items, user), user, lines=rows)
    purchasing_services.receive(received.lines.get(line_no=1), Decimal("4"), date(2026, 11, 10), user)
    assert purchasing_services.mark_change_accepted(received, user).status == PurchaseOrder.STATUS_PARTIAL

    accepted = purchasing_services.mark_accepted(_sent(subcontractor, items, user), user)
    assert purchasing_services.mark_change_accepted(accepted, user).status == PurchaseOrder.STATUS_ACCEPTED
