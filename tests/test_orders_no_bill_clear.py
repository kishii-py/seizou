"""受注の明細の「請求しない」の印を外す (作者の決め C107-8。orders.services.clear_no_bill と確かめの画面 orders:orderline_no_bill_clear)。

入口は受注の詳細の明細の ⋮ の「請求しないを外す」だけ (印のある明細・金額を見られる人で受注の書く権限のある人だけ。小窓)。
外すと、出し切らずに終えた一式は請求書の新規登録の一式の表に戻り、まだ請求書に入っていない出荷も請求の候補に戻る。
作り物のデータだけを使う。
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import OrderLine
from tests.billing_helpers import shipped

OCT_10 = date(2026, 10, 10)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
#: 金額の閲覧だけを効かせる (tests/test_core_line_totals.py と同じ)
NO_MONEY = {"department": "off", "write": "off", "money": "on"}
#: 書く権限だけを効かせる (金額は見られる)
NO_WRITE = {"department": "off", "write": "on", "money": "off"}


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def line(user, customer):
    """一式 (300・50000) を 100 出荷して納品書を発行し、受注を取り消した明細 (出し切らずに終えた一式)。"""
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
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
def test_clear_no_bill(user, line):
    services.mark_no_bill(line, user)

    cleared = services.clear_no_bill(line, user)

    assert cleared.no_bill is False
    assert cleared.saved_message == "請求しないを外しました。"
    assert OrderLine.objects.get(pk=line.pk).no_bill is False
    # 印の無い明細に呼んでも偽のまま (断らない)
    assert services.clear_no_bill(line, user).no_bill is False


@pytest.mark.django_db
def test_cleared_line_returns_to_billing(user, line):
    shipment = line.shipments.get()
    services.mark_no_bill(line, user)
    assert billing_services.unbilled_lumps() == []
    assert shipment not in billing_services.billable_shipments()

    services.clear_no_bill(line, user)

    assert billing_services.unbilled_lumps() == [line]
    assert shipment in billing_services.billable_shipments()


@pytest.mark.django_db
def test_menu_item_only_for_marked_line_with_money_and_write(settings, client, user, line):
    client.force_login(user)
    detail = reverse("orders:order_detail", args=[line.order_id])
    clear_url = reverse("orders:orderline_no_bill_clear", args=[line.pk])
    add_url = reverse("orders:orderline_no_bill", args=[line.pk])

    body = client.get(detail).content.decode()
    assert clear_url not in body

    services.mark_no_bill(line, user)
    body = client.get(detail).content.decode()
    assert f'href="{clear_url}?next=' in body
    assert f'data-modal-form="{clear_url}"' in body
    assert "請求しないを外す" in body
    # 付ける画面の link は ⋮ に置かない (付ける画面の URL の全体で見る。外す画面の URL は付ける画面の URL の全体を含まない)
    assert add_url not in body

    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    assert clear_url not in client.get(detail).content.decode()
    settings.SEIZOU = {"access_control": dict(NO_WRITE)}
    assert clear_url not in client.get(detail).content.decode()


@pytest.mark.django_db
def test_confirm_screen_clears_and_returns(client, user, line):
    services.mark_no_bill(line, user)
    client.force_login(user)
    url = reverse("orders:orderline_no_bill_clear", args=[line.pk])
    back = reverse("orders:order_detail", args=[line.order_id])

    page = client.get(url)
    assert page.status_code == 200
    assert f"{line.order.order_no}-1 の請求しないを外す" in page.content.decode()

    modal_page = client.get(url, **MODAL)
    assert f"{line.order.order_no}-1 の請求しないを外す" in modal_page.content.decode()

    response = client.post(url, {"next": back})
    assert response.status_code == 302
    assert response["Location"] == back
    assert OrderLine.objects.get(pk=line.pk).no_bill is False

    services.mark_no_bill(line, user)
    modal = client.post(url, {}, **MODAL)
    assert modal.status_code == 204
    assert json.loads(modal["HX-Trigger"])["seizou:saved"]["message"] == "請求しないを外しました。"
    assert OrderLine.objects.get(pk=line.pk).no_bill is False
