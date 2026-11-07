"""受ける側が仕入明細の明細ごとに答え、発注元が答えを受け、請求書と並べて見るテスト。初見テスト R5 の候補 C111 その 4。

1 つの DB に、注文を送る会社 (発注元) と受ける会社の両方の役を置く (test_connect_self_invoice.py の形)。
仕入明細回答は注文回答と同じ向き (得意先 C001 への「送る」→ 協力企業 S001 からの「受ける」) を通る。
- (a) 全部「承諾」(既定のまま送る)
- (b) 提案・確認できない・承諾が混ざる
- (c) 確認できない明細を答え直す (2 回目は 1CR)。承諾した明細は答える画面に出ず、services も受けない (決め C111-4)
- (d) form の誤り
- (e) 発注元の受け口が断る形と、同じ番号の 2 回目
- (f) 受けた仕入明細の詳細の「請求書と比べる」
相手への HTTP は requests.post を偽の物に差し替え、受けた本文を services.receive に渡す (外へは出ない)。取引先・品目・会社は作り物。
"""

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.billing.models import Invoice
from seizou.connect import services, signing
from seizou.connect.models import Inbox, ReceivedSelfInvoice
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import SelfInvoice
from tests.test_connect_reply import ORDER_ENV, REPLY_ENV, _connect, _into_receive, _send_order
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

#: 仕入明細回答の本文の頭の鍵 (C110 の docs/edi-items.md の試験が import する)
SELF_INVOICE_RESPONSE_KEYS = {"id", "kind", "sent_at", "self_invoice_no", "revision_no", "document_code", "reply_type", "supplier", "lines"}
#: 仕入明細回答の明細 1 つの鍵
SELF_INVOICE_RESPONSE_LINE_KEYS = {"line_no", "reply_type", "reason_code", "reply_qty", "reply_unit_price", "note"}
DAYS = (date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 7))
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
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


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


@pytest.fixture
def sent(monkeypatch, user, subcontractor, customer, item, own_item, connections):
    """注文を送って受け入れ、受入 3 件 (数 4・3・2) で仕入明細を作って送るまで。返す物は (偽の post, 仕入明細, 受けた仕入明細)。"""
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    po = _send_order(subcontractor, item, user)
    services.accept(Inbox.objects.get(), {"T-100": own_item}, user)
    line = po.lines.get()
    receipts = [purchasing_services.receive(line, Decimal(qty), day, user) for qty, day in zip(("4", "3", "2"), DAYS)]
    si = purchasing_services.create_self_invoice(subcontractor, receipts, user, issued_on=ISSUED)
    return fake, si, ReceivedSelfInvoice.objects.get()


def _answer_data(received):
    """答える画面に送る値 (全部の明細が承諾 = 既定のまま。数と単価は仕入明細の値)。"""
    data = {}
    for line in received.lines.all():
        number = line.line_no
        data.update({
            f"line_{number}_answer": "accept",
            f"line_{number}_qty": str(line.qty),
            f"line_{number}_unit_price": str(line.unit_price),
            f"line_{number}_reason": "",
            f"line_{number}_note": "",
        })
    return data


def _replies(fake):
    return [json.loads(call["data"].decode("utf-8")) for call in fake.calls if b'"self_invoice_response"' in call["data"]]


@pytest.mark.django_db
def test_accept_all(client, user, sent):
    """(a) 全部「承諾」: 受ける側は確認済、本文は AP・1R、発注元は確認済で確認済の金額が小計と同じ。"""
    fake, si, received = sent
    client.force_login(user)
    response = client.post(reverse("connect:receivedselfinvoice_answer", args=[received.pk]), _answer_data(received))
    assert response.status_code == 302

    received.refresh_from_db()
    assert received.status == ReceivedSelfInvoice.STATUS_CONFIRMED
    (body,) = _replies(fake)
    assert set(body) == SELF_INVOICE_RESPONSE_KEYS
    assert all(set(line) == SELF_INVOICE_RESPONSE_LINE_KEYS for line in body["lines"])
    assert body["reply_type"] == "AP" and body["document_code"] == "1R"
    assert [line["reply_type"] for line in body["lines"]] == ["AP", "AP", "AP"]
    assert received.reply_sent_at is not None

    si.refresh_from_db()
    assert si.status == SelfInvoice.STATUS_CONFIRMED
    assert si.reply_type == "AP"
    assert [line.reply_type for line in si.lines.order_by("line_no")] == ["AP", "AP", "AP"]
    assert si.confirmed_subtotal() == si.subtotal


@pytest.mark.django_db
def test_mixed_answers(client, user, sent):
    """(b) 明細 1 を提案 (数 4 → 3)・明細 2 を確認できない (単価が違う)・明細 3 を承諾: 鑑ヘッダ CA・発注元は確かめ中。"""
    fake, si, received = sent
    client.force_login(user)
    data = _answer_data(received)
    data.update({"line_1_answer": "propose", "line_1_qty": "3", "line_2_answer": "reject", "line_2_reason": "103"})
    response = client.post(reverse("connect:receivedselfinvoice_answer", args=[received.pk]), data)
    assert response.status_code == 302

    (body,) = _replies(fake)
    assert body["reply_type"] == "CA"
    lines = {line["line_no"]: line for line in body["lines"]}
    assert lines[1]["reply_type"] == "UV" and lines[1]["reply_qty"] == "3.00"
    assert lines[2]["reply_type"] == "RE" and lines[2]["reason_code"] == "103" and lines[2]["reply_qty"] is None
    assert lines[3]["reply_type"] == "AP" and lines[3]["reason_code"] == ""

    si.refresh_from_db()
    assert si.status == SelfInvoice.STATUS_PARTIAL
    rows = {line.line_no: line for line in si.lines.all()}
    assert rows[1].reply_type == "UV" and rows[1].reply_qty == Decimal("3")
    assert rows[2].reply_type == "RE" and rows[2].reply_reason_code == "103"
    body = client.get(reverse("purchasing:selfinvoice_detail", args=[si.pk])).content.decode()
    assert "提案: 数 4 → 3" in body
    assert "確認できない: 単価が違う" in body
    assert "相手に答え直してもらってください" in body
    assert "確認済の金額" in body and "答え待ち・確かめ中の金額" in body


@pytest.mark.django_db
def test_answer_again_and_accepted_lines(client, user, sent):
    """(c) 確認できない明細を答え直して提案にできる (2 回目は 1CR)。承諾した明細は答える画面に出ず、services も受けない。"""
    fake, si, received = sent
    client.force_login(user)
    data = _answer_data(received)
    data.update({"line_2_answer": "reject", "line_2_reason": "103"})
    client.post(reverse("connect:receivedselfinvoice_answer", args=[received.pk]), data)
    received.refresh_from_db()
    assert received.status == ReceivedSelfInvoice.STATUS_PARTIAL

    form = client.get(reverse("connect:receivedselfinvoice_answer", args=[received.pk])).context["form"]
    assert [line.line_no for line, _names in form.answer_lines] == [2]
    response = client.post(
        reverse("connect:receivedselfinvoice_answer", args=[received.pk]),
        {"line_2_answer": "propose", "line_2_qty": "3", "line_2_unit_price": "24", "line_2_reason": "", "line_2_note": "単価は 24 円の約束"},
    )
    assert response.status_code == 302
    first, second = _replies(fake)
    assert first["document_code"] == "1R" and second["document_code"] == "1CR"
    assert first["id"] != second["id"]
    assert [line["line_no"] for line in second["lines"]] == [1, 2, 3]
    si.refresh_from_db()
    row = si.lines.get(line_no=2)
    assert row.reply_type == "UV" and row.reply_unit_price == Decimal("24") and row.reply_note == "単価は 24 円の約束"

    calls = len(fake.calls)
    with pytest.raises(ValueError) as caught:
        services.answer_self_invoice(received, {1: {"answer": "reject", "reason": "102"}}, user)
    assert str(caught.value).startswith("送れません。明細 1 は承諾してあります。")
    assert len(fake.calls) == calls
    assert received.lines.get(line_no=1).reply_type == "AP"


@pytest.mark.django_db
def test_form_errors(client, user, sent):
    """(d) 確認できないで訳が空・提案で数も単価も同じ → 欄の下の赤い字と止まった知らせ。何も送らない。"""
    fake, _si, received = sent
    client.force_login(user)
    calls = len(fake.calls)
    data = _answer_data(received)
    data.update({"line_1_answer": "reject", "line_1_reason": "", "line_2_answer": "propose"})
    response = client.post(reverse("connect:receivedselfinvoice_answer", args=[received.pk]), data)
    assert response.status_code == 200
    body = response.content.decode()
    assert "明細 1 の確認できない訳を選んでください。" in body
    assert "明細 2 の提案が仕入明細と同じです。承諾にしてください。" in body
    assert "送れません。" in body
    assert len(fake.calls) == calls
    received.refresh_from_db()
    assert received.reply_message_id is None


def _reply_raw(si, **changes):
    data = {
        "kind": "self_invoice_response",
        "sent_at": "2026-11-30T10:00:00+09:00",
        "self_invoice_no": si.self_invoice_no,
        "revision_no": si.revision_no,
        "document_code": "1R",
        "reply_type": "AP",
        "supplier": {"id": "b-company", "name": "作り物の受ける会社"},
        "lines": [{"line_no": 1, "reply_type": "AP", "reason_code": "", "reply_qty": None, "reply_unit_price": None, "note": ""}],
    }
    data.update(changes)
    return data


def _receive(secret, data, msg_id=None):
    msg_id = msg_id or str(uuid.uuid4())
    raw = json.dumps({**data, "id": msg_id}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return services.receive(raw, signing.headers_for(secret, msg_id, raw))


@pytest.mark.django_db
def test_buyer_refuses(user, reply_secret, sent):
    """(e) 発注元の受け口は、違う版・知らない行番号・知らない答えのコードを断る。同じ番号の 2 回目は何もしない。"""
    _fake, si, _received = sent
    assert "今の版 (第 1 版) と違います" in _receive(reply_secret, _reply_raw(si, revision_no=2))
    assert "行番号 9" in _receive(reply_secret, _reply_raw(si, lines=[{"line_no": 9, "reply_type": "AP", "reason_code": "", "reply_qty": None, "reply_unit_price": None, "note": ""}]))
    assert isinstance(_receive(reply_secret, _reply_raw(si, reply_type="ZZ")), str)
    assert isinstance(_receive(reply_secret, _reply_raw(si, lines=[{"line_no": 1, "reply_type": "ZZ", "reason_code": "", "note": ""}])), str)
    assert "500 字まで" in _receive(reply_secret, _reply_raw(si, lines=[{"line_no": 1, "reply_type": "AP", "reason_code": "", "note": "字" * 501}]))
    si.refresh_from_db()
    assert si.reply_message_id is None and si.status == SelfInvoice.STATUS_SENT

    msg_id = str(uuid.uuid4())
    first = _receive(reply_secret, _reply_raw(si), msg_id)
    assert not isinstance(first, str)
    si.refresh_from_db()
    replied_at = si.replied_at
    again = _receive(reply_secret, _reply_raw(si, lines=[{"line_no": 1, "reply_type": "RE", "reason_code": "102", "note": ""}]), msg_id)
    assert not isinstance(again, str)
    si.refresh_from_db()
    assert si.replied_at == replied_at
    assert si.lines.get(line_no=1).reply_type == "AP"


@pytest.mark.django_db
def test_compare_with_invoices(client, user, customer, sent):
    """(f) 受けた仕入明細の詳細の「請求書と比べる」: 期間の重なる請求書の番号と小計と差。重ならない請求書と取消の請求書は出ない。"""
    _fake, _si, received = sent
    client.force_login(user)
    services.answer_self_invoice(received, {n: {"answer": "accept"} for n in (1, 2, 3)}, user)
    received.refresh_from_db()

    def invoice(number, start, end, subtotal, status=Invoice.STATUS_ISSUED):
        return Invoice.objects.create(
            invoice_no=number, customer=customer, issued_on=end, period_from=start, period_to=end,
            subtotal=Decimal(subtotal), tax=Decimal("0"), total=Decimal(subtotal), status=status,
        )

    invoice("IV-900001", date(2026, 11, 1), date(2026, 11, 30), "200")
    invoice("IV-900002", date(2026, 10, 1), date(2026, 10, 31), "999")
    invoice("IV-900003", date(2026, 11, 1), date(2026, 11, 30), "888", Invoice.STATUS_CANCELLED)
    body = client.get(reverse("connect:receivedselfinvoice_detail", args=[received.pk])).content.decode()
    assert "請求書と比べる" in body
    assert "IV-900001" in body and "IV-900002" not in body and "IV-900003" not in body
    confirmed = sum((line.amount for line in received.lines.all()), Decimal("0"))
    assert confirmed != Decimal("200")
    assert "仕入明細の期間 (受領日) と請求書の締め期間 (出荷日) がずれると差が出ます。" in body
