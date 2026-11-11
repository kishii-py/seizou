"""送った発注を取消として相手へ送る (中小企業共通 EDI の注文書目的コード 1 打切り。C96 その 5)。

1 つの DB に注文を送る会社 (A = 協力企業 S001 へ発注する) と受ける会社 (B = 得意先 C001 から受注する) の両方の役を置く
(tests/test_connect_reply.py の形)。相手への HTTP は requests.post を偽の物に差し替え、受けた本文を受け口に渡すか、決めた返事を返す。
取引先と品目は作り物。日は固定の値を渡す。取適法の確かめ (purchasing.checks の LawOrderCancel) は黄の知らせで、「このまま取り消す」(confirmed=1) で通す。
"""

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect.models import Inbox, Outbox
from seizou.core.screens import all_screens
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder
from tests.test_connect_reply import ORDER_ENV, REPLY_ENV, _connect, _into_receive
from tests.test_connect_revision_protocol import _signed_receive
from tests.test_connect_send import FakePost, FakeResponse
from tests.test_connect_signing import _new_secret

DAY = date(2026, 11, 6)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


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
def items(user):
    buyer = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    own = masters_services.create_item({"part_no": "B-100", "name": "作り物の受ける側の軸", "unit": "個"}, user)
    return buyer, own


@pytest.fixture
def connections(user, subcontractor, customer, order_secret, reply_secret):
    _connect(user, subcontractor, customer)


@pytest.fixture
def fake(monkeypatch, connections):
    post = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", post)
    return post


@pytest.fixture
def order(subcontractor, items, user, fake):
    buyer, _own = items
    po = services.create_purchase_order(
        subcontractor,
        [{"item": buyer, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 20)}],
        user,
        order_date=date(2026, 11, 2),
    )
    assert po.status == PurchaseOrder.STATUS_SENT
    return po


def _cancel_url(po):
    return reverse("purchasing:purchaseorder_cancel", args=[po.pk])


def _detail(client, po):
    return client.get(reverse("purchasing:purchaseorder_detail", args=[po.pk])).content.decode()


def _last_order_body(fake):
    bodies = [json.loads(call["data"].decode("utf-8")) for call in fake.calls]
    return [body for body in bodies if body["kind"] == "order"][-1]


@pytest.mark.django_db
def test_cancel_sent_order_warns_then_sends_cancel_revision(client_logged_in, order, items, fake, user):
    response = client_logged_in.post(_cancel_url(order), {"reason": "作り物の取消の理由"})
    body = response.content.decode()
    assert response.status_code == 200
    # 右上の黄の知らせ (1 文目は太字の span、2 文目から後ろはその後)
    assert '<span class="font-semibold">送った発注を取り消します。</span> 取適法の対象の取引では、相手に責めの無い取消や変更は' in body
    # 取消の画面の黄の後のボタンは「このまま取り消す」(N403b)。知らせの文の末尾もボタンの字に合わせる (本体の直し 488 回目)
    assert "相手と話して決めた後なら、このまま取り消してください。" in body
    assert "このまま取り消す" in body
    assert "このまま保存" not in body
    order.refresh_from_db()
    assert order.status == PurchaseOrder.STATUS_SENT

    response = client_logged_in.post(_cancel_url(order), {"reason": "作り物の取消の理由", "confirmed": "1"})
    assert response.status_code == 302
    order.refresh_from_db()
    assert (order.status, order.revision_no, order.purpose_code) == (PurchaseOrder.STATUS_CANCELLED, 2, "1")
    assert set(order.lines.values_list("line_status_code", flat=True)) == {"2"}
    sent = _last_order_body(fake)
    assert (sent["purpose_code"], sent["order_revision_no"]) == ("1", 2)
    assert [line["line_status_code"] for line in sent["lines"]] == ["2"]

    # 受ける側で受け入れると受注が取消になる
    base = Inbox.objects.get(revises=None)
    _buyer, own = items
    revision = Inbox.objects.get(revises=base)
    assert revision.purpose_code == "1"
    connect_services.accept(base, {"T-100": own}, user)
    received_order = connect_services.accept(revision, {}, user)
    received_order.refresh_from_db()
    assert received_order.status == received_order.STATUS_CANCELLED


@pytest.mark.django_db
def test_undelivered_cancel_shows_alert_and_resend(client_logged_in, order, monkeypatch):
    monkeypatch.setattr("requests.post", FakePost(reply=FakeResponse(503)))
    client_logged_in.post(_cancel_url(order), {"reason": "", "confirmed": "1"})
    order.refresh_from_db()
    assert order.status == PurchaseOrder.STATUS_CANCELLED
    body = _detail(client_logged_in, order)
    assert "第 2 版の取消は相手に届いていません。" in body
    assert reverse("purchasing:purchaseorder_send", args=[order.pk]) in body

    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    response = client_logged_in.post(reverse("purchasing:purchaseorder_send", args=[order.pk]), follow=True)
    assert "相手に取消を送りました。" in response.content.decode()
    assert json.loads(fake.calls[-1]["data"].decode("utf-8"))["purpose_code"] == "1"
    assert reverse("purchasing:purchaseorder_send", args=[order.pk]) not in _detail(client_logged_in, order)


@pytest.mark.django_db
def test_partly_shipped_order_is_refused_without_warning(client_logged_in, order):
    connect_services.record_shipping_notice(
        order.lines.get(), Decimal("2"), DAY, None, message_id=uuid.uuid4(), shipment_no="SH-1"
    )
    assert _cancel_url(order) in _detail(client_logged_in, order)
    response = client_logged_in.post(_cancel_url(order), {"reason": ""})
    body = response.content.decode()
    assert "取り消せません。" in body and "相手が一部を出荷済みなので、取消は送れません。" in body
    assert "「変更」で送ってください。" in body
    assert "送った発注を取り消します。" not in body
    order.refresh_from_db()
    assert (order.status, order.revision_no) == (PurchaseOrder.STATUS_SENT, 1)


@pytest.mark.django_db
def test_draft_cancel_is_not_sent_and_not_warned(client_logged_in, user, items, monkeypatch):
    partner = masters_services.create_partner({"code": "S002", "name": "作り物のつながりの無い協力企業", "role": "subcontractor"}, user)

    def fail(*args, **kwargs):
        raise AssertionError("下書きの取消で HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)
    buyer, _own = items
    draft = services.create_purchase_order(
        partner, [{"item": buyer, "qty": Decimal("1"), "unit_price": Decimal("1")}], user, order_date=DAY
    )
    response = client_logged_in.post(_cancel_url(draft), {"reason": ""})
    assert response.status_code == 302
    draft.refresh_from_db()
    assert (draft.status, draft.revision_no, draft.purpose_code) == (PurchaseOrder.STATUS_CANCELLED, 1, "9")


@pytest.mark.django_db
def test_first_reply_after_cancel_is_not_refused(order, user, reply_secret):
    first = Outbox.objects.filter(purchase_order=order, revision_no=1).order_by("pk").first()
    services.cancel_purchase_order(order, user)
    order.refresh_from_db()
    assert order.status == PurchaseOrder.STATUS_CANCELLED
    data = {
        "id": str(uuid.uuid4()),
        "kind": "order_reply",
        "sent_at": "2026-11-03T10:00:00+09:00",
        "order_id": str(first.message_id),
        "order_no": order.po_no,
        "order_revision_no": 1,
        "reply_type": "AP",
        "supplier_order_no": "SO-0001",
        "reason": "",
        "supplier": {"id": "b-company", "name": "作り物の受ける会社"},
    }
    receipt = _signed_receive(data, reply_secret)
    assert not isinstance(receipt, str)
    order.refresh_from_db()
    assert order.status == PurchaseOrder.STATUS_CANCELLED
    assert order.reply_message_id == uuid.UUID(data["id"])
    assert order.replied_at is not None


@pytest.mark.django_db
def test_cancel_screen_usecase_has_no_law_paragraph():
    screens = {screen.url_name: screen for screen in all_screens()}
    usecase = screens["purchasing:purchaseorder_cancel"].usecase
    assert "使う人: 生産管理" in usecase
    assert "取適法" not in usecase
