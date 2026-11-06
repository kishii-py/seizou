"""ダッシュボードの「今月の入金」のカード (core.dashboard.payments_this_month と dashboard_stats の 7 枚目)。

請求書の作り物の鎖は tests/test_billing_payment.py の形。日は timezone.localdate() から数える (関数の試験は固定の日を渡す)。
Django の test client で HTML を見る。名前は作り物だけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.billing import services as billing_services
from seizou.core import dashboard, views
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {
            "code": "C001",
            "name": "作り物の得意先",
            "role": "customer",
            "closing_day": 20,
            "payment_month": "next",
            "payment_day": 25,
        },
        user,
    )


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def invoice(user, customer, shaft):
    """出荷 4 と 6 の請求書 (小計 15,000・消費税 1,500・合計 16,500)。"""
    order = orders_services.create_order(
        customer,
        [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("1500")}],
        user,
        order_date=ORDER_DATE,
    )
    line = order.lines.get()
    shipments = []
    for qty in ("4", "6"):
        shipment = shipping_services.ship(line, Decimal(qty), SHIPPED_AT, user)
        shipping_services.create_delivery_note(customer, [shipment], user)
        shipment.refresh_from_db()
        shipments.append(shipment)
    return billing_services.create_invoice(customer, shipments, user, issued_on=ISSUED_ON)


def home(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    return response.content.decode()


class _NoParts:
    def get_model(self, app_label, name):
        raise LookupError(f"{app_label}.{name}")


@pytest.mark.django_db
def test_payments_this_month_sums_recorded_payments(user, invoice, monkeypatch):
    today = date(2026, 11, 15)
    assert dashboard.payments_this_month(today=today) == Decimal("0")

    billing_services.record_payment(invoice, date(2026, 11, 1), Decimal("6500"), user)
    billing_services.record_payment(invoice, date(2026, 11, 30), Decimal("1000"), user, method="cash")
    billing_services.record_payment(invoice, date(2026, 10, 31), Decimal("3000"), user)  # 先月 = 入らない
    cancelled = billing_services.record_payment(invoice, date(2026, 11, 10), Decimal("500"), user)
    billing_services.cancel_payment(cancelled, user)  # 取消 = 入らない

    assert dashboard.payments_this_month(today=today) == Decimal("7500")
    assert dashboard.payments_this_month(today=date(2026, 10, 2)) == Decimal("3000")

    monkeypatch.setattr(dashboard, "apps", _NoParts())
    assert dashboard.payments_this_month(today=today) is None


@pytest.mark.django_db
def test_stats_put_payments_card_after_payable_with_money_mark():
    stats = views.dashboard_stats()
    labels = [stat["label"] for stat in stats]

    card = stats[labels.index("今月の入金")]
    assert card["unit"] == ""
    assert card["money"] is True
    assert card["url"] == reverse("billing:invoice_list")
    assert card["icon"] == "yen"
    assert labels.index("今月の入金") == labels.index("今月の支払の見込み") + 1
    # 試験の settings は全部の部品が入っているので、数字のカードは 8 枚 (8 枚目は手で打たずに受けた明細 = N83)
    assert len(stats) == 8


@pytest.mark.django_db
def test_home_shows_payments_card(user, logged_in, invoice):
    billing_services.record_payment(invoice, timezone.localdate(), Decimal("2400"), user)

    body = home(logged_in)

    start = body.index("今月の入金")
    card = body[start : body.index("</p></div>", start)]
    assert "¥2,400" in card and "円" not in card
    assert f'href="{reverse("billing:invoice_list")}"' in body[body.rindex("<a", 0, start) : start]
    assert f"{timezone.localdate().month} 月に入金日のある入金" in card
    assert body.index("今月の支払の見込み") < start
    # 7 枚のときは xl 以上で 4 列 × 2 段 (6 枚の形の class は出ない)
    assert "xl:grid-cols-4" in body
    assert "xl:grid-cols-3 2xl:grid-cols-6" not in body
    for word in ("品目", "注残の明細", "はじめに"):
        assert word not in body, word


@pytest.mark.django_db
def test_home_hides_payments_without_money(user, logged_in, invoice, settings):
    billing_services.record_payment(invoice, timezone.localdate(), Decimal("2400"), user)
    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}

    body = home(logged_in)

    start = body.index("今月の入金")
    card = body[start : body.index("</p></div>", start)]
    assert "—" in card
    assert "2,400" not in card
