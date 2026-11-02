"""出荷の画面 (02 の出荷。core の土台の上)。Django の test client で HTML と表の状態を見る。

受注より多い出荷のポカよけ (R01) の画面のテストは tests/test_r01_over_shipment.py にある。
ブラウザでの確かめ (受注の明細の選ぶ欄の候補) は朝の E2E。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.shipping.models import Shipment

ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def order(user):
    """数量 10 の明細 1 行の受注。"""
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )


def _post(line, qty, shipped_at="2026-11-05", note=""):
    return {"order_line": line.pk, "qty": str(qty), "shipped_at": shipped_at, "note": note}


@pytest.mark.django_db
def test_shipment_list_requires_login(client, order):
    for name in ("shipping:shipment_list", "shipping:shipment_create"):
        response = client.get(reverse(name))
        assert response.status_code == 302
        assert reverse("core:login") in response["Location"]


@pytest.mark.django_db
def test_ship_partial_four_and_six_passes(logged_in, order):
    line = order.lines.get()
    form_page = logged_in.get(reverse("shipping:shipment_create"))
    assert form_page.status_code == 200
    body = form_page.content.decode()
    assert f'data-select-url="{reverse("orders:orderline_select")}"' in body
    assert "受注の明細" in body and "出荷日" in body

    first = logged_in.post(reverse("shipping:shipment_create"), _post(line, 4, note="作り物の備考"))
    shipment = Shipment.objects.get()
    assert first.status_code == 302
    assert first["Location"] == reverse("shipping:shipment_detail", args=[shipment.pk])
    assert shipment.note == "作り物の備考"
    order.refresh_from_db()
    assert order.status == Order.STATUS_PARTIAL

    detail = logged_in.get(first["Location"])
    detail_body = detail.content.decode()
    assert detail.status_code == 200
    assert shipment.shipment_no in detail_body and "作り物の備考" in detail_body

    second = logged_in.post(reverse("shipping:shipment_create"), _post(line, 6))
    assert second.status_code == 302
    second_body = logged_in.get(second["Location"]).content.decode()
    assert "alert-warning" not in second_body  # 合計がちょうど 10 なので知らせない
    order.refresh_from_db()
    assert order.status == Order.STATUS_SHIPPED
    assert orders_services.remaining_qty(line) == Decimal("0")
    assert Shipment.objects.count() == 2


@pytest.mark.django_db
def test_ship_to_cancelled_order_is_r05_red(logged_in, user, order):
    line = order.lines.get()
    orders_services.cancel_order(order, user)
    response = logged_in.post(reverse("shipping:shipment_create"), _post(line, 4))
    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body and "R05" in body
    assert Shipment.objects.count() == 0


@pytest.mark.django_db
def test_shipment_list_shows_order_and_item(logged_in, order):
    line = order.lines.get()
    logged_in.post(reverse("shipping:shipment_create"), _post(line, 4))
    shipment = Shipment.objects.get()

    response = logged_in.get(reverse("shipping:shipment_list"))
    assert response.status_code == 200
    body = response.content.decode()
    assert shipment.shipment_no in body
    assert order.order_no in body
    assert "T-100" in body and "作り物の得意先" in body
    assert reverse("shipping:shipment_create") in body  # 「新しく入れる」

    found = logged_in.get(reverse("shipping:shipment_list"), {"q": "T-100"})
    assert [row["obj"] for row in found.context["rows"]] == [shipment]
    missing = logged_in.get(reverse("shipping:shipment_list"), {"q": "作り物でない"})
    assert missing.context["rows"] == []


@pytest.mark.django_db
def test_menu_has_shipping_entries(logged_in):
    response = logged_in.get(reverse("core:home"))
    body = response.content.decode()
    assert reverse("shipping:shipment_list") in body
    assert "出荷" in body
