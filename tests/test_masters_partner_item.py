"""部品の取引先 (map_partner_item と resolve_partner_item と、足す・直す・消す services。C98)。"""

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


# ---------------------------------------------------------------- 部品の取引先を足す・直す・消す (C98 の その 1)

BLANK_REFUSAL = "保存できません。この取引先には、品番が空の行がもうあります。単価はその行に足してください。"


def _add(partner, item, user, part_no="", name=""):
    return services.add_partner_item(
        {"partner": partner, "item": item, "partner_part_no": part_no, "partner_item_name": name}, user
    )


@pytest.mark.django_db
def test_add_blank_row_does_not_grow(user, partner, item):
    # (a) 品番の空の行ができる。同じ取引先と部品を空でもう一度足すと、行は 1 つのままで緑の知らせの字が付く (C98-4)
    row = _add(partner, item, user)
    assert row.partner_part_no == ""
    again = _add(partner, item, user, part_no="  ")
    assert again.pk == row.pk
    assert again.saved_message == "この取引先は、もうこの部品に登録してあります。"
    assert PartnerItem.objects.count() == 1


@pytest.mark.django_db
def test_add_blank_rows_for_two_items(user, partner, item):
    # (b) 同じ取引先で別の部品 2 つに空の行 → 2 行 (空の字どうしが同じ品番と見なされない)
    other_item = Item.objects.create(part_no="T-200", name="作り物の板")
    _add(partner, item, user)
    _add(partner, other_item, user)
    assert PartnerItem.objects.filter(partner=partner, partner_part_no="").count() == 2


@pytest.mark.django_db
def test_map_fills_the_blank_row(user, partner, item):
    # (c) 空の行がある取引先と部品に品番を結ぶと、行は 1 つのままで、その行に品番と (空だった) 品名が入る
    blank = _add(partner, item, user)
    mapped = services.map_partner_item(partner, item, "X-1", user, partner_item_name="作り物の軸")
    assert mapped.pk == blank.pk
    assert PartnerItem.objects.count() == 1
    blank.refresh_from_db()
    assert (blank.partner_part_no, blank.partner_item_name) == ("X-1", "作り物の軸")
    assert services.resolve_partner_item(partner, "X-1") == item


@pytest.mark.django_db
def test_map_keeps_the_name_of_the_blank_row(user, partner, item):
    _add(partner, item, user, name="相手の呼び名")
    services.map_partner_item(partner, item, "X-1", user, partner_item_name="作り物の軸")
    assert PartnerItem.objects.get().partner_item_name == "相手の呼び名"


@pytest.mark.django_db
def test_resolve_blank_part_no_is_none(user, partner, item):
    # (d) 品番の空の行は自動のつなぎに使わない (C19-1)
    _add(partner, item, user)
    assert services.resolve_partner_item(partner, "") is None
    assert services.resolve_partner_item(partner, "  ") is None


@pytest.mark.django_db
def test_add_taken_part_no_is_refused(user, partner, item):
    services.map_partner_item(partner, item, "X-1", user)
    other_item = Item.objects.create(part_no="T-200", name="作り物の板")
    with pytest.raises(ValueError, match="既に T-100 作り物の軸 に対応しています"):
        _add(partner, other_item, user, part_no="X-1")
    assert PartnerItem.objects.count() == 1


@pytest.mark.django_db
def test_update_partner_item(user, partner, item):
    # (e) 品番を変えられる。同じ取引先のほかの行の品番にすると今の字の断り。部品と取引先の鍵は断る
    row = services.map_partner_item(partner, item, "X-1", user)
    other_item = Item.objects.create(part_no="T-200", name="作り物の板")
    services.map_partner_item(partner, other_item, "X-2", user)
    services.update_partner_item(row, {"partner_part_no": " X-3 ", "partner_item_name": "新しい呼び名"}, user)
    row.refresh_from_db()
    assert (row.partner_part_no, row.partner_item_name) == ("X-3", "新しい呼び名")
    with pytest.raises(ValueError, match="品番 X-2 は、既に T-200 作り物の板 に対応しています"):
        services.update_partner_item(row, {"partner_part_no": "X-2"}, user)
    with pytest.raises(ValueError, match="知らない項目 \\(partner\\)"):
        services.update_partner_item(row, {"partner": partner}, user)
    row.refresh_from_db()
    assert row.partner_part_no == "X-3"


@pytest.mark.django_db
def test_update_to_blank_keeps_one_blank_row(user, partner, item):
    # (e) 品番のある行が 2 つ (X-1・X-2) なら 1 つを空にできる。空の行がもうあれば、ほかの行を空にする保存を断る (C98-4)
    first = services.map_partner_item(partner, item, "X-1", user)
    second = services.map_partner_item(partner, item, "X-2", user)
    services.update_partner_item(first, {"partner_part_no": ""}, user)
    first.refresh_from_db()
    assert first.partner_part_no == ""
    with pytest.raises(ValueError) as refused:
        services.update_partner_item(second, {"partner_part_no": ""}, user)
    assert str(refused.value) == BLANK_REFUSAL
    second.refresh_from_db()
    assert second.partner_part_no == "X-2"


@pytest.mark.django_db
def test_delete_partner_item_keeps_attachments(user, partner, item, settings, tmp_path):
    # (f) 行が消えて None。もう一度消すと断る。付けた添付ファイルは残り、付けた先は「(削除された記録)」
    from django.core.files.uploadedfile import SimpleUploadedFile

    from seizou.attachments import services as attachments_services

    settings.MEDIA_ROOT = tmp_path / "media"
    row = services.map_partner_item(partner, item, "X-1", user)
    attachment = attachments_services.add_attachment(
        row, SimpleUploadedFile("作り物の見積書.pdf", b"%PDF-1.4\n"), user, kind="other", note="見積書"
    )
    assert services.delete_partner_item(row, user) is None
    assert not PartnerItem.objects.filter(pk=row.pk).exists()
    with pytest.raises(ValueError) as refused:
        services.delete_partner_item(row, user)
    assert str(refused.value) == "削除できません。この部品の取引先の行は既に消してあります。"
    attachment.refresh_from_db()
    assert attachment.target_label() == "(削除された記録)"


@pytest.mark.django_db
def test_str_of_blank_row(user, partner, item):
    row = _add(partner, item, user)
    assert str(row) == "作り物の得意先 (自社品番 T-100)"
