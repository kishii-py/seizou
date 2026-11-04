"""E-03 部品マスターの一覧 (仕様書の 6 番の 3 節)。

手順: 検索の欄に品番の一部を打つ → 見出しを押して並び替え → ページ送り → 1 ページの件数を変える。
期待: ページ全体でなく行だけが入れ替わる。「1〜50 / 全部の件数」の表示が合う。戻るボタンで前の条件に戻る。

この試験が見る所: 部品マスターの一覧 (`/masters/items/`) で `window` に印の値を置き、絞り込みの欄 `#list-q` に品番の一部を
打つと、表の全部の行の自社品番が打った字を含み、件数の表示 (「N 件中 1–M 件」) が打つ前と変わり、印の値が残る
(ページ全体を読み直していない)こと。打つ字は種の品番のうち一部だけが含む `WK-100` にした
(種の 30 品番は全部 `WK-10` を含むので、`WK-10` では件数が変わらない)。
見ない所 (理由): 見出しの並び替え・ページ送り・件数の変更・戻る (余力と今夜の範囲の外)。
"""

import re

from playwright.sync_api import expect

from .conftest import login, shot

TYPED = "WK-100"
COUNT_TEXT = re.compile(r"([\d,]+)\s*件中\s*([\d,]+)–([\d,]+)\s*件")


def test_e03_search_swaps_rows_only(page_a, company_a):
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/masters/items/")
    rows = page_a.locator("#rows")
    expect(rows.locator("tbody tr").first).to_be_visible()
    before_text = rows.inner_text()
    before = COUNT_TEXT.search(before_text)
    assert before, "件数の表示 (N 件中 1–M 件) が見つからない"

    page_a.evaluate("window.__e2e_marker = 'kept'")
    page_a.fill("#list-q", TYPED)
    expect(rows).not_to_have_text(before_text)

    after = COUNT_TEXT.search(rows.inner_text())
    assert after, "打った後の件数の表示が見つからない"
    assert after.group(0) != before.group(0)

    part_nos = rows.locator("tbody tr td:first-child").all_inner_texts()
    assert part_nos, "絞り込んだ後の行が 0 件"
    assert all(TYPED in part_no for part_no in part_nos)
    assert page_a.evaluate("window.__e2e_marker") == "kept"

    shot(page_a, "E-03", "a-search")


def test_e03_header_click_sorts(page_a, company_a):
    """E-03 の並び替え (余力): 見出し「自社品番」を押すと昇順、もう一度押すと降順になる。

    見ない所 (理由): 昇順の 1 回目は既定の並び (品番の昇順) と同じなので、「押すと逆になる」のは 2 回目から。
    """
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/masters/items/")
    header = page_a.locator("#rows thead th").filter(has_text="自社品番")
    rows = page_a.locator("#rows tbody tr td:first-child")
    expect(rows.first).to_be_visible()

    header.get_by_role("link").click()
    expect(header).to_have_attribute("aria-sort", "ascending")
    ascending = rows.all_inner_texts()
    assert ascending == sorted(ascending)

    header.get_by_role("link").click()
    expect(header).to_have_attribute("aria-sort", "descending")
    # 部品が 1 頁に収まらない置き場 (E2E を何度も回した 2 社) では、昇順の頁の末尾は全体の最大でない。
    # 降順の頁が降順に並び、先頭が昇順の頁の末尾以上であることを見る (1 頁に収まる置き場では昇順の逆と同じ)。
    expect(rows.first).not_to_have_text(ascending[0])
    descending = rows.all_inner_texts()
    assert descending == sorted(descending, reverse=True)
    assert descending[0] >= ascending[-1]
    if len(ascending) < 50:  # 1 頁の既定の行数 (core.views.DEFAULT_PAGE_SIZE) より少ない = 全部が 1 頁に収まっている
        assert descending == list(reversed(ascending))
