"""仕入明細を相手へ送り、相手が受けて受注の明細に当てるテスト。初見テスト R5 の候補 C111 その 3。

1 つの DB に、注文を送る会社と受ける会社の両方の役を置く (test_connect_acceptance.py の形)。
仕入明細は注文と同じ向き (協力企業 S001 への「送る」→ 得意先 C001 からの「受ける」) を通る。
- (a) 送る本文の鍵と値 (文書の種類・目的・税率・課税分類・相手の名前・日付・数の字)
- (b) 受ける側の受けた仕入明細と明細・同じ本文の 2 回目・送った側の状態・送り合った記録の金額
- (c) 断る形 (400 で、受けた側に行が残らない)
- (d) 届かない・つながりが無い: 仕入明細は作られ、詳細に黄色の 1 行と「送り直す」。送り直しは同じ番号
- (e) 画面 (受注の詳細のタブ・左のメニュー・受けた仕入明細の詳細)
相手への HTTP は requests.post を偽の物に差し替え、受けた本文を services.receive に渡す (外へは出ない)。
鍵はテストのたびに作り、環境変数に置く。取引先・品目・会社は作り物。
"""

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
import requests
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services, signing
from seizou.connect.models import Exchange, Inbox, ReceivedSelfInvoice, ReceivedSelfInvoiceLine
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import SelfInvoice
from tests.test_connect_reply import ORDER_ENV, REPLY_ENV, _connect, _into_receive, _send_order
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

#: 仕入明細の本文の頭の鍵 (C110 の docs/edi-items.md の試験が import する)
SELF_INVOICE_KEYS = {
    "id",
    "kind",
    "sent_at",
    "self_invoice_no",
    "revision_no",
    "document_code",
    "purpose_code",
    "issued_on",
    "period_from",
    "period_to",
    "due_on",
    "subtotal",
    "tax",
    "total",
    "tax_rate",
    "tax_category_code",
    "supplier_name",
    "supplier_registration_no",
    "buyer",
    "lines",
}
#: 仕入明細の明細 1 つの鍵
SELF_INVOICE_LINE_KEYS = {
    "line_no",
    "order_id",
    "order_no",
    "order_line_no",
    "order_revision_no",
    "part_no",
    "part_name",
    "received_on",
    "qty",
    "unit",
    "unit_code",
    "price_basis",
    "unit_price",
    "amount",
    "revised_in",
}
DAY = date(2026, 11, 5)
LATER = date(2026, 11, 12)
ISSUED = date(2026, 11, 30)


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
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "registration_no": "T0000000000002"}, user
    )


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の発注元", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def own_item(user):
    return masters_services.create_item({"part_no": "B-200", "name": "作り物の受ける側の部品", "unit": "個"}, user)


@pytest.fixture
def connections(user, subcontractor, customer, order_secret, reply_secret):
    _connect(user, subcontractor, customer)


def _accepted(monkeypatch, subcontractor, item, own_item, user):
    """注文を送り、相手の役が承認して受注にし、発注元が 2 回受け入れるまで (検収も偽の post の中で往復する)。"""
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    po = _send_order(subcontractor, item, user)
    inbox = Inbox.objects.get()
    order = services.accept(inbox, {"T-100": own_item}, user)
    line = po.lines.get()
    receipts = [
        purchasing_services.receive(line, Decimal("4"), DAY, user),
        purchasing_services.receive(line, Decimal("3"), LATER, user),
    ]
    return fake, po, order, receipts


def _sent(fake):
    """偽の post が最後に受けた仕入明細の本文と見出し。"""
    call = [call for call in fake.calls if json.loads(call["data"].decode("utf-8")).get("kind") == "self_invoice"][-1]
    return json.loads(call["data"].decode("utf-8")), call


def _raw(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _receive(secret, data):
    """本文を新しい番号で署名して受け口に渡す。返す物は services.receive の結果。"""
    msg_id = str(uuid.uuid4())
    body = {**data, "id": msg_id}
    raw = _raw(body)
    return services.receive(raw, signing.headers_for(secret, msg_id, raw))


@pytest.mark.django_db
def test_send_self_invoice_body(monkeypatch, user, subcontractor, customer, item, own_item, connections):
    """(a) 送る本文の鍵と値。支払の条件の無い取引先なので支払期日は null。"""
    fake, po, _order, receipts = _accepted(monkeypatch, subcontractor, item, own_item, user)

    si = purchasing_services.create_self_invoice(subcontractor, receipts, user, issued_on=ISSUED)

    sent, call = _sent(fake)
    assert set(sent) == SELF_INVOICE_KEYS
    assert all(set(line) == SELF_INVOICE_LINE_KEYS for line in sent["lines"])
    assert sent["kind"] == "self_invoice"
    assert sent["id"] == str(si.message_id)
    assert sent["self_invoice_no"] == si.self_invoice_no == "SI-000001"
    assert sent["revision_no"] == 1
    assert sent["document_code"] == "1"
    assert sent["purpose_code"] == "9"
    assert sent["tax_rate"] == "10"
    assert sent["tax_category_code"] == "S"
    assert sent["supplier_name"] == "作り物の協力企業"
    assert sent["supplier_registration_no"] == "T0000000000002"
    assert (sent["issued_on"], sent["period_from"], sent["period_to"]) == ("2026-11-30", "2026-11-01", "2026-11-30")
    assert sent["due_on"] is None
    assert sent["subtotal"] == str(si.subtotal) == "179.00" and sent["total"] == str(si.total)
    for value in (sent["subtotal"], sent["tax"], sent["total"]):
        assert "¥" not in value and "," not in value
    first = sent["lines"][0]
    assert first["order_no"] == po.po_no and first["order_line_no"] == 1 and first["order_revision_no"] == 1
    assert first["received_on"] == "2026-11-05" and first["qty"] == "4.00" and first["unit_price"] == "25.50"
    assert first["unit"] == "個" and first["unit_code"] == "H87" and first["price_basis"] == "unit" and first["revised_in"] == 1
    assert set(sent["buyer"]) == {"id", "name"}
    assert call["data"] == _raw(sent)


@pytest.mark.django_db
def test_receive_self_invoice(monkeypatch, user, order_secret, subcontractor, customer, item, own_item, connections):
    """(b) 受けた仕入明細と明細ができ、同じ本文の 2 回目で行が増えない。送った側は答え待ち。送り合った記録の金額は本文の total。"""
    masters_services.update_partner(subcontractor, {"closing_day": 31, "payment_month": "next", "payment_day": 31}, user)
    fake, po, order, receipts = _accepted(monkeypatch, subcontractor, item, own_item, user)

    si = purchasing_services.create_self_invoice(subcontractor, receipts, user, issued_on=ISSUED)

    received = ReceivedSelfInvoice.objects.get()
    assert received.partner == customer
    assert received.status == ReceivedSelfInvoice.STATUS_RECEIVED
    assert received.self_invoice_no == si.self_invoice_no
    assert received.tax_rate == Decimal("10") and received.tax_category_code == "S"
    assert received.supplier_name == "作り物の協力企業"
    assert (received.period_from, received.period_to, received.due_on) == (date(2026, 11, 1), date(2026, 11, 30), date(2026, 12, 31))
    assert received.total == si.total and received.document_code == "1" and received.purpose_code == "9"
    lines = list(received.lines.order_by("line_no"))
    assert [line.order_line for line in lines] == [order.lines.get(line_no=1)] * 2
    assert [line.buyer_line_no for line in lines] == [1, 1]
    assert [line.qty for line in lines] == [Decimal("4"), Decimal("3")]
    assert lines[0].buyer_order_no == po.po_no

    si.refresh_from_db()
    assert si.sent_at is not None and si.status == SelfInvoice.STATUS_SENT
    assert si.delivery_state().startswith("送った ")

    sent, call = _sent(fake)
    again = _into_receive(call["data"], {k: v for k, v in call["headers"].items() if k != "Content-Type"})
    assert again.status_code == 200
    assert ReceivedSelfInvoice.objects.count() == 1 and ReceivedSelfInvoiceLine.objects.count() == 2

    rows = Exchange.objects.filter(kind="self_invoice")
    assert {row.direction for row in rows} == {Exchange.DIRECTION_SENT, Exchange.DIRECTION_RECEIVED}
    assert all(row.amount == Decimal(sent["total"]) for row in rows)
    assert all(row.kind_label() == "仕入明細" for row in rows)


@pytest.mark.django_db
def test_receive_refuses(monkeypatch, user, order_secret, subcontractor, customer, item, own_item, connections):
    """(c) 断る形は文を返し (受け口は 400)、受けた側に行が残らない。同じ版の別の番号も断る。"""
    fake, _po, _order, receipts = _accepted(monkeypatch, subcontractor, item, own_item, user)
    purchasing_services.create_self_invoice(subcontractor, receipts, user, issued_on=ISSUED)
    sent, _call = _sent(fake)
    assert ReceivedSelfInvoice.objects.count() == 1
    other = {**sent, "self_invoice_no": "SI-999999"}

    def line(**changes):
        return {**other, "lines": [{**sent["lines"][0], **changes}]}

    cases = [
        line(order_id=str(uuid.uuid4())),
        line(order_line_no=99),
        line(qty="0"),
        {**other, "lines": []},
        {**other, "purpose_code": "7"},
        {**other, "supplier_name": "名" * 101},
        line(unit="単" * 11),
    ]
    for data in cases:
        result = _receive(order_secret, data)
        assert isinstance(result, str), data
    assert "100 字まで" in _receive(order_secret, {**other, "supplier_name": "名" * 101})
    assert "2 行目" not in _receive(order_secret, line(unit="単" * 11)) and "単位は 10 字まで" in _receive(order_secret, line(unit="単" * 11))
    same_number = _receive(order_secret, sent)
    assert same_number == f"仕入明細 {sent['self_invoice_no']} は第 1 版まで受けてあります。"
    # 取消 ("1") と修正 ("4") の版は、その 6 とその 5 で受ける (tests/test_purchasing_self_invoice_cancel.py・tests/test_purchasing_self_invoice_revision.py)。
    assert ReceivedSelfInvoice.objects.count() == 1
    assert ReceivedSelfInvoiceLine.objects.count() == 2


@pytest.mark.django_db
def test_undelivered_and_resend(monkeypatch, client, user, subcontractor, customer, item, own_item, connections):
    """(d) 届かない: 仕入明細は作られ未送信のまま、詳細に黄色の 1 行と「送り直す」。送り直しは同じ番号。"""
    _fake, _po, _order, receipts = _accepted(monkeypatch, subcontractor, item, own_item, user)
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("つながらない")))

    si = purchasing_services.create_self_invoice(subcontractor, receipts, user, issued_on=ISSUED)

    assert si.status == SelfInvoice.STATUS_DRAFT and si.sent_at is None
    assert si.saved_level == "warning" and si.saved_note.startswith("仕入明細は相手に届いていません。")
    first_id = si.message_id
    client.force_login(user)
    body = client.get(reverse("purchasing:selfinvoice_detail", args=[si.pk])).content.decode()
    assert "届きません" in body and "送り直す" in body
    assert reverse("purchasing:selfinvoice_resend", args=[si.pk]) in body

    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    response = client.post(reverse("purchasing:selfinvoice_resend", args=[si.pk]))
    assert response.status_code == 302
    si.refresh_from_db()
    assert si.message_id == first_id
    assert fake.calls[-1]["headers"][signing.HEADER_ID] == str(first_id)
    assert si.status == SelfInvoice.STATUS_SENT
    assert ReceivedSelfInvoice.objects.get().message_id == first_id


@pytest.mark.django_db
def test_no_connection(monkeypatch, user, subcontractor, customer, item, own_item, connections):
    """(d) つながりが無い: HTTP を送らず、返事の欄に接続の案内を書く (仕入明細は成立する)。"""
    _fake, _po, _order, receipts = _accepted(monkeypatch, subcontractor, item, own_item, user)
    monkeypatch.setattr(services, "_send_connection", lambda partner: None)
    held = FakePost()
    monkeypatch.setattr("requests.post", held)

    si = purchasing_services.create_self_invoice(subcontractor, receipts, user, issued_on=ISSUED)

    assert held.calls == []
    assert si.status == SelfInvoice.STATUS_DRAFT
    assert si.response == services.MESSAGE_SELF_INVOICE_NO_CONNECTION
    assert si.saved_level == "warning"


@pytest.mark.django_db
def test_screens(monkeypatch, client, user, subcontractor, customer, item, own_item, connections):
    """(e) 受注の詳細の「仕入明細」のタブ・左のメニューの「受けた仕入明細」・受けた仕入明細の詳細 (受注への link と出荷済み)。"""
    _fake, _po, order, receipts = _accepted(monkeypatch, subcontractor, item, own_item, user)
    purchasing_services.create_self_invoice(subcontractor, receipts, user, issued_on=ISSUED)
    received = ReceivedSelfInvoice.objects.get()
    client.force_login(user)

    response = client.get(reverse("orders:order_detail", args=[order.pk]))
    tabs = {tab["label"]: tab for tab in response.context["tabs"]}
    assert [row["obj"] for row in tabs["仕入明細"]["rows"]] == [received]
    body = response.content.decode()
    assert f"{received.self_invoice_no} 第 1 版 (2026-11-01〜2026-11-30) 未回答" in body
    assert "受けた仕入明細" in body and reverse("connect:receivedselfinvoice_list") in body

    body = client.get(reverse("connect:receivedselfinvoice_detail", args=[received.pk])).content.decode()
    assert reverse("orders:order_detail", args=[order.pk]) in body
    assert "出荷済み" in body and "受注の数" in body

    listing = client.get(reverse("connect:receivedselfinvoice_list")).content.decode()
    assert received.self_invoice_no in listing and "未回答" in listing
