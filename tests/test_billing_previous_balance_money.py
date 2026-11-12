"""請求書の前回の残 (Invoice.previous_balance) を、金額の項目として扱う (507 回目の本体の直し。依頼書 N409 の記録の迷った事 3)。

訳: 前回の残は金額だが、金額の項目の名前の集まり (core.access の MONEY_FIELDS) に名前が無かった。変更の履歴は、名前で金額かどうかを
見分けるので、設定が「載せる」の会社で請求書を作った時の履歴の行に、前回の残が「数」として残り、金額の閲覧の無い人にも見えた。
名前を集まりに足し、履歴・詳細・AI の道具のどれでも、ほかの金額と同じ扱いにする。
得意先は月末締め。単価 1,000・消費税 10%。作り物のデータは全部 services で作る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.billing import services
from seizou.billing.models import Invoice
from seizou.core import access, history
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

PASSWORD = "kumo-tests-2611"
MONEY_ON = {"department": "off", "write": "off", "money": "on"}


def _account(login_id, **flags):
    """作り物の利用者 (部署も権限も無し)。see_money=True で金額の閲覧あり (無ければ無し)。"""
    accounts_services.issue_account(login_id, PASSWORD, [], [], flags, None)
    return get_user_model().objects.get(username=login_id)


def test_previous_balance_is_a_money_name():
    assert access.is_money_name("previous_balance")
    assert history.value_type(Invoice._meta.get_field("previous_balance")) == "money"
    # 数えた日時は金額でない (仕組みのための列で、履歴にも残さない)
    assert not access.is_money_name("previous_balance_at")


@pytest.mark.django_db
def test_history_hides_previous_balance_from_people_without_money(settings, client, user):
    settings.SEIZOU = {"invoice_previous_balance": True}
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer", "closing_day": 31, "payment_month": "next", "payment_day": 25},
        user,
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("100"), "unit_price": Decimal("1000")}], user, order_date=date(2026, 8, 1)
    )
    line = order.lines.get()
    for qty, shipped_at in (("10", date(2026, 9, 10)), ("20", date(2026, 10, 10))):
        shipment = shipped(user, customer, line, qty, shipped_at)
        _from, period_to = services.closing_period(customer, shipped_at)
        invoice = services.create_invoice(customer, [shipment], user, issued_on=period_to)
    # 10 月分の請求書の前回の残 = 9 月分の残 (10 × 1,000 × 1.1)
    assert invoice.previous_balance == Decimal("11000")
    url = reverse("billing:invoice_detail", args=[invoice.pk])

    settings.SEIZOU = {"invoice_previous_balance": True, "access_control": dict(MONEY_ON)}
    client.force_login(_account("keiri", see_money=True))
    seen = client.get(url).content.decode()
    assert "前回の残" in seen and "11,000" in seen  # 金額の閲覧のある人には、履歴に前回の残が出る

    client.force_login(_account("genba"))
    response = client.get(url)
    assert response.status_code == 200
    hidden = response.content.decode()
    assert "前回の残" not in hidden
    assert "11,000" not in hidden and "11000" not in hidden
