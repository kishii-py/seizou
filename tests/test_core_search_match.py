"""横断の検索の結果で、列に出ていない項目で当たった行に出す 1 行 (候補 C77。初見テスト R5 の V120)。

core の約束 (SearchMatch・SearchRow.match・find_match) と、部品マスター・受注・マニュアルの search() が作る 1 行と、
検索の画面の 1 列目の下に描く字を見る。
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from seizou.core import fields as company_fields
from seizou.core import search as core_search
from seizou.core.search import MATCH_BEFORE, MATCH_CHARS, SearchMatch, SearchRow, SearchSection, find_match
from seizou.masters import search as masters_search
from seizou.masters.models import Item, Partner, PartnerItem

ITEM_CELLS = ("P-1001", "ブラケット", "DW-501", "個")
PARTNER_NO = [("取引先の品番", "X-9001")]


# 作り物は tests/test_core_search.py の fixture と同じ形 (別のファイルに書くのは、C36・C38 の試験の直しとぶつけないため)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(db):
    return Partner.objects.create(code="C001", name="作り物の得意先", role="customer")


@pytest.fixture
def other_customer(db):
    return Partner.objects.create(code="C002", name="作り物の別の得意先", role="both")


@pytest.fixture
def bracket(db):
    return Item.objects.create(part_no="P-1001", name="ブラケット", drawing_no="DW-501", unit="個")


@pytest.fixture
def shaft(db):
    return Item.objects.create(part_no="P-2002", name="シャフト", drawing_no="DW-777", unit="本")


# ---------------------------------------------------------------- (a) 約束の形


def test_row_match_defaults_to_none_and_display_section_carries_it():
    assert SearchRow(cells=("A-1",)).match is None
    match = SearchMatch("取引先の品番", "", "X-9", "001")
    assert match.text == "X-9001"
    section = core_search.display_section(
        SearchSection(
            kind="t",
            label="試し",
            columns=("番号", core_search.SearchColumn("数", numeric=True)),
            rows=(
                SearchRow(cells=("A-1", Decimal("1")), url=None),
                SearchRow(cells=("A-2", Decimal("2")), url="/a-2/", match=match),
            ),
            total=2,
        )
    )
    assert section["rows"][0]["match"] is None
    assert section["rows"][1]["match"] is match


# ---------------------------------------------------------------- (b) find_match を直に


def test_find_match_by_value_not_in_columns():
    assert find_match("X-9", ITEM_CELLS, PARTNER_NO) == SearchMatch("取引先の品番", "", "X-9", "001")


def test_find_match_none_when_term_is_in_columns():
    assert find_match("P-1001", ITEM_CELLS, PARTNER_NO) is None
    assert find_match("ブラケ", ITEM_CELLS, PARTNER_NO) is None


def test_find_match_folds_width_and_case_but_keeps_original_text():
    # 全角のダッシュは U+FF0D (－)
    for word in ("Ｘ－９", "x-9"):
        assert find_match(word, ITEM_CELLS, PARTNER_NO) == SearchMatch("取引先の品番", "", "X-9", "001"), word


def test_find_match_two_terms():
    assert find_match("ブラケ X-9", ITEM_CELLS, PARTNER_NO) == SearchMatch("取引先の品番", "", "X-9", "001")
    assert find_match("ブラケ DW", ITEM_CELLS, PARTNER_NO) is None


def test_find_match_skips_none_and_empty_candidates():
    candidates = [("材質", None), ("取引先の品番", ""), ("取引先の品番", "X-9001")]
    assert find_match("X-9", ITEM_CELLS, candidates) == SearchMatch("取引先の品番", "", "X-9", "001")


def test_find_match_uses_only_the_hit_line_without_markdown_marks():
    cells = ("刃物の交換の手順", "ルート", "ステップ形式", None)
    body = "バイトの番号を確かめる。\n\n- 図面 WK-1001 を見る"
    assert find_match("WK-1001", cells, [("本文", body)]) == SearchMatch("本文", "図面 ", "WK-1001", " を見る")


def test_find_match_crlf_and_checkbox_mark():
    body = "前の行\r\n- [ ] KEY を見る\r\n次の行"
    assert find_match("KEY", ("題",), [("本文", body)]) == SearchMatch("本文", "", "KEY", " を見る")


def test_find_match_cuts_long_value():
    value = "あ" * 30 + "KEY" + "い" * 30
    match = find_match("KEY", ("題",), [("本文", value)])
    assert match.hit == "KEY"
    assert match.before.startswith("…") and match.before[1:] == "あ" * MATCH_BEFORE
    assert match.after.endswith("…") and set(match.after[:-1]) == {"い"}
    assert len(match.before[1:]) + len(match.hit) + len(match.after[:-1]) == MATCH_CHARS


def test_find_match_none_when_no_candidate_has_term():
    assert find_match("Z-7", ITEM_CELLS, PARTNER_NO) is None


# ---------------------------------------------------------------- (c) 部品マスター


def test_items_match_partner_part_no(bracket, customer):
    PartnerItem.objects.create(partner=customer, item=bracket, partner_part_no="X-9001")
    match = masters_search.search("X-9").rows[0].match
    assert (match.label, match.hit) == ("取引先の品番", "X-9")
    assert masters_search.search("P-1001").rows[0].match is None


def test_items_match_only_partner_part_no_of_filtered_partner(bracket, customer, other_customer):
    PartnerItem.objects.create(partner=other_customer, item=bracket, partner_part_no="X-9555")
    PartnerItem.objects.create(partner=customer, item=bracket, partner_part_no="X-9001")
    match = masters_search.search("X-9", partner=customer.pk).rows[0].match
    assert match.after == "001"


# ---------------------------------------------------------------- (d) 会社ごとの項目 (試験の DB に拡張の表が無いので直に呼ぶ)


@pytest.fixture
def no_registration(monkeypatch):
    """会社ごとの項目の登録を空から始める (ほかの試験の登録を残さない)。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


def test_company_candidates_use_registered_label(no_registration):
    obj = SimpleNamespace(ext=SimpleNamespace(ext01="S45C"))
    company_fields.register("masters.Item", "fields.ItemExt", [{"label": "材質", "searchable": True}])
    assert masters_search.company_candidates(obj, ["ext__ext01"]) == [("材質", "S45C")]
    company_fields.register("masters.Item", "fields.ItemExt", [{"label": "処理の種類", "searchable": True}])
    assert masters_search.company_candidates(obj, ["ext__ext01"]) == [("処理の種類", "S45C")]
    assert masters_search.company_candidates(obj, []) == []
    assert masters_search.company_candidates(obj, ["unit"]) == []


def test_company_candidates_without_ext_give_none_and_find_match_skips_it(no_registration):
    company_fields.register("masters.Item", "fields.ItemExt", [{"label": "材質", "searchable": True}])
    candidates = masters_search.company_candidates(SimpleNamespace(), ["ext__ext01"])
    assert candidates == [("材質", None)]
    assert find_match("S45C", ITEM_CELLS, candidates) is None


# ---------------------------------------------------------------- (e) 部品マスターの読みの回数 (行ごとに読まない)


def test_items_search_reads_partner_part_nos_once(bracket, customer):
    PartnerItem.objects.create(partner=customer, item=bracket, partner_part_no="X-9001")
    with CaptureQueriesContext(connection) as one:
        assert len(masters_search.search("P-").rows) == 1
    for n in (2, 3):
        item = Item.objects.create(part_no=f"P-{n}00{n}", name=f"作り物の部品 {n}", unit="個")
        PartnerItem.objects.create(partner=customer, item=item, partner_part_no=f"X-{n}")
    with CaptureQueriesContext(connection) as three:
        assert len(masters_search.search("P-").rows) == 3
    assert len(three.captured_queries) == len(one.captured_queries)
