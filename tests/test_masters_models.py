"""masters の表の形 (01 の 4 節。文字数の上限と日本語の名前)。"""

import re

import pytest
from django.db import IntegrityError, models, transaction

from seizou.masters.models import Item, ItemProcess, Partner, PartnerItem, Process

MASTER_MODELS = [Partner, Item, PartnerItem, Process, ItemProcess]
JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


def _own_fields(model):
    return [field for field in model._meta.fields if not field.auto_created]


def test_models_have_japanese_verbose_names():
    for model in MASTER_MODELS:
        assert JAPANESE.search(str(model._meta.verbose_name)), model.__name__
        for field in _own_fields(model):
            label = f"{model.__name__}.{field.name}"
            assert JAPANESE.search(str(field.verbose_name)), label


def test_max_lengths_follow_the_limits():
    # 識別子 35・名称 100・備考 500・コード 10。15 は取引先の種類の列だけ。
    # 支払日は 1〜31 の数の列 (支払の月と 2 列に分けた。31 は月末) なので、字の上限を持たない。
    allowed = {35, 100, 500, 10, 15}
    seen = {}
    for model in MASTER_MODELS:
        for field in _own_fields(model):
            if isinstance(field, models.CharField):
                assert field.max_length in allowed, f"{model.__name__}.{field.name}"
                seen.setdefault(field.max_length, []).append(f"{model.__name__}.{field.name}")
    assert seen[15] == ["Partner.role"]
    assert isinstance(Partner._meta.get_field("payment_day"), models.PositiveSmallIntegerField)


@pytest.mark.django_db
def test_part_no_is_unique():
    Item.objects.create(part_no="T-001", name="作り物の軸")
    with pytest.raises(IntegrityError), transaction.atomic():
        Item.objects.create(part_no="T-001", name="作り物の別の軸")


@pytest.mark.django_db
def test_partner_part_no_is_unique_per_partner():
    first = Partner.objects.create(code="C001", name="作り物の得意先", role="customer")
    second = Partner.objects.create(code="C002", name="作り物の別の得意先", role="customer")
    item = Item.objects.create(part_no="T-001", name="作り物の軸")
    PartnerItem.objects.create(partner=first, item=item, partner_part_no="X-1")
    # 別の取引先なら同じ取引先の品番でもよい。
    PartnerItem.objects.create(partner=second, item=item, partner_part_no="X-1")
    with pytest.raises(IntegrityError), transaction.atomic():
        PartnerItem.objects.create(partner=first, item=item, partner_part_no="X-1")


@pytest.mark.parametrize(
    ("role", "is_customer", "is_subcontractor"),
    [
        ("customer", True, False),
        ("subcontractor", False, True),
        ("both", True, True),
    ],
)
def test_partner_role_properties(role, is_customer, is_subcontractor):
    partner = Partner(code="C001", name="作り物の取引先", role=role)
    assert partner.is_customer is is_customer
    assert partner.is_subcontractor is is_subcontractor
