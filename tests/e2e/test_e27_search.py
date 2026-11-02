"""E-27 横断検索 (仕様書の 6 番の 3 節に番号が無い。末尾に足す番号を仮に使う。06 への追記は本体)。

手順: 上の帯の横断検索の欄に品番を入れて Enter → 結果の行の link を押す。
期待: 検索の画面 (道が `/search/`) に「部品マスター」の節と品番の行が出る。link で部品の詳細へ移る。

この試験が見る所: 上の帯の横断検索の欄 (見出し「横断して検索」) に `WK-1001` を入れて Enter を押すと、道が `/search/` になり、
「部品マスター」の節に `WK-1001` の行がある。その行の link で部品の詳細 (道が `/masters/items/<pk>/`) へ移る。
見ない所 (理由): 受注など部品マスター以外の種類の節 (回すたびに件数が変わる)、種類の chip と得意先の絞り込み。
"""

import re

from playwright.sync_api import expect

from .conftest import login, shot

PART_NO = "WK-1001"


def test_e27_cross_search(page_a, company_a):
    login(page_a, company_a)
    box = page_a.get_by_label("横断して検索")
    box.fill(PART_NO)
    box.press("Enter")
    page_a.wait_for_url(re.compile(r"/search/\?"))

    section = page_a.locator("section", has=page_a.get_by_role("heading", name=re.compile("部品マスター")))
    expect(section).to_have_count(1)
    link = section.get_by_role("link", name=PART_NO, exact=True)
    expect(link).to_have_count(1)
    shot(page_a, "E-27", "a-search")

    link.click()
    page_a.wait_for_url(re.compile(r"/masters/items/\d+/$"))
