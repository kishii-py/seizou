"""横断の検索の結果で、列に出ていない項目で当たった行に出す 1 行 (候補 C77。初見テスト R5 の V120)。

core の約束 (SearchMatch・SearchRow.match・find_match) と、部品マスター・受注・マニュアルの search() が作る 1 行と、
検索の画面の 1 列目の下に描く字を見る。
"""

from decimal import Decimal

from seizou.core import search as core_search
from seizou.core.search import MATCH_BEFORE, MATCH_CHARS, SearchMatch, SearchRow, SearchSection, find_match

ITEM_CELLS = ("P-1001", "ブラケット", "DW-501", "個")
PARTNER_NO = [("取引先の品番", "X-9001")]


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
