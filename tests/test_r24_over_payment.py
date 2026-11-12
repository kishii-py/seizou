"""R24: 入金の金額が、請求書の残を超えたら (標準のポカよけ。会社の設定 over_payment)。

検査の層 (seizou.core.checks) を設定の 3 つの値 (stop・warn・off) で回し、入金の記録の画面 (画面の全体と小窓) でも同じ動きを見る。
止める = 赤の知らせで保存しない。知らせる = 黄の知らせと「このまま保存」、confirmed=1 の 2 回目で保存。通す = そのまま保存。
残と同じ額 (「残の全額を入れる」で入れた額) は知らせない。取消の請求書は R24 を見ない (service の R21 が赤で止める)。
services の record_payment は今のまま多い入金を止めない (現物優先。止めるかは画面の前の検査の層が会社の設定で決める)。
請求書は合計 16,500 (出荷 4 と 6 に単価 1,500・消費税 10%)。画面は Django の test client で見る。作り物のデータは全部 services で作る。
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services
from seizou.billing.models import Payment
from seizou.core import checks
from seizou.core.exceptions import CheckViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
RECEIVED_ON = date(2026, 12, 25)
ACTION = "billing.record_payment"
#: 小窓の要求の見出し (tests/test_small_inputs_modal.py と同じ字)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}


@pytest.fixture
def invoice(user):
    """出荷 4 と 6 の請求書 (小計 15,000・消費税 1,500・合計 16,500)。"""
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer", "closing_day": 20, "payment_month": "next", "payment_day": 25},
        user,
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}], user, order_date=ORDER_DATE
    )
    line = order.lines.get()
    first = shipped(user, customer, line, "4", SHIPPED_AT)
    second = shipped(user, customer, line, "6", SHIPPED_AT)
    invoice = services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)
    assert invoice.total == Decimal("16500")
    return invoice


@pytest.fixture
def partly_paid(user, invoice):
    """6,500 を記録した請求書 (残 10,000)。"""
    services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)
    assert invoice.balance() == Decimal("10000")
    return invoice


@pytest.fixture
def fully_paid(user, invoice):
    """残の全額 (16,500) を記録した請求書 (残 0)。"""
    services.record_payment(invoice, RECEIVED_ON, invoice.total, user)
    assert invoice.balance() == 0
    return invoice


def _data(invoice, amount):
    return {"object": invoice, "amount": Decimal(amount)}


def _post(amount, **extra):
    return {"received_on": RECEIVED_ON.isoformat(), "amount": str(amount), "method": "transfer", "note": "", **extra}


def _url(invoice):
    return reverse("billing:payment_create", args=[invoice.pk])


# --- 検査の層 ---


@pytest.mark.django_db
def test_r24_stop_raises_check_violation(settings, user, partly_paid):
    settings.SEIZOU = {"over_payment": "stop"}
    with pytest.raises(CheckViolation) as caught:
        checks.run(ACTION, _data(partly_paid, "10001"), user)
    assert caught.value.code == "R24"
    assert caught.value.message.startswith("保存できません。")
    # 残と同じ額なら止めない
    assert checks.run(ACTION, _data(partly_paid, "10000"), user) == []


@pytest.mark.django_db
def test_r24_warn_returns_warning(settings, user, partly_paid):
    settings.SEIZOU = {"over_payment": "warn"}
    warnings = checks.run(ACTION, _data(partly_paid, "10001"), user)
    assert len(warnings) == 1
    assert warnings[0].code == "R24"
    assert warnings[0].message == (
        "入金の合計 (¥16,501) が請求書の合計 (¥16,500) を超えます。"
        "この請求書の残は ¥10,000 です。同じ入金を 2 回記録していないか、金額を確かめてください。"
    )
    # 残と同じ額なら知らせない
    assert checks.run(ACTION, _data(partly_paid, "10000"), user) == []


@pytest.mark.django_db
def test_r24_warns_any_amount_after_full_payment(settings, user, fully_paid):
    settings.SEIZOU = {"over_payment": "warn"}
    (warning,) = checks.run(ACTION, _data(fully_paid, "1"), user)
    assert warning.code == "R24"
    assert warning.message == (
        "入金の合計 (¥16,501) が請求書の合計 (¥16,500) を超えます。"
        "この請求書の残は ¥0 です。同じ入金を 2 回記録していないか、金額を確かめてください。"
    )


@pytest.mark.django_db
def test_r24_off_is_silent(settings, user, fully_paid):
    settings.SEIZOU = {"over_payment": "off"}
    assert checks.run(ACTION, _data(fully_paid, "1"), user) == []
    assert checks.run(ACTION, _data(fully_paid, "1000000"), user) == []


@pytest.mark.django_db
def test_r24_skips_cancelled_invoice(settings, user, invoice):
    """取消の請求書は R24 を見ない。service の R21 (取消した請求書には入金を記録できない) が赤で止める。"""
    services.cancel_invoice(invoice, user)
    invoice.refresh_from_db()  # cancel_invoice は読み直した請求書を保存する (画面は URL の pk で読み直した物を渡す)
    settings.SEIZOU = {"over_payment": "stop"}
    assert checks.run(ACTION, _data(invoice, "100000"), user) == []


# --- 入金の記録の画面の全体 ---


@pytest.mark.django_db
def test_r24_warn_on_screen_then_confirm(settings, logged_in, fully_paid):
    settings.SEIZOU = {"over_payment": "warn"}
    first = logged_in.post(_url(fully_paid), _post(1000))
    assert first.status_code == 200
    body = first.content.decode()
    assert 'data-notice="check"' in body and "R24" in body and "このまま保存" in body
    assert 'name="confirmed" value="1"' in body
    assert 'data-notice="stop"' not in body
    assert Payment.objects.count() == 1

    second = logged_in.post(_url(fully_paid), _post(1000, confirmed="1"))
    assert second.status_code == 302
    assert Payment.objects.count() == 2
    assert fully_paid.balance() == Decimal("-1000")


@pytest.mark.django_db
def test_r24_stop_on_screen_is_red_and_not_saved(settings, logged_in, fully_paid):
    settings.SEIZOU = {"over_payment": "stop"}
    response = logged_in.post(_url(fully_paid), _post(1000))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body and "R24" in body
    assert "このまま保存" not in body
    assert Payment.objects.count() == 1

    # 「このまま保存」の印を付けて送っても、止めるの設定では保存しない
    again = logged_in.post(_url(fully_paid), _post(1000, confirmed="1"))
    assert again.status_code == 200
    assert Payment.objects.count() == 1


@pytest.mark.django_db
def test_r24_off_on_screen_saves(settings, logged_in, fully_paid):
    settings.SEIZOU = {"over_payment": "off"}
    response = logged_in.post(_url(fully_paid), _post(1000))
    assert response.status_code == 302
    assert Payment.objects.count() == 2
    assert fully_paid.balance() == Decimal("-1000")


# --- 入金の記録の小窓 ---


@pytest.mark.django_db
def test_r24_warn_in_modal_then_confirm(settings, logged_in, fully_paid):
    settings.SEIZOU = {"over_payment": "warn"}
    first = logged_in.post(_url(fully_paid), _post(1000), **MODAL)
    assert first.status_code == 200
    body = first.content.decode()
    assert "alert-warning" in body and "R24" in body and "このまま保存" in body
    assert Payment.objects.count() == 1

    second = logged_in.post(_url(fully_paid), _post(1000, confirmed="1"), **MODAL)
    assert second.status_code == 204
    assert "seizou:saved" in json.loads(second["HX-Trigger"])
    assert Payment.objects.count() == 2


# --- 調整のある請求書 (依頼書 N409。請求書の残 = 合計 − 入金 + 調整) ---


@pytest.mark.django_db
def test_r24_text_with_adjustment_uses_invoice_balance(settings, user, partly_paid):
    """調整のある請求書では、入金の合計と請求書の合計を比べず、今回の入金と請求書の残 (調整を数えた残) を比べる字にする。"""
    services.record_adjustment(partly_paid, RECEIVED_ON, Decimal("-500"), user)
    settings.SEIZOU = {"over_payment": "warn"}
    (warning,) = checks.run(ACTION, _data(partly_paid, "10000"), user)
    assert warning.code == "R24"
    assert warning.message == (
        "今回の入金 (¥10,000) が請求書の残 (¥9,500) を超えます。同じ入金を 2 回記録していないか、金額を確かめてください。"
    )
    # 調整を数えた残と同じ額なら知らせない
    assert checks.run(ACTION, _data(partly_paid, "9500"), user) == []
