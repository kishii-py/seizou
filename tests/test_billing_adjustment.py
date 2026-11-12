"""請求書の調整 (billing.services.record_adjustment・cancel_adjustment) と、調整を数えた請求書の残と入金の状態。

調整は請求書を指し、残を減らす調整はマイナスで入れる (振込手数料 660 は −660)。請求書の残 = 合計 − 取消でない入金 + 取消でない調整。
一覧の「残のある物だけ」とダッシュボードの「入金の残っている請求書」も同じ式で数える (結び付けを 2 つ並べて数が倍にならない事も見る)。
取消は行を消さずに状態を取消にする。取消でない調整の残る請求書は取り消せない (作り直しも同じ文で止まる)。
得意先ごとの台帳の残は tests/test_billing_ledger.py が見る。作り物のデータは全部 services で作る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services
from seizou.billing.models import Adjustment, Invoice, Payment
from seizou.core.dashboard import _invoices_to_be_paid
from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 20)
RECEIVED_ON = date(2026, 12, 25)
ADJUSTED_ON = date(2026, 12, 25)

#: 入金だけの請求書の取消の断りの文 (今の字。調整を足しても変えない)
PAYMENT_ONLY = "取り消せません。この請求書には入金の記録が 1 件あります (合計 ¥54,340)。先に入金を取り消してください。"


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer", "closing_day": 20, "payment_month": "next", "payment_day": 25},
        user,
    )


@pytest.fixture
def line(user, customer):
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("5000")}], user, order_date=ORDER_DATE
    )
    return order.lines.get()


@pytest.fixture
def invoice(user, customer, line):
    """出荷 10 の請求書 (小計 50,000・消費税 5,000・合計 55,000)。"""
    invoice = services.create_invoice(customer, [shipped(user, customer, line, "10", SHIPPED_AT)], user, issued_on=ISSUED_ON)
    assert invoice.total == Decimal("55000")
    return invoice


def _fresh(invoice):
    return Invoice.objects.prefetch_related("payments", "adjustments").get(pk=invoice.pk)


def _listed_unpaid(client):
    response = client.get(reverse("billing:invoice_list"), {"f_unpaid": "1"})
    assert response.status_code == 200
    return {row["obj"].pk for row in response.context["rows"]}


def _dashboard_unpaid():
    return set(_invoices_to_be_paid().values_list("pk", flat=True))


# --- (a) 振込手数料を引いた入金と、−660 の調整 ---


@pytest.mark.django_db
def test_fee_adjustment_clears_balance(user, logged_in, invoice):
    services.record_payment(invoice, RECEIVED_ON, Decimal("54340"), user)
    assert _fresh(invoice).balance() == Decimal("660")
    assert _fresh(invoice).payment_status() == Invoice.PAYMENT_PARTIAL
    assert invoice.pk in _listed_unpaid(logged_in)
    assert invoice.pk in _dashboard_unpaid()

    adjustment = services.record_adjustment(invoice, ADJUSTED_ON, Decimal("-660"), user, reason=Adjustment.REASON_FEE)

    assert adjustment.saved_message == f"請求書 {invoice.invoice_no} に調整 (振込手数料 ¥-660) を記録しました。"
    assert adjustment.recorded_by == user
    assert adjustment.status == Adjustment.STATUS_RECORDED
    fresh = _fresh(invoice)
    assert fresh.adjusted_total() == Decimal("-660")
    assert fresh.balance() == 0
    assert fresh.payment_status() == Invoice.PAYMENT_PAID
    assert fresh.payment_status_label() == "入金済み"
    assert invoice.pk not in _listed_unpaid(logged_in)
    assert invoice.pk not in _dashboard_unpaid()


@pytest.mark.django_db
def test_two_payments_and_two_adjustments_are_not_counted_twice(user, logged_in, invoice):
    """入金 2 件と調整 2 件の請求書。結び付けを 2 つ並べて数えると入金と調整が倍になり、残のある請求書が落ちる。"""
    services.record_payment(invoice, RECEIVED_ON, Decimal("20000"), user)
    services.record_payment(invoice, RECEIVED_ON, Decimal("20000"), user)
    services.record_adjustment(invoice, ADJUSTED_ON, Decimal("-500"), user)
    services.record_adjustment(invoice, ADJUSTED_ON, Decimal("-500"), user, reason=Adjustment.REASON_DISCOUNT)

    assert _fresh(invoice).balance() == Decimal("14000")
    assert invoice.pk in _listed_unpaid(logged_in)
    assert invoice.pk in _dashboard_unpaid()

    services.record_payment(invoice, RECEIVED_ON, Decimal("14000"), user)
    assert _fresh(invoice).balance() == 0
    assert invoice.pk not in _listed_unpaid(logged_in)
    assert invoice.pk not in _dashboard_unpaid()


# --- (b) 足す調整・調整だけで残が 0・調整の無い請求書 ---


@pytest.mark.django_db
def test_plus_adjustment_raises_balance(user, invoice):
    services.record_adjustment(invoice, ADJUSTED_ON, Decimal("1000"), user, reason=Adjustment.REASON_OTHER, note="運賃の足し")
    fresh = _fresh(invoice)
    assert fresh.balance() == Decimal("56000")
    assert fresh.payment_status() == Invoice.PAYMENT_UNPAID


@pytest.mark.django_db
def test_adjustment_alone_can_make_invoice_paid(user, logged_in, invoice):
    services.record_adjustment(invoice, ADJUSTED_ON, -invoice.total, user, reason=Adjustment.REASON_OFFSET)
    fresh = _fresh(invoice)
    assert fresh.paid_total() == 0
    assert fresh.balance() == 0
    assert fresh.payment_status() == Invoice.PAYMENT_PAID
    assert invoice.pk not in _listed_unpaid(logged_in)
    assert invoice.pk not in _dashboard_unpaid()


@pytest.mark.django_db
def test_status_without_adjustment_is_unchanged(user, invoice):
    assert _fresh(invoice).payment_status() == Invoice.PAYMENT_UNPAID
    services.record_payment(invoice, RECEIVED_ON, Decimal("1000"), user)
    assert _fresh(invoice).payment_status() == Invoice.PAYMENT_PARTIAL
    services.record_payment(invoice, RECEIVED_ON, Decimal("60000"), user)
    fresh = _fresh(invoice)
    assert fresh.balance() == Decimal("-6000")
    assert fresh.payment_status() == Invoice.PAYMENT_PAID


# --- (c) 断る入力 ---


@pytest.mark.django_db
def test_cancelled_invoice_refuses_adjustment(user, invoice):
    services.cancel_invoice(invoice, user)
    with pytest.raises(ValueError) as caught:
        services.record_adjustment(invoice, ADJUSTED_ON, Decimal("-660"), user)
    assert str(caught.value) == f"記録できません。請求書 {invoice.invoice_no} は取消です。発行の請求書を選び直してください。"
    assert Adjustment.objects.count() == 0
    assert not ChangeLog.objects.filter(model="billing.adjustment").exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kwargs",
    [
        {"amount": Decimal("0")},
        {"amount": "0.00"},
        {"amount": "abc"},
        {"amount": None},
        {"amount": Decimal("NaN")},
        {"amount": Decimal("-660"), "reason": "gift"},
        {"amount": Decimal("-660"), "note": "あ" * 501},
    ],
)
def test_bad_input_is_refused(user, invoice, kwargs):
    amount = kwargs.pop("amount")
    with pytest.raises(ValueError, match="^記録できません。"):
        services.record_adjustment(invoice, ADJUSTED_ON, amount, user, **kwargs)
    assert Adjustment.objects.count() == 0


@pytest.mark.django_db
def test_note_of_500_chars_is_kept(user, invoice):
    adjustment = services.record_adjustment(invoice, ADJUSTED_ON, Decimal("-1"), user, reason=Adjustment.REASON_ROUNDING, note="あ" * 500)
    assert Adjustment.objects.get(pk=adjustment.pk).note == "あ" * 500


# --- (d) 取消でない調整の残る請求書は取り消せない ---


@pytest.mark.django_db
def test_invoice_with_adjustment_cannot_be_cancelled(user, invoice):
    adjustment = services.record_adjustment(invoice, ADJUSTED_ON, Decimal("-660"), user)

    with pytest.raises(ValueError) as caught:
        services.cancel_invoice(invoice, user)
    assert str(caught.value) == "取り消せません。この請求書には調整が 1 件あります (合計 ¥-660)。先に入金と調整を取り消してください。"
    assert services.cancel_refusal(_fresh(invoice), hide_money=True) == (
        "取り消せません。この請求書には調整が 1 件あります。先に入金と調整を取り消してください。"
    )
    with pytest.raises(ValueError) as again:
        services.reissue_invoice(invoice, user, issued_on=ISSUED_ON)
    assert str(again.value) == str(caught.value)
    assert Invoice.objects.get(pk=invoice.pk).status == Invoice.STATUS_ISSUED

    services.cancel_adjustment(adjustment, user)
    cancelled = services.cancel_invoice(invoice, user)
    assert cancelled.status == Invoice.STATUS_CANCELLED


@pytest.mark.django_db
def test_refusal_with_payment_and_adjustment(user, invoice):
    services.record_payment(invoice, RECEIVED_ON, Decimal("54340"), user)
    services.record_adjustment(invoice, ADJUSTED_ON, Decimal("-660"), user)
    assert services.cancel_refusal(_fresh(invoice)) == (
        "取り消せません。この請求書には入金の記録が 1 件と調整が 1 件あります (合計 ¥54,340)。先に入金と調整を取り消してください。"
    )
    assert services.cancel_refusal(_fresh(invoice), hide_money=True) == (
        "取り消せません。この請求書には入金の記録が 1 件と調整が 1 件あります。先に入金と調整を取り消してください。"
    )


@pytest.mark.django_db
def test_refusal_with_payment_only_is_unchanged(user, invoice):
    services.record_payment(invoice, RECEIVED_ON, Decimal("54340"), user)
    adjustment = services.record_adjustment(invoice, ADJUSTED_ON, Decimal("-660"), user)
    services.cancel_adjustment(adjustment, user)
    assert services.cancel_refusal(_fresh(invoice)) == PAYMENT_ONLY
    with pytest.raises(ValueError) as caught:
        services.cancel_invoice(invoice, user)
    assert str(caught.value) == PAYMENT_ONLY


# --- (e) 調整の取消 ---


@pytest.mark.django_db
def test_cancel_adjustment_restores_balance_and_keeps_history(user, invoice):
    services.record_payment(invoice, RECEIVED_ON, Decimal("54340"), user)
    adjustment = services.record_adjustment(invoice, ADJUSTED_ON, Decimal("-660"), user)
    assert _fresh(invoice).balance() == 0

    cancelled = services.cancel_adjustment(adjustment, user)

    assert cancelled.saved_message == "取り消しました。"
    stored = Adjustment.objects.get(pk=adjustment.pk)
    assert stored.status == Adjustment.STATUS_CANCELLED
    fresh = _fresh(invoice)
    assert fresh.balance() == Decimal("660")
    assert fresh.payment_status() == Invoice.PAYMENT_PARTIAL
    rows = ChangeLog.objects.filter(model="billing.adjustment", object_pk=adjustment.pk).order_by("pk")
    assert [(row.action, row.owner_model, row.owner_pk) for row in rows] == [
        ("created", "billing.invoice", invoice.pk),
        ("cancelled", "billing.invoice", invoice.pk),
    ]

    with pytest.raises(ValueError, match="^取り消せません。この調整は取り消し済みです。"):
        services.cancel_adjustment(adjustment, user)
    assert Adjustment.objects.filter(status=Adjustment.STATUS_CANCELLED).count() == 1
    assert Payment.objects.count() == 1
