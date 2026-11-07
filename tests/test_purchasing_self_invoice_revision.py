"""発注元が相手の提案を受け入れ、修正仕入明細を送り、相手が修正を承諾するテスト。初見テスト R5 の候補 C111 その 5。

1 つの DB に、発注元と受ける会社の両方の役を置く (test_connect_self_invoice.py の形)。
- (a) 単価を下げる提案 (¥25.5 → ¥24) を受け入れる: 1 回目の POST は黄の確かめ (取適法のおそれ) で保存しない。「このまま保存」で版 2・1C・目的 4
- (b) 数だけを減らす提案と単価を上げる提案は、確かめを出さずに 1 回目の POST で保存する
- (c) 受ける側: 版 2 で直した明細だけ答え待ちに戻り、承諾すると AC・1CR。発注元は確認済
- (d) 版 1 の本文をもう一度受けても版 2 の行は変わらない
- (e) 直した内容を承諾 (AC) した明細の支払の見込みは、直した仕入明細の金額
相手への HTTP は requests.post を偽の物に差し替え、受けた本文を services.receive に渡す (外へは出ない)。取引先・品目・会社は作り物。
"""

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services, signing
from seizou.connect.models import Inbox, ReceivedSelfInvoice
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import Receipt, SelfInvoice
from tests.test_connect_reply import ORDER_ENV, REPLY_ENV, _connect, _into_receive, _send_order
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

DAYS = (date(2026, 11, 5), date(2026, 11, 6))
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
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "rounding": "round"}, user
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


@pytest.fixture
def sent(monkeypatch, user, subcontractor, customer, item, own_item, connections):
    """注文 (単価 25.5) を送って受け入れ、受入 2 件 (数 4・3) で仕入明細を作って送るまで。返す物は (偽の post, 仕入明細, 受けた仕入明細)。"""
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    po = _send_order(subcontractor, item, user)
    services.accept(Inbox.objects.get(), {"T-100": own_item}, user)
    line = po.lines.get()
    receipts = [purchasing_services.receive(line, Decimal(qty), day, user) for qty, day in zip(("4", "3"), DAYS)]
    si = purchasing_services.create_self_invoice(subcontractor, receipts, user, issued_on=ISSUED)
    return fake, si, ReceivedSelfInvoice.objects.get()


def _bodies(fake, kind):
    return [json.loads(call["data"].decode("utf-8")) for call in fake.calls if f'"kind":"{kind}"'.encode() in call["data"]]


def _propose(received, user, qty, price):
    """受ける側が明細 1 を提案 (数と単価) で、明細 2 を承諾で答える。"""
    services.answer_self_invoice(
        received, {1: {"answer": "propose", "qty": Decimal(qty), "unit_price": Decimal(price)}, 2: {"answer": "accept"}}, user
    )


@pytest.mark.django_db
def test_accept_price_down_needs_check(client, user, sent):
    """(a) 単価を下げる提案: 1 回目は黄の確かめで保存しない。「このまま保存」で版 2・明細を直して 1C・目的 4 で送る。受入は変わらない。"""
    fake, si, received = sent
    _propose(received, user, "4", "24")
    si.refresh_from_db()
    first_id = si.message_id
    receipts_before = list(Receipt.objects.order_by("pk").values_list("qty", "amount"))
    client.force_login(user)
    url = reverse("purchasing:selfinvoice_accept", args=[si.pk])
    body = client.get(url).content.decode()
    assert "明細 1: 単価 ¥25.5 → ¥24・金額 ¥102 → ¥96" in body
    assert "受け入れて修正仕入明細を送る" in body

    response = client.post(url, {"line_1": "on"})
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="check"' in body and "明細 1 の単価を ¥25.5 / 個 から ¥24 / 個 に下げます。" in body
    assert "違反になるおそれがあります" in body and "このまま保存" in body
    si.refresh_from_db()
    assert si.revision_no == 1

    response = client.post(url, {"line_1": "on", "confirmed": "1"})
    assert response.status_code == 302
    si.refresh_from_db()
    line = si.lines.get(line_no=1)
    assert si.revision_no == 2 and si.purpose_code == "4"
    assert line.unit_price == Decimal("24") and line.amount == Decimal("96") and line.revised_in == 2
    assert line.reply_type == "" and line.reply_unit_price is None
    assert si.subtotal == Decimal("96") + si.lines.get(line_no=2).amount
    assert si.status == SelfInvoice.STATUS_SENT and si.message_id != first_id
    revised = _bodies(fake, "self_invoice")[-1]
    assert revised["document_code"] == "1C" and revised["purpose_code"] == "4" and revised["revision_no"] == 2
    assert list(Receipt.objects.order_by("pk").values_list("qty", "amount")) == receipts_before


@pytest.mark.django_db
@pytest.mark.parametrize("qty, price", [("3", "25.5"), ("4", "26")])
def test_no_check_for_qty_down_or_price_up(client, user, sent, qty, price):
    """(b) 数だけを減らす提案と単価を上げる提案は、確かめを出さずに 1 回目の POST で保存する。"""
    _fake, si, received = sent
    _propose(received, user, qty, price)
    client.force_login(user)
    response = client.post(reverse("purchasing:selfinvoice_accept", args=[si.pk]), {"line_1": "on"})
    assert response.status_code == 302
    si.refresh_from_db()
    assert si.revision_no == 2
    line = si.lines.get(line_no=1)
    assert line.qty == Decimal(qty) and line.unit_price == Decimal(price)


@pytest.mark.django_db
def test_receiver_takes_revision_and_acknowledges(user, sent):
    """(c) 受ける側は版 2 で直した明細だけ答え待ちに戻り、ほかの承諾は残る。承諾すると AC・鑑ヘッダ AC・1CR。発注元は確認済。"""
    fake, si, received = sent
    _propose(received, user, "4", "26")
    si.refresh_from_db()
    purchasing_services.accept_self_invoice_proposals(si, [1], user)

    received.refresh_from_db()
    assert received.revision_no == 2 and received.document_code == "1C" and received.purpose_code == "4"
    assert received.status == ReceivedSelfInvoice.STATUS_RECEIVED
    rows = {line.line_no: line for line in received.lines.all()}
    assert rows[1].reply_type == "" and rows[1].revised_in == 2 and rows[1].unit_price == Decimal("26")
    assert rows[2].reply_type == "AP"

    services.answer_self_invoice(received, {1: {"answer": "accept"}}, user)
    reply = _bodies(fake, "self_invoice_response")[-1]
    assert reply["document_code"] == "1CR" and reply["reply_type"] == "AC" and reply["revision_no"] == 2
    assert {line["line_no"]: line["reply_type"] for line in reply["lines"]} == {1: "AC", 2: "AP"}
    si.refresh_from_db()
    assert si.status == SelfInvoice.STATUS_CONFIRMED
    assert si.lines.get(line_no=1).reply_type == "AC"


@pytest.mark.django_db
def test_old_revision_does_not_overwrite(user, order_secret, sent):
    """(d) 版 1 の本文をもう一度 (新しい番号で) 受けても、版 2 の行は変わらない。"""
    fake, si, received = sent
    first = _bodies(fake, "self_invoice")[-1]
    _propose(received, user, "4", "26")
    si.refresh_from_db()
    purchasing_services.accept_self_invoice_proposals(si, [1], user)
    msg_id = str(uuid.uuid4())
    raw = json.dumps({**first, "id": msg_id}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    result = services.receive(raw, signing.headers_for(order_secret, msg_id, raw))
    assert result == f"仕入明細 {si.self_invoice_no} は第 2 版まで受けてあります。"
    received.refresh_from_db()
    assert received.revision_no == 2 and received.lines.get(line_no=1).unit_price == Decimal("26")


@pytest.mark.django_db
def test_payable_uses_revised_amount(user, sent):
    """(e) 直した内容を承諾 (AC) した明細の支払の見込みは、受入の金額 (102) でなく直した仕入明細の金額 (104)。"""
    _fake, si, received = sent
    _propose(received, user, "4", "26")
    si.refresh_from_db()
    purchasing_services.accept_self_invoice_proposals(si, [1], user)
    received.refresh_from_db()
    services.answer_self_invoice(received, {1: {"answer": "accept"}}, user)
    rows = {
        row.pk: row.payable_amount for row in purchasing_services.annotate_payable(Receipt.objects.all())
    }
    first_receipt = si.lines.get(line_no=1).receipt
    assert first_receipt.amount == Decimal("102")
    assert rows[first_receipt.pk] == Decimal("104")
