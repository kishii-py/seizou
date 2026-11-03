"""ダッシュボードの 2 枚のカード (N77): 「まだ出荷していない明細」と「今月の受注」の数と、押した先の一覧の件数。

受注と出荷は services で作る (tests/test_core_dashboard.py の make_order の形)。名前は作り物だけ。
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.core import views
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.shipping import services as shipping_services

LINES_LABEL = "まだ出荷していない明細"
MONTH_LABEL = "今月の受注"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def make_order(user, customer, item, lines, *, order_date):
    """lines = [(数量, 単価, 納期), ...] の受注を作る。"""
    return services.create_order(
        customer,
        [
            {"item": item, "qty": Decimal(qty), "unit_price": Decimal(price), "due_date": due}
            for qty, price, due in lines
        ],
        user,
        order_date=order_date,
    )


def card(label):
    return next(stat for stat in views.dashboard_stats() if stat["label"] == label)


@pytest.mark.django_db
def test_lines_card_matches_orderline_list(logged_in, user, customer, shaft):
    today = timezone.localdate()
    order_a = make_order(user, customer, shaft, [("3", "100", None), ("2", "100", None)], order_date=today)
    order_b = make_order(user, customer, shaft, [("4", "100", None)], order_date=today)
    order_c = make_order(user, customer, shaft, [("5", "100", None)], order_date=today)
    # A の 1 行目だけを全部出荷する (A は一部出荷。出荷し終えた 1 行目は数えない)
    shipping_services.ship(order_a.lines.get(line_no=1), Decimal("3"), today, user)
    services.cancel_order(order_b, user)
    order_a.refresh_from_db()
    assert order_a.status == order_a.STATUS_PARTIAL

    stat = card(LINES_LABEL)
    assert stat["value"] == 2  # A の 2 行目と C
    assert stat["url"] == reverse("orders:orderline_list")
    assert stat["unit"] == "行"
    assert stat["note"] == "出荷が終わっていない受注の明細の数"

    listing = logged_in.get(stat["url"])
    assert listing.status_code == 200
    assert listing.context["paginator"].count == stat["value"]
    shown = {(row["obj"].order.order_no, row["obj"].line_no) for row in listing.context["rows"]}
    assert shown == {(order_a.order_no, 2), (order_c.order_no, 1)}
