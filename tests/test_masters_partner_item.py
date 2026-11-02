"""取引先ごとの品番 (map_partner_item と resolve_partner_item)。"""

import pytest
from django.contrib.auth import get_user_model

from seizou.masters import services
from seizou.masters.models import Item, Partner, PartnerItem


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def partner(db):
    return Partner.objects.create(code="C001", name="作り物の得意先", role="customer")


@pytest.fixture
def item(db):
    return Item.objects.create(part_no="T-100", name="作り物の軸")


@pytest.mark.django_db
def test_map_then_resolve(user, partner, item):
    mapping = services.map_partner_item(
        partner, item, "X-1", user, partner_item_name="作り物の得意先の軸"
    )
    assert isinstance(mapping, PartnerItem)
    assert mapping.partner_item_name == "作り物の得意先の軸"
    assert services.resolve_partner_item(partner, "X-1") == item


@pytest.mark.django_db
def test_resolve_unknown_returns_none(user, partner, item):
    assert services.resolve_partner_item(partner, "X-1") is None
    services.map_partner_item(partner, item, "X-1", user)
    other = Partner.objects.create(code="C002", name="作り物の別の得意先", role="customer")
    # 別の取引先の同じ品番は引けない。
    assert services.resolve_partner_item(other, "X-1") is None
    assert services.resolve_partner_item(partner, "X-2") is None


@pytest.mark.django_db
def test_duplicate_mapping_is_refused(user, partner, item):
    services.map_partner_item(partner, item, "X-1", user)
    # 同じ品目を指していても断る。
    with pytest.raises(ValueError, match="X-1"):
        services.map_partner_item(partner, item, "X-1", user)
    other_item = Item.objects.create(part_no="T-200", name="作り物の板")
    with pytest.raises(ValueError, match="X-1"):
        services.map_partner_item(partner, other_item, "X-1", user)
    assert PartnerItem.objects.count() == 1

    # 別の取引先なら同じ取引先の品番を足せる。
    other = Partner.objects.create(code="C002", name="作り物の別の得意先", role="customer")
    services.map_partner_item(other, other_item, "X-1", user)
    assert services.resolve_partner_item(other, "X-1") == other_item
    assert services.resolve_partner_item(partner, "X-1") == item
