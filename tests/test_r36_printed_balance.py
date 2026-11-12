"""R36: 前回の残を写した請求書に数えた入金か調整を取り消そうとしたら (標準のポカよけ。会社の設定 printed_balance)。

前回の残を載せる会社 (invoice_previous_balance = true) で、10 月分の請求書の後に 11 月分を作ると、11 月分は 10 月分の残を前回の残に写す。
その後に 10 月分の入金か調整を取り消そうとすると、既定 (知らせる) で黄の知らせ、止めるで赤、通すで何も出ない。
取消の画面は service の名前が cancel_ で始まるので、黄の後のボタンは「このまま取り消す」、止めるの赤の頭は「取り消せません。」
(入力の土台 BaseFormView.check_words。依頼書は「このまま保存」と書いていたが、取消の画面の字はこちら)。
11 月分を作った後に記録した入金・前回の残の写しの無い会社・請求書の外で受け取った入金・取り消し済みの入金と調整には何も出ない。
調整の取消の画面はまだ無いので、調整は検査の層 (checks.run) を直に呼んで見る。services を直に呼ぶと断らずに取り消せる。
得意先は月末締め (締日 31)。単価 1,000・消費税 10%。作り物のデータは全部 services で作る。
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services
from seizou.billing.models import Adjustment, Payment
from seizou.core import checks
from seizou.core.exceptions import CheckViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

ORDER_DATE = date(2026, 8, 1)
PAYMENT_CANCEL = "billing.cancel_payment"
ADJUSTMENT_CANCEL = "billing.cancel_adjustment"
ON = {"invoice_previous_balance": True}
#: 小窓の要求の見出し (tests/test_r24_over_payment.py と同じ字)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer", "closing_day": 31, "payment_month": "next", "payment_day": 25},
        user,
    )


@pytest.fixture
def line(user, customer):
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("100"), "unit_price": Decimal("1000")}], user, order_date=ORDER_DATE
    )
    return order.lines.get()


def _invoice(user, customer, line, qty, shipped_at):
    shipment = shipped(user, customer, line, str(qty), shipped_at)
    _period_from, period_to = services.closing_period(customer, shipped_at)
    return services.create_invoice(customer, [shipment], user, issued_on=period_to)


@pytest.fixture
def printed(settings, user, customer, line):
    """10 月分 (合計 11,000) の入金 5,000 と調整 −660 を記録した後に、11 月分を作った (前回の残 5,340 を写した)。"""
    settings.SEIZOU = dict(ON)
    october = _invoice(user, customer, line, 10, date(2026, 10, 15))
    payment = services.record_payment(october, date(2026, 10, 31), Decimal("5000"), user)
    adjustment = services.record_adjustment(october, date(2026, 10, 31), Decimal("-660"), user)
    november = _invoice(user, customer, line, 5, date(2026, 11, 5))
    november.refresh_from_db()
    assert november.previous_balance == Decimal("5340")
    return {"october": october, "november": november, "payment": payment, "adjustment": adjustment}


def _payment_text(payment, november):
    number = november.invoice_no
    return (
        f"入金 ({payment.received_on:%Y-%m-%d}) は、請求書 {number} の前回の残に数えて刷っています。"
        f"取り消すと、{number} に刷った前回の残と、台帳の残が違ってきます。請求書も直すときは、{number} を作り直してください。"
    )


def _cancel_url(payment):
    return reverse("billing:payment_cancel", args=[payment.pk])


def _status(record):
    return type(record).objects.get(pk=record.pk).status


# --- 検査の層 ---


@pytest.mark.django_db
def test_r36_payment_text(user, printed):
    warnings = checks.run(PAYMENT_CANCEL, {"object": printed["payment"]}, user)
    assert [(warning.code, warning.message) for warning in warnings] == [
        ("R36", _payment_text(printed["payment"], printed["november"]))
    ]
    assert services.counted_in(printed["payment"]) == [printed["november"]]


@pytest.mark.django_db
def test_r36_adjustment_warn_stop_off(settings, user, printed):
    adjustment, number = printed["adjustment"], printed["november"].invoice_no
    expected = (
        f"調整 (2026-10-31・振込手数料) は、請求書 {number} の前回の残に数えて刷っています。"
        f"取り消すと、{number} に刷った前回の残と、台帳の残が違ってきます。請求書も直すときは、{number} を作り直してください。"
    )
    (warning,) = checks.run(ADJUSTMENT_CANCEL, {"object": adjustment}, user)
    assert (warning.code, warning.message) == ("R36", expected)

    settings.SEIZOU = {**ON, "printed_balance": "stop"}
    with pytest.raises(CheckViolation) as caught:
        checks.run(ADJUSTMENT_CANCEL, {"object": adjustment}, user, stop_head="取り消せません。")
    assert caught.value.code == "R36"
    assert caught.value.message.startswith("取り消せません。")

    settings.SEIZOU = {**ON, "printed_balance": "off"}
    assert checks.run(ADJUSTMENT_CANCEL, {"object": adjustment}, user) == []


@pytest.mark.django_db
def test_r36_silent_for_later_records_and_cancelled(settings, user, printed):
    october = printed["october"]
    later_payment = services.record_payment(october, date(2026, 11, 20), Decimal("100"), user)
    later_adjustment = services.record_adjustment(october, date(2026, 11, 20), Decimal("-1"), user)
    assert checks.run(PAYMENT_CANCEL, {"object": later_payment}, user) == []
    assert checks.run(ADJUSTMENT_CANCEL, {"object": later_adjustment}, user) == []
    assert services.counted_in(later_payment) == []

    # 取り消し済みの入金と調整には文を返さない (services が断るので、黄の後に赤を出さない)
    payment = services.cancel_payment(printed["payment"], user)
    adjustment = services.cancel_adjustment(printed["adjustment"], user)
    assert checks.run(PAYMENT_CANCEL, {"object": payment}, user) == []
    assert checks.run(ADJUSTMENT_CANCEL, {"object": adjustment}, user) == []


@pytest.mark.django_db
def test_r36_silent_without_copies(settings, user, customer, line):
    """前回の残の写しの無い会社 (既定の「載せない」で作った請求書) では何も出ない。"""
    settings.SEIZOU = {}
    october = _invoice(user, customer, line, 10, date(2026, 10, 15))
    payment = services.record_payment(october, date(2026, 10, 31), Decimal("5000"), user)
    adjustment = services.record_adjustment(october, date(2026, 10, 31), Decimal("-660"), user)
    _invoice(user, customer, line, 5, date(2026, 11, 5))
    assert checks.run(PAYMENT_CANCEL, {"object": payment}, user) == []
    assert checks.run(ADJUSTMENT_CANCEL, {"object": adjustment}, user) == []


@pytest.mark.django_db
def test_r36_silent_for_outside_payment(settings, logged_in, user, customer, line, printed):
    loose = shipped(user, customer, line, "1", date(2026, 10, 20))
    outside = services.record_outside_payment(loose, date(2026, 10, 20), Decimal("1100"), user)
    assert checks.run(PAYMENT_CANCEL, {"object": outside}, user) == []
    response = logged_in.post(_cancel_url(outside), {})
    assert response.status_code == 302
    assert _status(outside) == Payment.STATUS_CANCELLED


@pytest.mark.django_db
def test_services_cancel_counted_records_without_refusal(user, printed):
    services.cancel_payment(printed["payment"], user)
    services.cancel_adjustment(printed["adjustment"], user)
    assert _status(printed["payment"]) == Payment.STATUS_CANCELLED
    assert _status(printed["adjustment"]) == Adjustment.STATUS_CANCELLED
    printed["november"].refresh_from_db()
    assert printed["november"].previous_balance == Decimal("5340")  # 写した値は変わらない


# --- 入金の取消の画面 ---


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, MODAL], ids=["whole", "modal"])
def test_r36_screen_warns_then_cancels(settings, logged_in, printed, headers):
    payment, number = printed["payment"], printed["november"].invoice_no

    first = logged_in.post(_cancel_url(payment), {}, **headers)

    assert first.status_code == 200
    body = first.content.decode()
    assert "alert-warning" in body and "R36" in body
    assert f"{number} の前回の残に数えて" in body
    assert "このまま取り消す</button>" in body
    assert 'name="confirmed" value="1"' in body
    assert 'data-notice="stop"' not in body
    assert _status(payment) == Payment.STATUS_RECORDED

    second = logged_in.post(_cancel_url(payment), {"confirmed": "1"}, **headers)
    assert second.status_code == (204 if headers else 302)
    if headers:
        assert "seizou:saved" in json.loads(second["HX-Trigger"])
    assert _status(payment) == Payment.STATUS_CANCELLED


@pytest.mark.django_db
def test_r36_screen_stop_and_off(settings, logged_in, printed):
    payment = printed["payment"]
    settings.SEIZOU = {**ON, "printed_balance": "stop"}

    stopped = logged_in.post(_cancel_url(payment), {})
    assert stopped.status_code == 200
    body = stopped.content.decode()
    assert 'data-notice="stop"' in body and "R36" in body
    assert '<span class="font-semibold">取り消せません。</span>' in body
    assert "このまま取り消す" not in body
    again = logged_in.post(_cancel_url(payment), {"confirmed": "1"})
    assert again.status_code == 200
    assert _status(payment) == Payment.STATUS_RECORDED

    settings.SEIZOU = {**ON, "printed_balance": "off"}
    passed = logged_in.post(_cancel_url(payment), {})
    assert passed.status_code == 302
    assert _status(payment) == Payment.STATUS_CANCELLED


@pytest.mark.django_db
def test_r36_screen_silent_for_payment_after_copy(logged_in, user, printed):
    later = services.record_payment(printed["october"], date(2026, 11, 20), Decimal("100"), user)
    response = logged_in.post(_cancel_url(later), {})
    assert response.status_code == 302
    assert _status(later) == Payment.STATUS_CANCELLED
