"""横断の検索は、部署で開けない部品を探さない (Claude Security の走査の候補 11-07)。

部署の判定が on のとき、一覧が 403 になる部品 (例 受注) の行を、検索の画面から読めないようにする。
種類の chip と「探した項目」の 1 行にも、その部品を出さない。部署の判定が log (既定) と off なら今までどおり全部を探す。
人と部署は accounts の services で作る。名前と品番は作り物。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services

User = get_user_model()

PASSWORD = "kumo-tests-2611"


@pytest.fixture
def admin(db):
    return User.objects.create_superuser("admin", password=PASSWORD)


@pytest.fixture
def order(admin):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, admin)
    shaft = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, admin)
    return orders_services.create_order(
        customer, [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("1500")}], admin, order_date=date(2026, 11, 2)
    )


@pytest.fixture
def genba(order):
    """製造の部署 (受注を開けない。部品マスターは開ける)。"""
    seizo = accounts_services.create_department("製造", ["workorders", "progress", "masters"], ["workorders"], None)
    accounts_services.issue_account("genba", PASSWORD, [seizo], [], {}, None)
    return User.objects.get(username="genba")


def _search(client, user, params):
    client.force_login(User.objects.get(pk=user.pk))
    response = client.get(reverse("core:search"), params)
    assert response.status_code == 200
    return response


def _kinds(response):
    return [section["kind"] for section in response.context["sections"]]


def test_department_on_skips_parts_outside_department(settings, client, genba, order):
    settings.SEIZOU = {"access_control": {"department": "on"}}

    response = _search(client, genba, {"q": "T-100"})

    assert _kinds(response) == ["items"]
    assert "orders" not in [chip["kind"] for chip in response.context["chips"]]
    assert "受注" not in response.context["kind_labels"].split("・")
    assert "受注" not in [row["label"] for row in response.context["searched_fields"]]
    assert order.order_no not in response.content.decode()


def test_department_on_kind_outside_department_falls_back_to_all(settings, client, genba):
    settings.SEIZOU = {"access_control": {"department": "on"}}

    response = _search(client, genba, {"q": "T-100", "kind": "orders"})

    assert response.context["kind"] == "all"
    assert _kinds(response) == ["items"]


@pytest.mark.parametrize("state", ["log", "off"])
def test_department_log_and_off_search_everything(settings, client, genba, state):
    settings.SEIZOU = {"access_control": {"department": state}}

    response = _search(client, genba, {"q": "T-100"})

    assert set(_kinds(response)) == {"items", "orders"}
    assert "orders" in [chip["kind"] for chip in response.context["chips"]]


def test_department_on_insider_searches_orders(settings, client, order):
    settings.SEIZOU = {"access_control": {"department": "on"}}
    sales = accounts_services.create_department("受注と出荷", ["orders", "shipping", "masters"], ["orders"], None)
    accounts_services.issue_account("jimu", PASSWORD, [sales], [], {}, None)

    response = _search(client, User.objects.get(username="jimu"), {"q": "T-100"})

    assert set(_kinds(response)) == {"items", "orders"}
