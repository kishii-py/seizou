"""選ぶ欄の候補 (candidates。05 の 2 節の採用の条件)。"""

import pytest

from seizou.masters import services
from seizou.masters.models import Item, Partner


def _texts(result):
    return [row["text"] for row in result["results"]]


@pytest.mark.django_db
def test_full_width_half_width_kana_and_case_match():
    Item.objects.create(part_no="T-100", name="ｱﾙﾐの板")
    Item.objects.create(part_no="T-200", name="ステンレスの軸")
    # 「ｱ」と「あ」と「ア」で同じ品目が出る。
    for query in ["ｱ", "あ", "ア", "あるみ"]:
        assert _texts(services.candidates("item", query, 1)) == ["T-100 ｱﾙﾐの板"], query
    # 全角の英数字と大文字小文字の違いも同じとみなす。
    assert _texts(services.candidates("item", "ｔ－２", 1)) == ["T-200 ステンレスの軸"]
    assert _texts(services.candidates("item", "t-1", 1)) == ["T-100 ｱﾙﾐの板"]


@pytest.mark.django_db
def test_every_term_must_match():
    Item.objects.create(part_no="T-100", name="作り物の板")
    Item.objects.create(part_no="T-101", name="作り物の軸")
    Item.objects.create(part_no="U-100", name="作り物の板")
    assert _texts(services.candidates("item", "T-1 板", 1)) == ["T-100 作り物の板"]
    # 全角の空白でも語を切る。
    assert _texts(services.candidates("item", "板　U", 1)) == ["U-100 作り物の板"]


@pytest.mark.django_db
def test_page_boundary_and_more_flag():
    for number in range(31):
        Item.objects.create(part_no=f"T-{number:03d}", name="作り物の部品")
    first = services.candidates("item", "", 1)
    assert len(first["results"]) == 30
    assert first["pagination"]["more"] is True
    second = services.candidates("item", "", 2)
    assert _texts(second) == ["T-030 作り物の部品"]
    assert second["pagination"]["more"] is False
    # 空の検索は全件。
    ids = [row["id"] for row in first["results"] + second["results"]]
    assert sorted(ids) == sorted(Item.objects.values_list("pk", flat=True))


@pytest.mark.django_db
def test_page_that_is_not_a_number_is_one():
    for number in range(31):
        Item.objects.create(part_no=f"T-{number:03d}", name="作り物の部品")
    first = services.candidates("item", "", 1)
    for page in ["abc", "", None, "2.5"]:
        assert services.candidates("item", "", page) == first, page
    assert services.candidates("item", "", "2")["results"] == services.candidates("item", "", 2)["results"]


@pytest.mark.django_db
def test_no_match_returns_empty_page():
    Item.objects.create(part_no="T-100", name="作り物の板")
    assert services.candidates("item", "存在しない語", 1) == {
        "results": [],
        "pagination": {"more": False},
    }


@pytest.mark.django_db
def test_partner_text_is_code_and_name():
    partner = Partner.objects.create(code="C001", name="作り物の得意先", role="customer")
    Partner.objects.create(code="S001", name="作り物の協力企業", role="subcontractor")
    result = services.candidates("partner", "得意", 1)
    assert result["results"] == [{"id": partner.pk, "text": "C001 作り物の得意先"}]


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="候補の種類"):
        services.candidates("order", "", 1)
