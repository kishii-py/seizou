"""部品と取引先の詳細の「取引先ごとの品番」のタブ (初見テスト 2 回目の所見: 字の並びでなく表で出す)。

core の詳細の block tab_rows を masters のテンプレートが差し替える。ほかのタブ (標準の工程) は core の形のまま。
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters.models import Item, Partner, PartnerItem


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
