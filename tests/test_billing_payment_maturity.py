"""入金の期日 (Payment.matures_on) と、入金の方法の電子記録債権。

手形と電子記録債権は期日を必ず入れる (services の record_payment・record_outside_payment は ValueError、入金の form は欄の誤り)。
ほかの方法で期日を入れても断らずに残す。期日と入金日の前後は見ない。入金の種 (billing.demo_seed) は「期日」と「電子記録債権」の字を読む。
作り物のデータは全部 services で作る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import demo_seed, services
from seizou.billing.models import Payment
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 20)
RECEIVED_ON = date(2026, 12, 25)
MATURES_ON = date(2027, 3, 25)
NEEDS_DATE = "記録できません。手形と電子記録債権は期日を入れてください。"
FIELD_ERROR = "手形と電子記録債権は期日を入れてください。"


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
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}], user, order_date=ORDER_DATE
    )
    return order.lines.get()


@pytest.fixture
def invoice(user, customer, line):
    return services.create_invoice(customer, [shipped(user, customer, line, "4", SHIPPED_AT)], user, issued_on=ISSUED_ON)


@pytest.fixture
def loose_shipment(user, customer, line):
    """請求書に入っていない出荷 (請求書の外で受け取った入金を記録できる)。"""
    return shipped(user, customer, line, "2", SHIPPED_AT)


# --- services ---


@pytest.mark.django_db
@pytest.mark.parametrize("method", [Payment.METHOD_BILL, Payment.METHOD_E_BOND])
def test_bill_and_e_bond_need_maturity(user, invoice, loose_shipment, method):
    with pytest.raises(ValueError) as caught:
        services.record_payment(invoice, RECEIVED_ON, Decimal("6600"), user, method=method)
    assert str(caught.value) == NEEDS_DATE
    with pytest.raises(ValueError) as outside:
        services.record_outside_payment(loose_shipment, RECEIVED_ON, Decimal("3300"), user, method=method)
    assert str(outside.value) == NEEDS_DATE
    assert Payment.objects.count() == 0

    payment = services.record_payment(invoice, RECEIVED_ON, Decimal("6600"), user, method=method, matures_on=MATURES_ON)
    other = services.record_outside_payment(loose_shipment, RECEIVED_ON, Decimal("3300"), user, method=method, matures_on=MATURES_ON)
    assert Payment.objects.get(pk=payment.pk).matures_on == MATURES_ON
    assert Payment.objects.get(pk=other.pk).matures_on == MATURES_ON
    assert Payment.objects.get(pk=payment.pk).method == method


@pytest.mark.django_db
def test_transfer_keeps_maturity_and_order_is_not_checked(user, invoice):
    transfer = services.record_payment(invoice, RECEIVED_ON, Decimal("1000"), user, matures_on=MATURES_ON)
    assert Payment.objects.get(pk=transfer.pk).matures_on == MATURES_ON
    plain = services.record_payment(invoice, RECEIVED_ON, Decimal("1000"), user)
    assert Payment.objects.get(pk=plain.pk).matures_on is None
    # 期日が入金日より前でも記録できる (前後は見ない)
    early = services.record_payment(
        invoice, RECEIVED_ON, Decimal("1000"), user, method=Payment.METHOD_BILL, matures_on=date(2026, 12, 1)
    )
    assert Payment.objects.get(pk=early.pk).matures_on == date(2026, 12, 1)


@pytest.mark.django_db
def test_unknown_method_text_lists_e_bond(user, invoice):
    with pytest.raises(ValueError) as caught:
        services.record_payment(invoice, RECEIVED_ON, Decimal("1000"), user, method="check")
    assert "振込 / 現金 / 手形 / 電子記録債権 / その他" in str(caught.value)


def test_method_choices_order():
    assert [label for _value, label in Payment.METHOD_CHOICES] == ["振込", "現金", "手形", "電子記録債権", "その他"]


# --- 入金の種 ---


def test_seed_reads_e_bond_word():
    assert demo_seed._method("電子記録債権") == Payment.METHOD_E_BOND
    with pytest.raises(ValueError, match="振込・現金・手形・電子記録債権・その他"):
        demo_seed._method("小切手")


@pytest.mark.django_db
def test_seed_reads_maturity(user, customer, invoice):
    entry = {
        "得意先の取引先コード": customer.code,
        "発行日": ISSUED_ON.isoformat(),
        "入金": [
            {"入金日": "2026-12-25", "金額": 3000, "入金の方法": "電子記録債権", "期日": "2027-03-25", "備考": ""},
            {"入金日": "2026-12-26", "金額": 1000, "入金の方法": "振込", "期日": "", "備考": ""},
        ],
    }
    assert demo_seed._load_invoice(entry, user) == (2, 0)
    rows = list(Payment.objects.order_by("pk"))
    assert [(row.method, row.matures_on) for row in rows] == [(Payment.METHOD_E_BOND, MATURES_ON), (Payment.METHOD_TRANSFER, None)]


# --- 入金の画面 ---


def _post(method, matures_on="", **extra):
    return {"received_on": "2026-12-25", "amount": "3000", "method": method, "matures_on": matures_on, "note": "", **extra}


def _assert_field_after_method(body):
    assert body.index('name="method"') < body.index('name="matures_on"') < body.index('name="note"')
    assert ">電子記録債権</option>" in body
    assert "手形と電子記録債権のときは必ず入れます。" in body


@pytest.mark.django_db
def test_payment_screen_has_maturity_field(logged_in, invoice):
    response = logged_in.get(reverse("billing:payment_create", args=[invoice.pk]))
    assert response.status_code == 200
    _assert_field_after_method(response.content.decode())


@pytest.mark.django_db
def test_outside_payment_screen_has_maturity_field(logged_in, loose_shipment):
    response = logged_in.get(reverse("billing:shipment_payment_create", args=[loose_shipment.pk]))
    assert response.status_code == 200
    _assert_field_after_method(response.content.decode())


@pytest.mark.django_db
def test_bill_without_maturity_is_field_error(logged_in, invoice):
    url = reverse("billing:payment_create", args=[invoice.pk])
    response = logged_in.post(url, _post(Payment.METHOD_BILL))
    assert response.status_code == 200
    assert FIELD_ERROR in response.content.decode()
    assert response.context["form"].errors["matures_on"] == [FIELD_ERROR]
    assert Payment.objects.count() == 0

    saved = logged_in.post(url, _post(Payment.METHOD_BILL, "2027-03-25"))
    assert saved.status_code == 302
    assert Payment.objects.get().matures_on == MATURES_ON


@pytest.mark.django_db
def test_outside_e_bond_without_maturity_is_field_error(logged_in, loose_shipment):
    url = reverse("billing:shipment_payment_create", args=[loose_shipment.pk])
    response = logged_in.post(url, _post(Payment.METHOD_E_BOND))
    assert response.status_code == 200
    assert response.context["form"].errors["matures_on"] == [FIELD_ERROR]
    assert Payment.objects.count() == 0

    saved = logged_in.post(url, _post(Payment.METHOD_E_BOND, "2027-03-25"))
    assert saved.status_code == 302
    payment = Payment.objects.get()
    assert (payment.method, payment.matures_on, payment.shipment_id) == (Payment.METHOD_E_BOND, MATURES_ON, loose_shipment.pk)
