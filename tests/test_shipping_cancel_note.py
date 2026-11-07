"""納品書を取り消す関数 (shipping.services.cancel_delivery_note)。

取消の納品書は出荷を指したまま残す。2 回目の取消と、発行の請求書に入った出荷の納品書の取消は ValueError。
請求書を先に取り消せば、納品書を取り消せる。
請求書に入金があれば、断りの文は取り消す順 (入金 → 請求書 → 納品書) と入金の記録の件数と合計 (金額の閲覧の無い人には件数だけ) を書く。
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
def test_cancel_after_invoice_cancelled(user, customer, note_with_two):
    note, first, second = note_with_two
    first.refresh_from_db()
    second.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)
    with pytest.raises(ValueError, match=invoice.invoice_no):
        services.cancel_delivery_note(note, user)

    billing_services.cancel_invoice(invoice, user)
    result = services.cancel_delivery_note(note, user)

    assert result.status == DeliveryNote.STATUS_CANCELLED


# --- 取り消せない理由の文 (cancel_refusal。初見テスト R5 の候補 C03。依頼書 N196) ---


@pytest.mark.django_db
def test_cancel_refusal_names_note_shipment_and_invoice(user, customer, note_with_two):
    """発行の請求書に入った出荷のある納品書の文は「取り消せません。」で始まり、番号 3 つと先にすることを書く。"""
    note, first, second = note_with_two
    second.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [second], user, issued_on=ISSUED_ON)

    refusal = services.cancel_refusal(note)

    assert refusal.startswith("取り消せません。")
    assert f"納品書 {note.note_no} の出荷 {second.shipment_no} は請求書 {invoice.invoice_no} で請求済みです。" in refusal
    assert refusal.endswith("先に請求書を取り消してから、納品書を取り消してください。")
    assert "入っています" not in refusal
    # 読むだけで、表は変えない
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_ISSUED


@pytest.mark.django_db
def test_cancel_refusal_is_empty_without_issued_invoice(user, customer, note_with_two):
    """請求していない納品書と、請求書を取り消した後の納品書は空の字。"""
    note, first, second = note_with_two
    assert services.cancel_refusal(note) == ""

    first.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [first], user, issued_on=ISSUED_ON)
    assert services.cancel_refusal(note) != ""
    billing_services.cancel_invoice(invoice, user)

    assert services.cancel_refusal(note) == ""


@pytest.mark.django_db
def test_cancel_delivery_note_raises_the_same_refusal(user, customer, note_with_two):
    """cancel_delivery_note の ValueError の字は cancel_refusal の字と同じ (文は 1 か所)。"""
    note, first, second = note_with_two
    first.refresh_from_db()
    second.refresh_from_db()
    billing_services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)

    with pytest.raises(ValueError) as caught:
        services.cancel_delivery_note(note, user)

    assert str(caught.value) == services.cancel_refusal(note)
    # 出荷が 2 つとも請求済みでも、文は pk の順で最初の出荷の番号で作る
    assert first.shipment_no in str(caught.value)
    assert second.shipment_no not in str(caught.value)


# --- 断りの文の入金と取り消す順 (初見テスト R5 の候補 C03。決め C03-2 a。依頼書 N296) ---

NO_PAYMENT = "先に請求書を取り消してから、納品書を取り消してください。"
PAID_ONE = "先に請求書の入金を取り消し (入金の記録 1 件・合計 ¥6,500)、次に請求書を、最後に納品書を取り消してください。"
PAID_TWO = "先に請求書の入金を取り消し (入金の記録 2 件・合計 ¥7,500)、次に請求書を、最後に納品書を取り消してください。"
PAID_ONE_COUNT = "先に請求書の入金を取り消し (入金の記録 1 件)、次に請求書を、最後に納品書を取り消してください。"


def _billed(user, customer, note_with_two):
    """出荷 4 と 6 を両方とも 1 枚の発行の請求書に入れる。(納品書, 文の頭の 2 文, 請求書) を返す。"""
    note, first, second = note_with_two
    first.refresh_from_db()
    second.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)
    head = f"取り消せません。納品書 {note.note_no} の出荷 {first.shipment_no} は請求書 {invoice.invoice_no} で請求済みです。"
    return note, head, invoice


@pytest.mark.django_db
def test_cancel_refusal_without_payment_is_exact(user, customer, note_with_two):
    """入金の無い請求書なら、文は N196 の字のまま (入金を書かない)。"""
    note, head, _invoice = _billed(user, customer, note_with_two)

    refusal = services.cancel_refusal(note)

    assert refusal == head + NO_PAYMENT
    assert "入金" not in refusal


@pytest.mark.django_db
def test_cancel_refusal_counts_payments_in_order(user, customer, note_with_two):
    """入金があれば、取り消す順 (入金 → 請求書 → 納品書) と、入金の記録の件数と合計 ¥ を 1 つの文で書く。"""
    note, head, invoice = _billed(user, customer, note_with_two)

    billing_services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)
    assert services.cancel_refusal(note) == head + PAID_ONE

    billing_services.record_payment(invoice, RECEIVED_ON, Decimal("1000"), user)
    refusal = services.cancel_refusal(note)
    assert refusal == head + PAID_TWO
    assert "残り" not in refusal
    assert " 円" not in refusal
    with pytest.raises(ValueError) as caught:
        services.cancel_delivery_note(note, user)
    assert str(caught.value) == refusal
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_ISSUED


@pytest.mark.django_db
def test_cancel_refusal_hides_money(user, customer, note_with_two):
    """hide_money が真なら、入金は件数だけ (「合計」と「¥」を書かない。請求書の取消の文と同じ決まり)。"""
    note, head, invoice = _billed(user, customer, note_with_two)
    billing_services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)

    refusal = services.cancel_refusal(note, hide_money=True)

    assert refusal == head + PAID_ONE_COUNT
    assert "合計" not in refusal
    assert "¥" not in refusal


@pytest.mark.django_db
def test_cancel_refusal_follows_payment_and_invoice_cancel(user, customer, note_with_two):
    """入金を取り消すと入金の無い文に戻り、請求書を取り消すと空の字。請求していない納品書も空の字。"""
    assert services.cancel_refusal(note_with_two[0]) == ""
    note, head, invoice = _billed(user, customer, note_with_two)
    payment = billing_services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)
    assert services.cancel_refusal(note) == head + PAID_ONE

    billing_services.cancel_payment(payment, user)
    assert services.cancel_refusal(note) == head + NO_PAYMENT

    billing_services.cancel_invoice(invoice, user)
    assert services.cancel_refusal(note) == ""


@pytest.mark.django_db
def test_cancel_delivery_note_refusal_counts_without_money(settings, user, customer, note_with_two):
    """金額の閲覧の判定を on にした会社で、金額を見られない人の断りは件数だけ、見られる人は件数と合計。発行のまま。"""
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    note, head, invoice = _billed(user, customer, note_with_two)
    billing_services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)

    # 伝票編集 (納品書を書ける)・金額の閲覧なし = 件数だけ
    jimu = account("jimu", permissions=["slip_edit"])
    with pytest.raises(ValueError) as caught:
        services.cancel_delivery_note(note, jimu)
    assert str(caught.value) == head + PAID_ONE_COUNT
    assert str(caught.value) == services.cancel_refusal(note, hide_money=True)

    # 金額の閲覧あり = 件数と合計
    keiri = account("keiri", permissions=["billing"], see_money=True)
    with pytest.raises(ValueError) as caught:
        services.cancel_delivery_note(note, keiri)
    assert str(caught.value) == head + PAID_ONE
    assert str(caught.value) == services.cancel_refusal(note)

    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_ISSUED
