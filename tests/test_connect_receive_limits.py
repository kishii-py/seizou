"""受け口と承認が、表の欄に入らない数と取り消した発注への出荷の知らせを断るテスト (精査 A1 の不具合 1〜5)。

出荷の知らせ・検収・注文の数が、表の欄の桁 (整数の部分と小数の桁) に入らなければ、受け口は日本語の理由で 400 を返し、
表に何も入れない (入れた後で画面を開くと 500 になるため)。受け口を通らない受信した注文 (record_inbox の道) は承認で止まる。
出荷の知らせと検収の 0 と負も 400。注文の 0 と負は今までどおり受信箱に入り、承認の時に R02 で止まる (test_r10_inbox_accept.py の形)。
1 つの DB に 2 社の役を置く形と、偽の requests.post は test_connect_reply.py と同じ (外へは HTTP を送らない)。
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

from seizou.connect import services
from seizou.connect.models import Acceptance, Inbox, Outbox, ShippingNotice
from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.masters.models import PartnerItem
from seizou.orders.models import Order
from seizou.purchasing import services as purchasing_services
from tests.test_connect_acceptance import _acceptance_raw
from tests.test_connect_reply import ORDER_ENV, REPLY_ENV, _connect, _send_order, _signed_post
from tests.test_connect_send import FakePost
from tests.test_connect_shipping_notice import _accepted, _notice_raw
from tests.test_connect_signing import _new_secret

DAY = date(2026, 11, 5)
# 桁あふれ・小数 3 桁・1e20 (表の欄に入らない) と、0・負 (0 より大きくない)
OUT_OF_FIELD = ["12345678901.5", "0.001", "1e20"]
NOT_POSITIVE = ["0", "-1"]
UNIT_PRICE_OUT_OF_FIELD = ["1234567890123.5", "0.001", "1e20"]


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
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の発注元", "role": "customer"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def own_item(user):
    return masters_services.create_item(
        {"part_no": "B-200", "name": "作り物の受ける側の部品", "unit": "個"}, user
    )


@pytest.fixture
def connections(user, subcontractor, customer, order_secret, reply_secret):
    _connect(user, subcontractor, customer)


def _order_data(**line_changes):
    """手で作る注文のデータ (send_order が作る形。明細 1 行)。返す物は (番号, 辞書)。"""
    msg_id = str(uuid.uuid4())
    line = {
        "line_no": 1,
        "part_no": "T-100",
        "part_name": "作り物の軸",
        "qty": "10.00",
        "unit": "個",
        "unit_price": "25.50",
        "due_date": "2026-11-20",
        "note": "",
    }
    line.update(line_changes)
    data = {
        "id": msg_id,
        "kind": "order",
        "sent_at": "2026-11-02T10:00:00+09:00",
        "order_no": "PO-9001",
        "order_date": "2026-11-02",
        "buyer": {"id": "a-company", "name": "作り物の発注する会社"},
        "supplier": {"code": "S001", "name": "作り物の受ける会社"},
        "note": "",
        "lines": [line],
    }
    return msg_id, data


def _raw(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _notice_for(po, outbox, shipment_no="SH-9001"):
    """送った注文 (Outbox) の番号に当てる出荷の知らせの本文。受信した注文の無い発注のため手で作る。"""
    msg_id = str(uuid.uuid4())
    data = {
        "id": msg_id,
        "kind": "shipping_notice",
        "sent_at": "2026-11-05T10:00:00+09:00",
        "order_id": str(outbox.message_id),
        "order_no": po.po_no,
        "line_no": 1,
        "supplier_order_no": "SO-9001",
        "shipment_no": shipment_no,
        "shipped_at": "2026-11-05",
        "qty": "4.00",
        "supplier": {"id": "b-company", "name": "作り物の受ける会社"},
    }
    return msg_id, _raw(data)


@pytest.mark.parametrize("qty", OUT_OF_FIELD)
@pytest.mark.django_db
def test_notice_out_of_field_is_refused(
    monkeypatch, client, user, reply_secret, subcontractor, customer, item, own_item, connections, qty
):
    _fake, po, inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    msg_id, raw = _notice_raw(inbox, qty=qty)

    response = _signed_post(client, raw, msg_id, reply_secret)

    assert response.status_code == 400
    assert response.json()["message"] == services.MESSAGE_SHIPPING_QTY_DIGITS
    assert ShippingNotice.objects.count() == 0
    client.force_login(user)
    assert client.get(reverse("purchasing:purchaseorder_detail", args=[po.pk])).status_code == 200


@pytest.mark.parametrize("qty", NOT_POSITIVE)
@pytest.mark.django_db
def test_notice_not_positive_is_refused(
    monkeypatch, client, user, reply_secret, subcontractor, customer, item, own_item, connections, qty
):
    _fake, _po, inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    msg_id, raw = _notice_raw(inbox, qty=qty)

    response = _signed_post(client, raw, msg_id, reply_secret)

    assert response.status_code == 400
    assert response.json()["message"] == services.MESSAGE_SHIPPING_QTY
    assert ShippingNotice.objects.count() == 0


@pytest.mark.parametrize("qty", OUT_OF_FIELD)
@pytest.mark.django_db
def test_acceptance_out_of_field_is_refused(
    monkeypatch, client, user, order_secret, subcontractor, customer, item, own_item, connections, qty
):
    _fake, po, _inbox, order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    msg_id, raw = _acceptance_raw(po, qty=qty)

    response = _signed_post(client, raw, msg_id, order_secret)

    assert response.status_code == 400
    assert response.json()["message"] == services.MESSAGE_ACCEPTANCE_QTY_DIGITS
    assert Acceptance.objects.count() == 0
    client.force_login(user)
    assert client.get(reverse("orders:order_detail", args=[order.pk])).status_code == 200


@pytest.mark.parametrize("qty", NOT_POSITIVE)
@pytest.mark.django_db
def test_acceptance_not_positive_is_refused(
    monkeypatch, client, user, order_secret, subcontractor, customer, item, own_item, connections, qty
):
    _fake, po, _inbox, _order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    msg_id, raw = _acceptance_raw(po, qty=qty)

    response = _signed_post(client, raw, msg_id, order_secret)

    assert response.status_code == 400
    assert response.json()["message"] == services.MESSAGE_ACCEPTANCE_QTY
    assert Acceptance.objects.count() == 0


@pytest.mark.django_db
def test_record_functions_refuse_out_of_field(
    monkeypatch, user, subcontractor, customer, item, own_item, connections
):
    _fake, po, inbox, order = _accepted(monkeypatch, subcontractor, item, own_item, user)
    po_line = po.lines.get()
    order_line = order.lines.get()

    def notice(qty, shipment_no="SH-0001"):
        return services.record_shipping_notice(
            po_line,
            qty,
            DAY,
            None,
            message_id=uuid.uuid4(),
            shipment_no=shipment_no,
            supplier_order_no="SO-0001",
        )

    def acceptance(qty):
        return services.record_acceptance(
            inbox,
            order_line,
            qty,
            DAY,
            None,
            message_id=uuid.uuid4(),
            buyer_order_no=po.po_no,
            buyer_line_no=1,
        )

    for bad in (Decimal("1e20"), Decimal("0.001")):
        with pytest.raises(ValueError) as caught:
            notice(bad)
        assert str(caught.value) == services.MESSAGE_SHIPPING_QTY_DIGITS
        with pytest.raises(ValueError) as caught:
            acceptance(bad)
        assert str(caught.value) == services.MESSAGE_ACCEPTANCE_QTY_DIGITS
    assert ShippingNotice.objects.count() == 0
    assert Acceptance.objects.count() == 0

    # 境目 (整数の部分 10 桁・小数 2 桁) と、末尾の 0 (値は同じ) は通る
    assert notice(Decimal("9999999999.99"), "SH-0001").qty == Decimal("9999999999.99")
    assert notice(Decimal("10.000"), "SH-0002").qty == Decimal("10")
    assert acceptance(Decimal("9999999999.99")).qty == Decimal("9999999999.99")
    assert acceptance(Decimal("10.000")).qty == Decimal("10")
    assert ShippingNotice.objects.count() == 2
    assert Acceptance.objects.count() == 2


@pytest.mark.parametrize(
    ("key", "value"),
    [("qty", value) for value in OUT_OF_FIELD] + [("unit_price", value) for value in UNIT_PRICE_OUT_OF_FIELD],
)
@pytest.mark.django_db
def test_order_out_of_field_is_refused_at_receive(client, order_secret, connections, key, value):
    msg_id, data = _order_data(**{key: value})

    response = _signed_post(client, _raw(data), msg_id, order_secret)

    assert response.status_code == 400
    message = response.json()["message"]
    label = "数量" if key == "qty" else "単価"
    assert message.startswith(f"注文の 1 行目の{label}は")
    assert "整数の部分" in message
    assert Inbox.objects.count() == 0


@pytest.mark.parametrize(("key", "value"), [(key, value) for key in ("qty", "unit_price") for value in NOT_POSITIVE])
@pytest.mark.django_db
def test_order_not_positive_still_reaches_inbox(client, user, order_secret, connections, own_item, key, value):
    # 注文の 0 と負は表に入らない (create_order が R02 で止める) ので、受け口では断らない (R10 の試験の形のまま)
    msg_id, data = _order_data(**{key: value})

    response = _signed_post(client, _raw(data), msg_id, order_secret)

    assert response.status_code == 200
    inbox = Inbox.objects.get()
    with pytest.raises(RuleViolation) as caught:
        services.accept(inbox, {"T-100": own_item}, user)
    assert caught.value.code == "R02"
    assert Order.objects.count() == 0


@pytest.mark.django_db
def test_order_amount_over_field_is_refused(client, order_secret, connections):
    msg_id, data = _order_data(qty="1000000", unit_price="1000000")

    response = _signed_post(client, _raw(data), msg_id, order_secret)

    assert response.status_code == 400
    assert "金額 (数量 × 単価)" in response.json()["message"]
    assert Inbox.objects.count() == 0


@pytest.mark.django_db
def test_order_at_edge_is_received(client, order_secret, connections):
    msg_id, data = _order_data(qty="9999999999.99", unit_price="1")

    response = _signed_post(client, _raw(data), msg_id, order_secret)

    assert response.status_code == 200
    assert Inbox.objects.count() == 1


@pytest.mark.django_db
def test_order_with_unreadable_number_still_reaches_inbox(client, order_secret, connections):
    # 読めない数は今までどおり受信箱に入り、承認の時に止まる (N39 の決め)
    msg_id, data = _order_data(qty="abc")

    response = _signed_post(client, _raw(data), msg_id, order_secret)

    assert response.status_code == 200
    assert Inbox.objects.count() == 1


@pytest.mark.django_db
def test_accept_refuses_out_of_field_from_record_inbox(user, customer, own_item):
    _msg_id, data = _order_data(qty="1e20")
    inbox = services.record_inbox(customer, data, user)
    mapped = PartnerItem.objects.count()

    with pytest.raises(ValueError) as caught:
        services.accept(inbox, {"T-100": own_item}, user)

    message = str(caught.value)
    assert message.startswith("注文の 1 行目の数量は")
    assert message.endswith("この注文は断り、送る側に送り直してもらってください。")
    assert Order.objects.count() == 0
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_RECEIVED
    assert PartnerItem.objects.count() == mapped


@pytest.mark.django_db
def test_notice_for_cancelled_order_is_refused(
    monkeypatch, client, user, reply_secret, subcontractor, customer, item, connections
):
    # 届かずに「作成」のまま (送った記録が 1 行) の発注を 2 つ作り、1 つ目だけ取り消す
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError()))
    cancelled = _send_order(subcontractor, item, user)
    kept = _send_order(subcontractor, item, user)
    purchasing_services.cancel_purchase_order(cancelled, user)
    msg_id, raw = _notice_for(cancelled, Outbox.objects.get(purchase_order=cancelled))

    response = _signed_post(client, raw, msg_id, reply_secret)

    assert response.status_code == 400
    assert response.json()["message"] == services.MESSAGE_SHIPPING_CANCELLED
    assert ShippingNotice.objects.count() == 0

    # 取り消していない発注への同じ形の知らせは通る (比べ)
    msg_id, raw = _notice_for(kept, Outbox.objects.get(purchase_order=kept))

    response = _signed_post(client, raw, msg_id, reply_secret)

    assert response.status_code == 200
    assert ShippingNotice.objects.count() == 1
