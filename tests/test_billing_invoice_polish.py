"""請求書の一覧と詳細の字の直し (精査 A1 の不具合 6・想定外 S10)。

- 一覧の「残」は 3 桁区切りの金額の列 (kind が money。Python の値なので並び替えと合計は無い)。
fixture と、一覧の行を読む関数は tests/test_billing_payment_views.py の形。得意先・品目は作り物。
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters import services as masters_services
from tests.test_billing_payment_views import PASSWORD, _cells, _customer, _invoice, _order_line


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password=PASSWORD)


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def customer(user):
    return _customer(user, closing_day=20, payment_month="next", payment_day=25)


@pytest.fixture
def line(user, customer, item):
    return _order_line(user, customer, item)


@pytest.fixture
def invoice(user, customer, line):
    return _invoice(user, customer, line)


def test_list_balance_is_money_column(client_logged_in, invoice):
    response = client_logged_in.get(reverse("billing:invoice_list"))

    balance = next(spec for spec in response.context["columns"] if spec.name == "balance")
    assert balance.numeric and balance.money
    assert not balance.sortable
    body = response.content.decode()
    assert "16,500" in body
    assert "16500" not in body
    # 列の値は Decimal のまま (字にするのはテンプレート)
    assert _cells(response, invoice.invoice_no)["balance"] == Decimal("16500.00")
