"""出荷の削除の断りの見る順 (shipping.services.delete_refusal。開発の記録 docs/nights/20261111/N377.md の (3))。

請求書の外の入金が指す出荷は、入金を取り消しても指したまま残るので、発行の納品書に入っていても
初めから入金の文で断る (納品書を取り消すよう案内した後に、別の訳で断らないため)。
発行の納品書だけに入った出荷は今までどおり納品書の文。
請求書の明細が指す出荷は断らない (依頼書 N403c。本人の決め 11-11 12:5x〜13:0x = 請求済みの出荷の直しと削除は止めない)。
発行の納品書に入っていれば納品書の文で断り、納品書を取り消した後は消せて、検査の層の R27 が知らせる。
作り物の得意先・部品・受注だけを使う。相手への HTTP は偽物 (呼ばれたら落とす) にする。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.core import checks
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.models import Shipment

SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
#: 請求書の外の入金が指す出荷の断りの字 (依頼書 N403c で「請求書の明細か請求書の外の入金の記録」から、入金だけを言う形にした)
PAYMENT_TEXT = "請求書の外で受け取った入金の記録が指しています"
#: 前の請求書の明細の断りの字 (依頼書 N403c の後は、どの断りにも出ない)
OLD_INVOICE_TEXT = "請求書の明細か請求書の外の入金の記録が指しています"
NOTE_NEXT_STEP = "先に納品書を取り消してください"


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("出荷を消すときに相手への HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def line(customer, user):
    """手入力の受注 (数量 10・単価 100) の明細。"""
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=date(2026, 11, 2),
    )
    return order.lines.get()


def _noted(customer, line, user):
    """発行の納品書に入った出荷 (数量 4)。"""
    shipment = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    return shipment


def _refusal_and_refused(shipment, user):
    """断りの字を返す。delete_shipment も同じ字で断り、出荷は残る。"""
    refusal = services.delete_refusal(shipment)
    assert refusal.startswith("削除できません。")
    assert shipment.shipment_no in refusal
    with pytest.raises(ValueError) as caught:
        services.delete_shipment(shipment, user)
    assert str(caught.value) == refusal
    assert Shipment.objects.filter(pk=shipment.pk).exists()
    return refusal


@pytest.mark.django_db
def test_invoiced_shipment_gets_note_text_then_can_be_deleted(customer, line, user):
    """(a) 発行の納品書と発行の請求書に入った出荷は、納品書の文で断る (請求書の文は無い)。納品書を取り消すと delete_refusal は空 (消せる) で、
    検査の層の R27 が「消しても、請求書の明細…は残ります」で知らせる。

    前は「最初の断りは請求書の文」だった。期待を変えた訳: 本人の決め 11-11 12:5x〜13:0x (請求済みの出荷の直しと削除は止めない) で、
    請求書の明細が出荷番号と出荷日を写して残し、請求書に入った出荷を消せるようにしたため (依頼書 N403c)。
    """
    shipment = _noted(customer, line, user)
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)

    refusal = _refusal_and_refused(shipment, user)

    assert NOTE_NEXT_STEP in refusal
    assert OLD_INVOICE_TEXT not in refusal and PAYMENT_TEXT not in refusal

    services.cancel_delivery_note(shipment.delivery_note, user)
    shipment.refresh_from_db()

    assert services.delete_refusal(shipment) == ""
    warnings = checks.run("shipping.delete_shipment", {"object": shipment}, user)
    assert [warning.code for warning in warnings] == ["R27"]
    assert f"請求書 {invoice.invoice_no} に入っています。消しても、請求書の明細 (出荷番号と出荷日の写し) は残ります。" in warnings[0].message


@pytest.mark.django_db
def test_outside_payment_shipment_in_issued_note_gets_invoice_text_first(customer, line, user):
    """(b) 発行の納品書に入り、請求書の外の入金 (取消でない) を記録した出荷は、初めから入金の文 (断りの字だけを依頼書 N403c の形にした)。"""
    shipment = _noted(customer, line, user)
    billing_services.record_outside_payment(shipment, SHIPPED_AT, Decimal("440"), user)

    refusal = _refusal_and_refused(shipment, user)

    assert PAYMENT_TEXT in refusal
    assert NOTE_NEXT_STEP not in refusal


@pytest.mark.django_db
def test_issued_note_only_shipment_keeps_note_text(customer, line, user):
    """(c) 発行の納品書だけに入った出荷 (請求書と入金なし) は今までどおり納品書の文。"""
    shipment = _noted(customer, line, user)

    refusal = _refusal_and_refused(shipment, user)

    assert f"納品書 {shipment.delivery_note.note_no} に入っています" in refusal
    assert NOTE_NEXT_STEP in refusal
    assert PAYMENT_TEXT not in refusal and OLD_INVOICE_TEXT not in refusal


@pytest.mark.django_db
def test_delete_screen_shows_payment_text_first(logged_in, customer, line, user):
    """(d) 削除の確かめの画面にも (b) と同じ入金の文が出る (納品書を取り消すよう案内しない)。

    前は (a) の請求書の文を画面で見ていた。期待を変えた訳: 請求書の明細が指す出荷を断らなくなった (依頼書 N403c。本人の決め 11-11 12:5x〜13:0x) ので、
    発行の納品書より先に見る断りは入金の文だけになったため。請求書に入った出荷の削除の画面は tests/test_r27_billed_shipment.py の (j) が見る。
    """
    shipment = _noted(customer, line, user)
    billing_services.record_outside_payment(shipment, SHIPPED_AT, Decimal("440"), user)
    refusal = services.delete_refusal(shipment)

    body = logged_in.get(reverse("shipping:shipment_delete", args=[shipment.pk])).content.decode()

    assert refusal in body
    assert PAYMENT_TEXT in body
    assert NOTE_NEXT_STEP not in body
