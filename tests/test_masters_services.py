"""masters の保存の関数 (01 の 10.4)。"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.core.models import Company
from seizou.masters import services
from seizou.masters.models import Item, Partner


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.mark.django_db
def test_create_and_update_item(user):
    item = services.create_item(
        {
            "part_no": "T-100",
            "name": "作り物の軸",
            "unit": "個",
            "standard_price": Decimal("120.50"),
        },
        user,
    )
    assert isinstance(item, Item)
    assert item.pk

    updated = services.update_item(item, {"name": "作り物の軸 改"}, user)
    assert isinstance(updated, Item)
    updated.refresh_from_db()
    assert updated.name == "作り物の軸 改"
    # data に無い鍵は変わらない。
    assert updated.part_no == "T-100"
    assert updated.unit == "個"
    assert updated.standard_price == Decimal("120.50")

    services.update_item(item, {"part_no": "T-101"}, user)
    item.refresh_from_db()
    assert item.part_no == "T-101"
    assert item.name == "作り物の軸 改"


@pytest.mark.django_db
def test_duplicate_part_no_is_refused(user):
    services.create_item({"part_no": "T-100", "name": "作り物の軸"}, user)
    with pytest.raises(ValueError, match="品番 T-100 は既にあります"):
        services.create_item({"part_no": "T-100", "name": "作り物の別の軸"}, user)
    assert Item.objects.count() == 1

    # 品番を変えるときも一意を見る。自分の品番のままの更新は通る。
    other = services.create_item({"part_no": "T-200", "name": "作り物の板"}, user)
    with pytest.raises(ValueError, match="品番 T-100 は既にあります"):
        services.update_item(other, {"part_no": "T-100"}, user)
    services.update_item(other, {"part_no": "T-200", "name": "作り物の板 改"}, user)
    other.refresh_from_db()
    assert other.part_no == "T-200"


@pytest.mark.django_db
def test_create_partner_requires_valid_role(user):
    with pytest.raises(ValueError, match="区分"):
        services.create_partner({"code": "C000", "name": "作り物の取引先", "role": "supplier"}, user)
    with pytest.raises(ValueError, match="区分"):
        services.create_partner({"code": "C000", "name": "作り物の取引先"}, user)
    assert Partner.objects.count() == 0

    for number, role in enumerate(["customer", "subcontractor", "both"], start=1):
        partner = services.create_partner(
            {"code": f"C00{number}", "name": f"作り物の取引先 {number}", "role": role}, user
        )
        assert isinstance(partner, Partner)
        assert partner.role == role

    partner = Partner.objects.get(code="C001")
    with pytest.raises(ValueError, match="区分"):
        services.update_partner(partner, {"role": "supplier"}, user)
    services.update_partner(partner, {"role": "both", "closing_day": 31}, user)
    partner.refresh_from_db()
    assert partner.role == "both"
    assert partner.closing_day == 31
    assert partner.name == "作り物の取引先 1"


@pytest.mark.django_db
def test_duplicate_partner_code_is_refused(user):
    services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    with pytest.raises(ValueError, match="取引先コード C001 は既にあります"):
        services.create_partner({"code": "C001", "name": "作り物の外注先", "role": "subcontractor"}, user)
    other = services.create_partner(
        {"code": "C002", "name": "作り物の外注先", "role": "subcontractor"}, user
    )
    with pytest.raises(ValueError, match="取引先コード C001 は既にあります"):
        services.update_partner(other, {"code": "C001"}, user)


@pytest.mark.django_db
def test_unknown_keys_are_refused(user):
    with pytest.raises(ValueError, match="知らない項目"):
        services.create_item({"part_no": "T-100", "name": "作り物の軸", "ext": {"ext01": "x"}}, user)
    with pytest.raises(ValueError, match="知らない項目"):
        services.create_partner(
            {"code": "C001", "name": "作り物の得意先", "role": "customer", "color": "赤"}, user
        )
    assert Item.objects.count() == 0
    assert Partner.objects.count() == 0


@pytest.mark.django_db
def test_ext_is_not_silently_dropped(user):
    # 会社ごとの項目の登録 (core の fields.register) が無いのに ext が来たら、黙って捨てずに止める。
    ext = {"ext01": "x"}
    with pytest.raises(ValueError, match="登録されていません"):
        services.create_item({"part_no": "T-100", "name": "作り物の軸"}, user, ext=ext)
    with pytest.raises(ValueError, match="登録されていません"):
        services.create_partner(
            {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user, ext=ext
        )
    assert Item.objects.count() == 0
    assert Partner.objects.count() == 0

    item = services.create_item({"part_no": "T-100", "name": "作り物の軸"}, user)
    partner = services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    with pytest.raises(ValueError, match="登録されていません"):
        services.update_item(item, {"name": "作り物の軸 改"}, user, ext=ext)
    with pytest.raises(ValueError, match="登録されていません"):
        services.update_partner(partner, {"name": "作り物の得意先 改"}, user, ext=ext)
    item.refresh_from_db()
    partner.refresh_from_db()
    assert item.name == "作り物の軸"
    assert partner.name == "作り物の得意先"


@pytest.mark.django_db
def test_round_amount_follows_partner_setting():
    value = Decimal("123.5")
    assert services.round_amount(Partner(rounding="round"), value) == Decimal("124")
    assert services.round_amount(Partner(rounding="ceil"), value) == Decimal("124")
    assert services.round_amount(Partner(rounding="floor"), value) == Decimal("123")
    # 0.5 未満の端数で、四捨五入と切上の違いを見る。
    assert services.round_amount(Partner(rounding="round"), Decimal("123.4")) == Decimal("123")
    assert services.round_amount(Partner(rounding="ceil"), Decimal("123.4")) == Decimal("124")

    # 取引先が空のときは会社の既定。会社の行が無ければ四捨五入。
    blank = Partner(rounding="")
    assert services.round_amount(blank, value) == Decimal("124")
    Company.objects.create(name="作り物の会社", rounding="floor")
    assert services.round_amount(blank, value) == Decimal("123")
    # 取引先の値が会社の既定より優先する。
    assert services.round_amount(Partner(rounding="ceil"), Decimal("123.4")) == Decimal("124")
