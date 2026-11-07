"""相手が答える前の仕入明細を発注元が取り消し、取消を相手へ送るテスト。初見テスト R5 の候補 C111 その 6 (決め C111-3)。

1 つの DB に、発注元と受ける会社の両方の役を置く (test_connect_self_invoice.py の形)。
- (a) 小窓の確かめと取消の POST・送る本文 (1C・目的 1)・受ける側も取消
- (b) 取り消した後、同じ受入でまた仕入明細を作れる
- (c) 答えが届いた仕入明細は取り消せない (「⋯」に無い・カードは黄の 1 行だけ・POST は止まる)
- (d) 受ける側が答えを送ってあれば取消を断る (送る側に「断られた」)
- (e) 行き違い: 取り消した後に相手の前の版への答えが届く
- (f) 取消が届かない: 黄の知らせと「送り直す」(同じ番号)
- (g) 受ける側に行の無い仕入明細番号の取消は、行を作って取消で受ける
相手への HTTP は requests.post を偽の物に差し替え、受けた本文を services.receive に渡す (外へは出ない)。取引先・品目・会社は作り物。
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
from seizou.connect.models import Inbox, ReceivedSelfInvoice
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import SelfInvoice
from tests.test_connect_reply import ORDER_ENV, REPLY_ENV, _connect, _into_receive, _send_order
from tests.test_connect_self_invoice import SELF_INVOICE_KEYS
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

DAYS = (date(2026, 11, 5), date(2026, 11, 6))
ISSUED = date(2026, 11, 30)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}


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
    """注文を送って受け入れ、受入 2 件で仕入明細を作って送るまで。返す物は (偽の post, 仕入明細, 受入の並び)。"""
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    po = _send_order(subcontractor, item, user)
    services.accept(Inbox.objects.get(), {"T-100": own_item}, user)
    line = po.lines.get()
    receipts = [purchasing_services.receive(line, Decimal(qty), day, user) for qty, day in zip(("4", "3"), DAYS)]
    si = purchasing_services.create_self_invoice(subcontractor, receipts, user, issued_on=ISSUED)
    return fake, si, receipts


def _bodies(fake, kind="self_invoice"):
    return [json.loads(call["data"].decode("utf-8")) for call in fake.calls if f'"kind":"{kind}"'.encode() in call["data"]]


@pytest.mark.django_db
def test_cancel_before_reply(client, user, sent):
    """(a) 小窓に黄の 1 行と赤のボタン。POST で取消・版 2・目的 1・新しい番号。送る本文は 1C・目的 1・同じ鍵の組。受ける側も取消。"""
    fake, si, _receipts = sent
    first_id = si.message_id
    client.force_login(user)
    url = reverse("purchasing:selfinvoice_cancel", args=[si.pk])
    body = client.get(url, **MODAL).content.decode()
    assert "この仕入明細を取り消します。" in body
    assert f"{si.self_invoice_no} を取り消す" in body and "btn-error" in body

    response = client.post(url, follow=True)
    assert response.status_code == 200
    si.refresh_from_db()
    assert si.status == SelfInvoice.STATUS_CANCELLED and si.revision_no == 2 and si.purpose_code == "1"
    assert si.message_id != first_id and si.lines.count() == 2
    assert "取り消しました。" in response.content.decode()
    cancel = _bodies(fake)[-1]
    assert set(cancel) == SELF_INVOICE_KEYS
    assert cancel["document_code"] == "1C" and cancel["purpose_code"] == "1" and cancel["revision_no"] == 2

    received = ReceivedSelfInvoice.objects.get()
    assert received.status == ReceivedSelfInvoice.STATUS_CANCELLED and received.revision_no == 2
    detail = client.get(reverse("connect:receivedselfinvoice_detail", args=[received.pk])).content.decode()
    assert "得意先がこの仕入明細を取り消しました" in detail
    assert reverse("connect:receivedselfinvoice_answer", args=[received.pk]) not in detail
    assert "請求書と比べる" not in detail
    assert client.get(reverse("connect:receivedselfinvoice_answer", args=[received.pk])).status_code == 404


@pytest.mark.django_db
def test_receipts_come_back(client, user, subcontractor, sent):
    """(b) 取り消した後、同じ受入が候補に並び、新しい仕入明細 (SI-000002) を作れる。受入の一覧の「仕入明細」の列は SI-000002。"""
    _fake, si, receipts = sent
    purchasing_services.cancel_self_invoice(si, user)
    pks = set(purchasing_services.self_invoice_receipts().values_list("pk", flat=True))
    assert {r.pk for r in receipts} <= pks
    again = purchasing_services.create_self_invoice(subcontractor, receipts, user, issued_on=ISSUED)
    assert again.self_invoice_no == "SI-000002"
    client.force_login(user)
    body = client.get(reverse("purchasing:receipt_list")).content.decode()
    assert "SI-000002" in body and "SI-000001" not in body


@pytest.mark.django_db
def test_cannot_cancel_after_reply(client, user, sent):
    """(c) 答えが届いた仕入明細は「⋯」に「取消」が無く、カードは黄の 1 行だけ。POST は止まり、状態も版も変わらず何も送らない。"""
    fake, si, _receipts = sent
    received = ReceivedSelfInvoice.objects.get()
    services.answer_self_invoice(received, {1: {"answer": "accept"}}, user)
    si.refresh_from_db()
    assert si.has_reply()
    client.force_login(user)
    url = reverse("purchasing:selfinvoice_cancel", args=[si.pk])
    detail = client.get(reverse("purchasing:selfinvoice_detail", args=[si.pk])).content.decode()
    assert url not in detail
    card = client.get(url).content.decode()
    assert "取り消せません。" in card and "相手が答えた明細があります。" in card
    assert f"{si.self_invoice_no} を取り消す" not in card
    calls = len(fake.calls)
    response = client.post(url)
    assert response.status_code == 200
    assert "相手が答えた明細があります。相手と会話で話し" in response.content.decode()
    si.refresh_from_db()
    assert si.status != SelfInvoice.STATUS_CANCELLED and si.revision_no == 1
    assert len(fake.calls) == calls


@pytest.mark.django_db
def test_receiver_refuses_cancel_after_answer(monkeypatch, user, sent):
    """(d) 受ける側が答えを送ってある (送る側にはまだ届いていない) 受けた仕入明細は、取消を 400 で断り、状態は変わらない。送る側は「断られた」。"""
    _fake, si, _receipts = sent
    received = ReceivedSelfInvoice.objects.get()
    # 答えは送る側に届かない (偽の post が落ちる) ので、送る側の仕入明細は答えを知らない
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("つながらない")))
    services.answer_self_invoice(received, {1: {"answer": "accept"}, 2: {"answer": "accept"}}, user)
    si.refresh_from_db()
    assert not si.has_reply()

    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    saved = purchasing_services.cancel_self_invoice(si, user)
    received.refresh_from_db()
    assert received.status == ReceivedSelfInvoice.STATUS_CONFIRMED and received.revision_no == 1
    assert saved.status == SelfInvoice.STATUS_CANCELLED
    assert saved.delivery_state().startswith("断られた")
    assert "取り消せません。仕入明細 SI-000001 には第 1 版の答えを送ってあります。" in saved.response


@pytest.mark.django_db
def test_crossed_reply_after_cancel(client, monkeypatch, user, reply_secret, sent):
    """(e) 行き違い: 送る側が取り消した (版 2) 後に、受ける側の第 1 版への答えが届くと 200 で答えを書き、状態は取消のまま。"""
    _fake, si, _receipts = sent
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("つながらない")))
    purchasing_services.cancel_self_invoice(si, user)
    si.refresh_from_db()
    assert si.revision_no == 2

    msg_id = str(uuid.uuid4())
    data = {
        "id": msg_id,
        "kind": "self_invoice_response",
        "sent_at": "2026-11-30T10:00:00+09:00",
        "self_invoice_no": si.self_invoice_no,
        "revision_no": 1,
        "document_code": "1R",
        "reply_type": "AP",
        "supplier": {"id": "b-company", "name": "作り物の受ける会社"},
        "lines": [{"line_no": 1, "reply_type": "AP", "reason_code": "", "reply_qty": None, "reply_unit_price": None, "note": ""}],
    }
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    result = services.receive(raw, signing.headers_for(reply_secret, msg_id, raw))
    assert not isinstance(result, str)
    si.refresh_from_db()
    assert si.status == SelfInvoice.STATUS_CANCELLED
    assert si.lines.get(line_no=1).reply_type == "AP" and str(si.reply_message_id) == msg_id
    client.force_login(user)
    body = client.get(reverse("purchasing:selfinvoice_detail", args=[si.pk])).content.decode()
    assert "取り消した後に、相手の第 1 版への答えが届きました。" in body


@pytest.mark.django_db
def test_cancel_undelivered(client, monkeypatch, user, sent):
    """(f) 取消が届かない: 状態は取消、黄の知らせ「取消は相手に届いていません。」、詳細に「送り直す」。送り直しは同じ番号。"""
    _fake, si, _receipts = sent
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("つながらない")))
    saved = purchasing_services.cancel_self_invoice(si, user)
    assert saved.status == SelfInvoice.STATUS_CANCELLED
    assert saved.saved_level == "warning" and saved.saved_note.startswith("取消は相手に届いていません。")
    cancel_id = saved.message_id
    client.force_login(user)
    body = client.get(reverse("purchasing:selfinvoice_detail", args=[si.pk])).content.decode()
    assert "この仕入明細は取り消しました (第 2 版で取消を送りました)。" in body
    assert reverse("purchasing:selfinvoice_resend", args=[si.pk]) in body

    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    client.post(reverse("purchasing:selfinvoice_resend", args=[si.pk]))
    assert fake.calls[-1]["headers"][signing.HEADER_ID] == str(cancel_id)
    assert ReceivedSelfInvoice.objects.get().status == ReceivedSelfInvoice.STATUS_CANCELLED


@pytest.mark.django_db
def test_cancel_without_earlier_row(monkeypatch, user, order_secret, sent):
    """(g) 受ける側に行の無い仕入明細番号の取消は、行を作って状態を取消で受ける。"""
    fake, si, _receipts = sent
    first = _bodies(fake)[-1]
    ReceivedSelfInvoice.objects.all().delete()
    msg_id = str(uuid.uuid4())
    data = {**first, "id": msg_id, "revision_no": 2, "document_code": "1C", "purpose_code": "1"}
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    result = services.receive(raw, signing.headers_for(order_secret, msg_id, raw))
    assert not isinstance(result, str)
    received = ReceivedSelfInvoice.objects.get()
    assert received.status == ReceivedSelfInvoice.STATUS_CANCELLED and received.revision_no == 2
    assert received.lines.count() == 2
