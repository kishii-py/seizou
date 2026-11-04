"""取消の後の知らせ (N106。精査 A7 の 4 節の 5・決め 11-04 14:4x の (5))。

取消の service が、返す行に saved_message (「取り消しました。」) を付け、
入力の土台 (core.views.with_saved_note) が「保存しました。」の代わりに緑の知らせに出す。
右上の知らせは get_messages(response.wsgi_request) の (level_tag, 字) で見る (tests/test_purchasing_send_receipt_polish.py と同じ形)。
取引先と品目は作り物。発注の取消 (purchasing) はこのファイルの外。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import Order

ORDER_DATE = date(2026, 11, 2)
CANCELLED = [("success", "取り消しました。")]


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def order(user):
    """作り物の得意先の、数量 10 の明細 1 行の受注。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )


def _messages(response):
    """その要求で足した右上の知らせ (level_tag と字) の並び。"""
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def test_order_cancel_says_cancelled(logged_in, order):
    response = logged_in.post(reverse("orders:order_cancel", args=[order.pk]), {"reason": "作り物の理由"})

    assert response.status_code == 302
    assert response["Location"] == reverse("orders:order_detail", args=[order.pk])
    assert _messages(response) == CANCELLED
    order.refresh_from_db()
    assert order.status == Order.STATUS_CANCELLED
