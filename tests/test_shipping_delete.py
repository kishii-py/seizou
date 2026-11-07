"""打ち間違えた出荷を消す道 (shipping.services.delete_shipment と delete_refusal。本人の決め 11-08 00:23・00:25。指示書の候補 12)。

消すと出荷の行が無くなり、受注の状態と在庫が出荷の前に戻り、変更の履歴に「消した」の 1 行が残る。
発行の納品書に入った出荷・請求書の明細か請求書の外の入金が指す出荷・取引先につながる出荷 (受信した注文の出荷・送った出荷の知らせ・
届いた検収)・明細を出し終えた出荷は、日本語の ValueError で断り、何も変えない (字は delete_refusal の 1 か所)。
作り物の得意先・部品・受注だけを使う。相手への HTTP は偽物 (呼ばれたら落とす) にする。
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from seizou.attachments import services as attachments_services
from seizou.attachments.models import Attachment
from seizou.billing import services as billing_services
from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.core import access
from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.models import Shipment
from seizou.stock import services as stock_services

ORDER_DATE = date(2026, 11, 2)
COUNTED_ON = date(2026, 11, 3)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("出荷を消すときに相手への HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _order(customer, item, user, *, source="manual"):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
        source=source,
    )


@pytest.fixture
def line(customer, item, user):
    """手入力の受注 (数量 10) の明細。"""
    return _order(customer, item, user).lines.get()


def _ship(line, qty, user, **kwargs):
    return services.ship(line, Decimal(qty), SHIPPED_AT, user, **kwargs)


def _deleted_logs(pk):
    return ChangeLog.objects.filter(owner_model="shipping.shipment", owner_pk=pk, action=ChangeLog.ACTION_DELETED)


def _assert_refused(shipment, user, words):
    """消せない出荷: 断りの字は delete_refusal と同じで、出荷も受注の状態も履歴の行も変わらない。"""
    shipment.refresh_from_db()
    order = shipment.order_line.order
    order.refresh_from_db()
    status = order.status
    logs = ChangeLog.objects.count()
    refusal = services.delete_refusal(shipment)
    assert refusal.startswith("削除できません。")
    assert shipment.shipment_no in refusal
    assert words in refusal

    with pytest.raises(ValueError) as caught:
        services.delete_shipment(shipment, user)

    assert str(caught.value) == refusal
    assert Shipment.objects.filter(pk=shipment.pk).exists()
    order.refresh_from_db()
    assert order.status == status
    assert ChangeLog.objects.count() == logs
    return refusal


# ---------------------------------------------------------------- (a) 消すと出荷の前に戻る


@pytest.mark.django_db
def test_delete_returns_order_and_stock_to_before(user, item, line):
    stock_services.adjust(item, Decimal("50"), COUNTED_ON, user)
    first = _ship(line, "4", user)
    second = _ship(line, "3", user)
    assert stock_services.balance(item) == Decimal("43")
    first_pk, number = first.pk, first.shipment_no

    result = services.delete_shipment(first, user)

    assert result.pk is None
    assert result.saved_message == f"出荷 {number} を削除しました。"
    assert not Shipment.objects.filter(pk=first_pk).exists()
    order = line.order
    order.refresh_from_db()
    assert order.status == order.STATUS_PARTIAL  # ほかの出荷 (3) が残るので一部出荷
    assert stock_services.balance(item) == Decimal("47")
    logs = list(_deleted_logs(first_pk))
    assert len(logs) == 1
    assert number in logs[0].object_text

    services.delete_shipment(second, user)

    order.refresh_from_db()
    assert order.status == order.STATUS_OPEN  # 出荷が無くなったので受注に戻る
    assert stock_services.balance(item) == Decimal("50")
    assert orders_services.remaining_qty(line) == Decimal("10")


# ---------------------------------------------------------------- (b) 消せない出荷と、消せる取消の納品書の出荷


@pytest.mark.django_db
def test_issued_note_is_refused(user, customer, line):
    shipment = _ship(line, "4", user)
    note = services.create_delivery_note(customer, [shipment], user)

    refusal = _assert_refused(shipment, user, f"納品書 {note.note_no} に入っています")
    assert "金額を見られる人" in refusal


@pytest.mark.django_db
def test_cancelled_note_shipment_can_be_deleted(user, customer, line):
    shipment = _ship(line, "4", user)
    note = services.create_delivery_note(customer, [shipment], user)
    services.cancel_delivery_note(note, user)
    shipment.refresh_from_db()

    assert services.delete_refusal(shipment) == ""
    services.delete_shipment(shipment, user)

    assert not Shipment.objects.filter(pk=shipment.pk).exists()


@pytest.mark.django_db
def test_invoice_line_is_refused_even_after_cancel(user, customer, line):
    shipment = _ship(line, "4", user)
    note = services.create_delivery_note(customer, [shipment], user)
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    _assert_refused(shipment, user, "納品書")  # 発行の請求書の出荷は発行の納品書にも入っている (先に納品書の文)

    billing_services.cancel_invoice(invoice, user)
    services.cancel_delivery_note(note, user)

    _assert_refused(shipment, user, "請求書の明細か請求書の外の入金の記録が指しています")


@pytest.mark.django_db
def test_outside_payment_is_refused_even_after_cancel(user, customer, line):
    shipment = _ship(line, "4", user)
    note = services.create_delivery_note(customer, [shipment], user)
    payment = billing_services.record_outside_payment(shipment, SHIPPED_AT, Decimal("400"), user)
    billing_services.cancel_payment(payment, user)
    services.cancel_delivery_note(note, user)

    _assert_refused(shipment, user, "請求書の明細か請求書の外の入金の記録が指しています")


@pytest.mark.django_db
def test_sent_notice_is_refused(user, line):
    shipment = _ship(line, "4", user)
    services.record_notice_response(shipment, "作り物の返事", user, sent=True)

    _assert_refused(shipment, user, "取引先につながる記録")


@pytest.mark.django_db
def test_acceptance_on_line_is_refused(user, customer, line):
    """手入力の受注の明細に検収を 1 行置く (入口は手入力のままなので、受信した注文の出荷の断りには当たらない)。"""
    shipment = _ship(line, "4", user)
    inbox = Inbox.objects.create(
        message_id=uuid.uuid4(), partner=customer, kind="order", payload={}, status="accepted", order=line.order
    )
    connect_services.record_acceptance(
        inbox, line, Decimal("4"), SHIPPED_AT, None, message_id=uuid.uuid4(), buyer_order_no="PO-0001", buyer_line_no=line.line_no
    )

    refusal = _assert_refused(shipment, user, "取引先につながる記録")
    assert "相手に会話で伝えてください" in refusal


@pytest.mark.django_db
def test_inbox_order_shipment_is_refused(user, customer, item):
    """受信した注文で作った受注の出荷 (種の読み込みと同じく知らせを送らない形で作るので、送った日時は空)。"""
    order = _order(customer, item, user, source="inbox")
    Inbox.objects.create(
        message_id=uuid.uuid4(), partner=customer, kind="order", payload={"order_no": "PO-0001"}, status="accepted", order=order
    )
    shipment = _ship(order.lines.get(), "4", user, send=False)
    assert shipment.notice_sent_at is None

    _assert_refused(shipment, user, "取引先につながる記録")


@pytest.mark.django_db
def test_finishing_shipment_is_refused(user, line):
    shipment = _ship(line, "10", user)
    assert shipment.finishes_line

    refusal = _assert_refused(shipment, user, "明細を出し終えた出荷です")
    assert "「編集」で直してください" in refusal


# ---------------------------------------------------------------- (c) 2 回目


@pytest.mark.django_db
def test_delete_twice_is_refused(user, line):
    shipment = _ship(line, "4", user)
    services.delete_shipment(shipment, user)

    with pytest.raises(ValueError, match="削除できません。この出荷は既に消してあります。"):
        services.delete_shipment(shipment, user)
    assert _deleted_logs(shipment.pk).count() == 1


# ---------------------------------------------------------------- (d) 添付 (今の動き = 付けた先の無い行が残る)


@pytest.mark.django_db
def test_attachment_row_stays_after_delete(settings, tmp_path, user, line):
    settings.MEDIA_ROOT = tmp_path / "media"
    shipment = _ship(line, "4", user)
    attachment = attachments_services.add_attachment(shipment, SimpleUploadedFile("作り物の写真.png", b"png"), user, kind="photo")
    shipment_pk = shipment.pk

    services.delete_shipment(shipment, user)

    attachment = Attachment.objects.get(pk=attachment.pk)
    assert attachment.object_id == shipment_pk
    assert attachment.target is None


# ---------------------------------------------------------------- (e) 権限の表


def test_delete_action_is_shipping_kind_and_not_money():
    assert access.ACTION_KINDS["shipping.delete_shipment"] == access.ACTION_KINDS["shipping.update_shipment"] == "shipping"
    assert "shipping.delete_shipment" not in access.MONEY_ACTIONS
