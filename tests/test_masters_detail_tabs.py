"""部品と取引先の詳細のタブ (初見テスト 2 回目の所見: 字の並びでなく表で出す。本人の決め 11-03 09:2x: 取引先の受注履歴と発注履歴)。

core の詳細の block tab_rows を masters のテンプレートが差し替える。ほかのタブ (標準の工程) は core の形のまま。
"""

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters.models import Item, Partner, PartnerItem
from seizou.orders.models import Order
from seizou.purchasing.models import PurchaseOrder


@pytest.fixture
def client_logged_in(client, db):
    client.force_login(get_user_model().objects.create_user(username="tester", password="tests-only"))
    return client


@pytest.fixture
def partner_item(db):
    item = Item.objects.create(part_no="P-1001", name="ブラケット", unit="個")
    partner = Partner.objects.create(code="C001", name="作り物の得意先", role="customer")
    return PartnerItem.objects.create(item=item, partner=partner, partner_part_no="RB-3110", partner_item_name="ブラケット 小")


def test_item_detail_shows_partner_items_as_table(client_logged_in, partner_item):
    body = client_logged_in.get(reverse("masters:item_detail", args=[partner_item.item_id])).content.decode()

    assert '<th class="font-medium">取引先の品番</th>' in body
    assert '<td class="whitespace-nowrap font-mono">RB-3110</td>' in body
    assert "ブラケット 小" in body
    # 取引先の名前は小窓を開く link
    assert reverse("masters:partner_modal", args=[partner_item.partner_id]) in body


def test_partner_detail_shows_partner_items_as_table(client_logged_in, partner_item):
    body = client_logged_in.get(reverse("masters:partner_detail", args=[partner_item.partner_id])).content.decode()

    assert '<th class="font-medium">自社品番</th>' in body
    assert '<td class="whitespace-nowrap font-mono">RB-3110</td>' in body
    assert "ブラケット" in body
    # 自社品番は部品の小窓を開く link
    assert reverse("masters:item_modal", args=[partner_item.item_id]) in body


def test_item_detail_without_partner_items_keeps_empty_state(client_logged_in, db):
    item = Item.objects.create(part_no="P-2002", name="シャフト", unit="個")
    body = client_logged_in.get(reverse("masters:item_detail", args=[item.pk])).content.decode()

    assert "まだありません。" in body
    assert '<th class="font-medium">取引先の品番</th>' not in body


def test_partner_detail_shows_order_history_as_table(client_logged_in, partner_item):
    partner = partner_item.partner
    order = Order.objects.create(order_no="SO-0001", customer=partner, order_date=date(2026, 11, 2), customer_order_no="PO-77")
    body = client_logged_in.get(reverse("masters:partner_detail", args=[partner.pk])).content.decode()

    assert "受注履歴" in body and '<th class="font-medium">受注番号</th>' in body
    assert reverse("orders:order_detail", args=[order.pk]) in body
    assert "SO-0001" in body and "2026-11-02" in body and "PO-77" in body
    assert "受注</td>" in body  # 状態は表示名


def test_partner_detail_shows_purchase_history_as_table(client_logged_in, db):
    supplier = Partner.objects.create(code="S001", name="作り物の外注先", role="supplier")
    po = PurchaseOrder.objects.create(po_no="PO-0001", partner=supplier, order_date=date(2026, 11, 3), status=PurchaseOrder.STATUS_SENT)
    body = client_logged_in.get(reverse("masters:partner_detail", args=[supplier.pk])).content.decode()

    assert "発注履歴" in body and '<th class="font-medium">発注番号</th>' in body
    assert reverse("purchasing:purchaseorder_detail", args=[po.pk]) in body
    assert "PO-0001" in body and "2026-11-03" in body and "送った" in body
    # 受注の無い取引先の受注履歴は空の案内
    assert "受注履歴が入ると、ここに並びます。" in body
