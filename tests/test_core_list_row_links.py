"""一覧の行の 1 列目の link の見た目 (初見テスト R5 の V62・V57。候補 C33 の A)。

1 列目の番号 (請求書番号・指示書番号・自社品番) は、指を乗せる前から下線と主の色が出る link (link link-primary) にし、
指を乗せたときだけ下線を出す link-hover を使わない。名前の欄の master_link (link master-link。押すと小窓) は今のまま
(決め C33-1 = a「変えない (名前は小窓のまま)」)。fixture の形は tests/test_billing_views.py (請求書)・
tests/test_progress_board.py (指示書と工程)・tests/test_masters_views.py (部品マスターの一覧) を写した。作り物のデータだけを使う。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing.models import Invoice
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.workorders import services as workorders_services
from tests.billing_helpers import shipped

ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer", "closing_day": 20, "payment_month": "next", "payment_day": 25},
        user,
    )


@pytest.fixture
def line(user, customer, item):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _body(response):
    assert response.status_code == 200
    return response.content.decode()


def _anchor(body, href):
    """href へ移る <a ...> の開きの tag (class・href の順に書く形)。最初の 1 つ。"""
    match = re.search(rf'<a class="[^"]*" href="{re.escape(href)}"[^>]*>', body)
    assert match, f"{href} への link がありません"
    return match.group(0)


def _assert_number_link(body, href, head):
    """1 列目の番号の link: class が head で始まり、その tag に link-hover が無い。"""
    tag = _anchor(body, href)
    assert tag.startswith(f'<a class="{head}'), tag
    assert "link-hover" not in tag, tag


def _assert_master_link(body, href):
    """名前の欄の link: 今のまま master_link (class="link master-link"。押すと小窓)。"""
    tag = _anchor(body, href)
    assert tag.startswith('<a class="link master-link" '), tag


@pytest.mark.django_db
def test_invoice_list_number_is_primary_link(client_logged_in, user, customer, line):
    shipment = shipped(user, customer, line, "4")
    response = client_logged_in.post(
        reverse("billing:invoice_create"),
        {"customer": customer.pk, "shipments": [shipment.pk], "issued_on": "2026-11-30"},
    )
    assert response.status_code == 302
    invoice = Invoice.objects.get()

    body = _body(client_logged_in.get(reverse("billing:invoice_list")))

    _assert_number_link(body, reverse("billing:invoice_detail", args=[invoice.pk]), "link link-primary")
    _assert_master_link(body, reverse("masters:partner_modal", args=[customer.pk]))


@pytest.mark.django_db
def test_progress_board_workorder_no_is_primary_link(client_logged_in, user, line, item):
    cut = Process.objects.create(code="CUT", name="切断", sort_order=1)
    workorder = workorders_services.issue_workorder(
        line, Decimal("10"), [{"process": cut, "planned_qty": Decimal("10")}], user
    )

    body = _body(client_logged_in.get(reverse("progress:progress_list")))

    _assert_number_link(body, reverse("workorders:workorder_detail", args=[workorder.pk]), "link link-primary rounded")
    _assert_master_link(body, reverse("masters:item_modal", args=[item.pk]))


@pytest.mark.django_db
def test_item_list_part_no_is_primary_link_and_name_stays_modal(client_logged_in, item):
    body = _body(client_logged_in.get(reverse("masters:item_list")))

    _assert_number_link(body, reverse("masters:item_detail", args=[item.pk]), "link link-primary")
    # 決め C33-1 = a: 品名は今のまま小窓を開く (詳細の画面へ移さない)
    _assert_master_link(body, reverse("masters:item_modal", args=[item.pk]))
