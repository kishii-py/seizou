"""請求書の外で受け取った入金の入力 (billing.forms.ShipmentPaymentForm) の金額の初めの値 (開発の記録 docs/nights/20261111/N377.md の (1))。

初めの値は、出荷の金額 (税抜。billing.services.shipment_amount) に消費税 (billing.services.consumption_tax) を足した税込の金額。
消費税は請求書と同じ 1 か所 (_tax) で計算し、丸める向きは得意先の消費税の端数の向き (空なら会社の既定)。
説明の字は税込・税抜・消費税の 3 つの金額を出す。金額の閲覧の無い人には初めの値を入れず、説明に金額を出さない。
作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from seizou.billing import services
from seizou.core.models import Company
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

#: 金額の閲覧だけを効かせる (tests/test_billing_outside_payment.py と同じ)
NO_MONEY = {"department": "off", "write": "off", "money": "on"}
#: 会社の消費税の端数の向きの選択肢のうち、四捨五入の値
ROUND_HALF = next(value for value, label in Company.ROUNDING_CHOICES if label == "四捨五入")
#: 会社の消費税の端数の向きの選択肢のうち、切捨の値
ROUND_DOWN = next(value for value, label in Company.ROUNDING_CHOICES if label == "切捨")


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _customer(user, code, tax_rounding=""):
    data = {"code": code, "name": f"作り物の得意先 {code}", "role": "customer"}
    if tax_rounding:
        data["tax_rounding"] = tax_rounding
    return masters_services.create_partner(data, user)


def _shipment(user, customer, item):
    """部品の単位の明細 (単価 275) を 5 出荷して納品書を発行した出荷 (税抜の金額 1,375)。"""
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("275"), "price_basis": "unit"}],
        user,
        order_date=date(2026, 10, 1),
    )
    return shipped(user, customer, order.lines.get(), "5", timezone.localdate())


def _form(client, shipment):
    page = client.get(reverse("billing:shipment_payment_create", args=[shipment.pk]))
    assert page.status_code == 200
    return page.context["form"]


@pytest.mark.django_db
def test_initial_amount_includes_consumption_tax(client, user, item):
    """(a) 初めの値は 税抜 1,375 + consumption_tax(得意先, [1,375])。"""
    customer = _customer(user, "C001")
    shipment = _shipment(user, customer, item)
    client.force_login(user)

    form = _form(client, shipment)

    assert services.shipment_amount(customer, shipment) == Decimal("1375")
    tax = services.consumption_tax(customer, [Decimal("1375")])
    assert tax > 0
    assert form["amount"].value() == Decimal("1375") + tax


@pytest.mark.django_db
def test_initial_amount_follows_customer_tax_rounding(client, user, item):
    """(b) 得意先の消費税の端数の向きが四捨五入なら、四捨五入で丸めた消費税を足す (切捨の得意先と値が違う)。"""
    half = _customer(user, "C001", ROUND_HALF)
    down = _customer(user, "C002", ROUND_DOWN)
    half_shipment = _shipment(user, half, item)
    down_shipment = _shipment(user, down, item)
    client.force_login(user)

    half_value = _form(client, half_shipment)["amount"].value()
    down_value = _form(client, down_shipment)["amount"].value()

    # 1,375 × 10% = 137.5。四捨五入は 138、切捨は 137。
    assert half_value == Decimal("1375") + services.consumption_tax(half, [Decimal("1375")]) == Decimal("1513")
    assert down_value == Decimal("1375") + services.consumption_tax(down, [Decimal("1375")]) == Decimal("1512")
    assert half_value != down_value


@pytest.mark.django_db
def test_help_text_names_tax_included_amount(client, user, item):
    """(c) 説明の字は税込の金額と、税抜と消費税の内訳を出す。"""
    customer = _customer(user, "C001", ROUND_DOWN)
    shipment = _shipment(user, customer, item)
    line = shipment.order_line
    client.force_login(user)

    help_text = _form(client, shipment).fields["amount"].help_text

    assert "税抜 ¥1,375" in help_text
    assert "消費税 ¥" in help_text
    assert help_text == (
        f"出荷 {shipment.shipment_no} (受注 {line.order.order_no}-{line.line_no}・T-100・5 個)"
        "の金額は ¥1,512 (税抜 ¥1,375・消費税 ¥137) です。受け取った金額を入れます。"
    )


@pytest.mark.django_db
def test_no_initial_amount_without_money(settings, client, user, item):
    """(d) 金額の閲覧の無い人には、入金の記録の画面を開いた時点で断る (入金の記録は金額に関わる操作。本人の決め 11-12 07:2x。

    直す前は、初めの値と金額の説明の無い画面が開いた)。開けない画面に、金額の欄と金額の説明は出ない。
    """
    customer = _customer(user, "C001")
    shipment = _shipment(user, customer, item)
    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    client.force_login(user)

    page = client.get(reverse("billing:shipment_payment_create", args=[shipment.pk]))

    assert page.status_code == 403
    body = page.content.decode()
    assert "金額の閲覧の権限が無い方は" in body
    assert 'name="amount"' not in body
    assert f"出荷 {shipment.shipment_no} " not in body
