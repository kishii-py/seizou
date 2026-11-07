"""受注の明細の「請求しない」の印を付ける (作者の決め C107-3。orders.services.mark_no_bill と確かめの画面 orders:orderline_no_bill)。

印は出し切らずに終えた一式の明細に、請求書の新規登録の一式の表から付ける。受注の詳細の明細の ⋮ には付ける入口を置かない。
作り物のデータだけを使う。
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import OrderLine
from tests.billing_helpers import shipped

OCT_10 = date(2026, 10, 10)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
#: 金額の閲覧だけを効かせる (tests/test_core_line_totals.py と同じ)
NO_MONEY = {"department": "off", "write": "off", "money": "on"}


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def line(user, customer, item):
    """一式 (300・50000) を 100 出荷して納品書を発行し、受注を取り消した明細 (出し切らずに終えた一式)。"""
    order = services.create_order(
        customer,
        [{"item": item, "qty": Decimal("300"), "unit_price": Decimal("50000"), "price_basis": "lump"}],
        user,
        order_date=date(2026, 10, 1),
    )
    line = order.lines.get()
    shipped(user, customer, line, "100", OCT_10)
    services.cancel_order(order, user)
    line.refresh_from_db()
    return line


@pytest.mark.django_db
def test_mark_no_bill(user, line):
    marked = services.mark_no_bill(line, user)

    assert marked.no_bill is True
    assert marked.saved_message == "請求しないにしました。"
    assert OrderLine.objects.get(pk=line.pk).no_bill is True
    # 2 回呼んでも真のまま (断らない)
    again = services.mark_no_bill(line, user)
    assert again.no_bill is True and again.saved_message == "請求しないにしました。"


@pytest.mark.django_db
@pytest.mark.parametrize("money", [True, False])
def test_order_detail_shows_mark_without_add_link(settings, client, user, line, money):
    if not money:
        settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    client.force_login(user)
    add_url = reverse("orders:orderline_no_bill", args=[line.pk])

    body = client.get(reverse("orders:order_detail", args=[line.order_id])).content.decode()
    assert '<span class="block text-xs text-base-content/70">請求しない</span>' not in body
    assert add_url not in body

    services.mark_no_bill(line, user)
    body = client.get(reverse("orders:order_detail", args=[line.order_id])).content.decode()
    assert '<span class="block text-xs text-base-content/70">請求しない</span>' in body
    assert add_url not in body


@pytest.mark.django_db
def test_confirm_screen_marks_and_returns_to_next(client, user, line):
    client.force_login(user)
    url = reverse("orders:orderline_no_bill", args=[line.pk])
    back = reverse("billing:invoice_create") + "?customer=1"

    page = client.get(url)
    body = page.content.decode()
    assert page.status_code == 200
    assert f"{line.order.order_no}-1 を請求しない" in body

    response = client.post(url + "?next=" + back.replace("?", "%3F").replace("=", "%3D"), {"next": back})
    assert response.status_code == 302
    assert response["Location"] == back
    assert OrderLine.objects.get(pk=line.pk).no_bill is True


@pytest.mark.django_db
def test_modal_post_returns_204_with_saved_trigger(client, user, line):
    client.force_login(user)
    url = reverse("orders:orderline_no_bill", args=[line.pk])

    page = client.get(url, **MODAL)
    assert page.status_code == 200
    assert f"{line.order.order_no}-1 を請求しない" in page.content.decode()

    response = client.post(url, {}, **MODAL)
    assert response.status_code == 204
    trigger = json.loads(response["HX-Trigger"])
    assert trigger["seizou:saved"]["message"] == "請求しないにしました。"
    assert OrderLine.objects.get(pk=line.pk).no_bill is True


@pytest.mark.django_db
def test_mark_survives_order_update(user, customer, item):
    order = services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "price_basis": "lump"}],
        user,
        order_date=date(2026, 10, 1),
    )
    line = order.lines.get()
    services.mark_no_bill(line, user)

    services.update_order(order, {}, [{"id": line.pk, "qty": Decimal("12"), "note": "作り物の備考"}], user)

    line.refresh_from_db()
    assert line.no_bill is True
    assert line.note == "作り物の備考"
