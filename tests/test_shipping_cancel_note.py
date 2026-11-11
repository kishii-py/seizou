"""納品書を取り消す関数 (shipping.services.cancel_delivery_note) と、請求済みの出荷があるときの知らせの文 (billed_notice)。

取消の納品書は出荷を指したまま残す。2 回目の取消は ValueError。
発行の請求書に入った出荷の納品書も取り消せる (前は断った。納品書は出荷の記録を刷った物で、取り消しても請求書の明細 (写し) は
変わらないので、本人の決め 11-11 10:1x・13:0x「それでOK」で、止めずに検査の層の R27 で知らせる形にした)。
知らせの文は請求書の番号と「請求書は変わりません」を書き、入金の件数と合計と取り消す順は書かない (金額を書かない)。
入金の作り物と人は tests/test_billing_payment_views.py の定数と関数を import する (fixture を import すると ruff の F401・F811 に当たる)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.models import DeliveryNote
from tests.test_billing_payment_views import ALL_ON, RECEIVED_ON, account

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def line(user, customer):
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def note_with_two(user, customer, line):
    """出荷 4 と 6 をまとめた発行の納品書。"""
    first = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    second = services.ship(line, Decimal("6"), SHIPPED_AT, user)
    return services.create_delivery_note(customer, [first, second], user), first, second


@pytest.mark.django_db
def test_cancel_sets_status(user, note_with_two):
    note, _first, _second = note_with_two

    result = services.cancel_delivery_note(note, user)

    assert isinstance(result, DeliveryNote)
    assert result.pk == note.pk
    assert result.status == DeliveryNote.STATUS_CANCELLED
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_CANCELLED


@pytest.mark.django_db
def test_cancel_twice_is_refused(user, note_with_two):
    note, _first, _second = note_with_two
    services.cancel_delivery_note(note, user)

    with pytest.raises(ValueError, match="この納品書は取り消し済みです。"):
        services.cancel_delivery_note(note, user)
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_CANCELLED


@pytest.mark.django_db
def test_cancelled_note_keeps_its_shipments(user, note_with_two):
    note, first, second = note_with_two

    services.cancel_delivery_note(note, user)

    note.refresh_from_db()
    assert sorted(row.pk for row in note.shipments.all()) == sorted([first.pk, second.pk])
    first.refresh_from_db()
    assert first.delivery_note == note


@pytest.mark.django_db
def test_cancel_with_issued_invoice_keeps_invoice(user, customer, note_with_two):
    """発行の請求書に入った出荷の納品書も取り消せる。請求書は発行のまま、明細の写し (数量・単価・金額) も変わらない。"""
    note, first, second = note_with_two
    first.refresh_from_db()
    second.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)
    copies = list(invoice.lines.order_by("pk").values_list("shipment_id", "qty", "unit_price", "amount"))

    result = services.cancel_delivery_note(note, user)

    assert result.status == DeliveryNote.STATUS_CANCELLED
    invoice.refresh_from_db()
    assert invoice.status == invoice.STATUS_ISSUED
    assert list(invoice.lines.order_by("pk").values_list("shipment_id", "qty", "unit_price", "amount")) == copies


# --- 請求済みの出荷があるときの知らせの文 (billed_notice。前は取り消せない理由の文 cancel_refusal。初見テスト R5 の候補 C03。依頼書 N196・N403b) ---


@pytest.mark.django_db
def test_billed_notice_names_note_shipment_and_invoice(user, customer, note_with_two):
    """発行の請求書に入った出荷のある納品書の文は、番号 3 つと「納品書を取り消しても、請求書は変わりません。」。"""
    note, first, second = note_with_two
    second.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [second], user, issued_on=ISSUED_ON)

    notice = services.billed_notice(note)

    assert notice == (
        f"納品書 {note.note_no} の出荷 {second.shipment_no} は請求書 {invoice.invoice_no} に入っています。"
        "納品書を取り消しても、請求書は変わりません。"
    )
    # 読むだけで、表は変えない
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_ISSUED


@pytest.mark.django_db
def test_billed_notice_is_empty_without_issued_invoice(user, customer, note_with_two):
    """請求していない納品書と、請求書を取り消した後の納品書は空の字。"""
    note, first, second = note_with_two
    assert services.billed_notice(note) == ""

    first.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [first], user, issued_on=ISSUED_ON)
    assert services.billed_notice(note) != ""
    billing_services.cancel_invoice(invoice, user)

    assert services.billed_notice(note) == ""


@pytest.mark.django_db
def test_billed_notice_names_first_shipment(user, customer, note_with_two):
    """出荷が 2 つとも請求済みでも、文は pk の順で最初の出荷の番号で作る。"""
    note, first, second = note_with_two
    first.refresh_from_db()
    second.refresh_from_db()
    billing_services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)

    notice = services.billed_notice(note)

    assert first.shipment_no in notice
    assert second.shipment_no not in notice


# --- 知らせの文に入金を書かない (前の断りの文は入金の件数と合計と取り消す順を書いた。決め C03-2 a・依頼書 N296 を N403b で外した) ---


def _billed(user, customer, note_with_two):
    """出荷 4 と 6 を両方とも 1 枚の発行の請求書に入れる。(納品書, 知らせの文, 請求書) を返す。"""
    note, first, second = note_with_two
    first.refresh_from_db()
    second.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)
    text = (
        f"納品書 {note.note_no} の出荷 {first.shipment_no} は請求書 {invoice.invoice_no} に入っています。"
        "納品書を取り消しても、請求書は変わりません。"
    )
    return note, text, invoice


@pytest.mark.django_db
def test_billed_notice_does_not_write_payments(user, customer, note_with_two):
    """入金があっても、文は入金の無いときと同じ (入金の件数・合計・¥・取り消す順を書かない)。取り消すと入金は記録のまま残る。"""
    note, text, invoice = _billed(user, customer, note_with_two)
    assert services.billed_notice(note) == text

    payment = billing_services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)
    billing_services.record_payment(invoice, RECEIVED_ON, Decimal("1000"), user)
    notice = services.billed_notice(note)
    assert notice == text
    assert "入金" not in notice
    assert "¥" not in notice and "合計" not in notice

    assert services.cancel_delivery_note(note, user).status == DeliveryNote.STATUS_CANCELLED
    payment.refresh_from_db()
    assert payment.status == payment.STATUS_RECORDED
    invoice.refresh_from_db()
    assert invoice.status == invoice.STATUS_ISSUED


@pytest.mark.django_db
def test_billed_notice_follows_invoice_cancel(user, customer, note_with_two):
    """入金を取り消しても文は変わらず、請求書を取り消すと空の字。請求していない納品書も空の字。"""
    assert services.billed_notice(note_with_two[0]) == ""
    note, text, invoice = _billed(user, customer, note_with_two)
    payment = billing_services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)
    assert services.billed_notice(note) == text

    billing_services.cancel_payment(payment, user)
    assert services.billed_notice(note) == text

    billing_services.cancel_invoice(invoice, user)
    assert services.billed_notice(note) == ""


@pytest.mark.django_db
def test_cancel_delivery_note_with_paid_invoice_without_money(settings, user, customer, note_with_two):
    """金額の閲覧の判定を on にした会社で、金額を見られない人も、入金のある請求書の出荷の納品書を取り消せる (文に金額が無い)。"""
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    note, _text, invoice = _billed(user, customer, note_with_two)
    billing_services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)

    # 伝票編集 (納品書を書ける)・金額の閲覧なし
    jimu = account("jimu", permissions=["slip_edit"])
    assert services.cancel_delivery_note(note, jimu).status == DeliveryNote.STATUS_CANCELLED
    invoice.refresh_from_db()
    assert invoice.status == invoice.STATUS_ISSUED
