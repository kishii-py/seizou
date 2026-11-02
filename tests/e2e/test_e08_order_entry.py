"""E-08 受注の入力 (仕様書の 6 番の 3 節)。

手順: 明細 2 行の受注を入れる → 数量に 0 → 単価を入れて金額を見る → 出荷した後に数量を出荷済みより下げる。
期待: 一覧に出る。0 は欄の横に赤の文で保存されない (入れた値は残る)。金額 = 数量 × 単価。出荷済みより下げると止まる。

この試験が見る所: ログイン → ダッシュボード → 左のメニューの「受注」→「新規登録」→ 明細 1 行の受注を保存
→ 受注の詳細 (道が `/orders/<pk>/`) と知らせ → 受注の一覧の絞り込みに印を入れて、その 1 行だけが出ること。
見ない所 (理由): 明細 2 行 (今夜の指示は 1 行)、数量 0 の赤 (余力の試験)、金額の計算 (services の試験が見る)、
出荷済みより下げる所 (編集の画面。今夜の範囲の外)。
"""

import re

from playwright.sync_api import expect

from .conftest import login, new_order, seed_name, shot, unique_mark

PART_NO = "WK-1001"
CUSTOMER_CODE = "C001"
STANDARD_PRICE = "480"


def test_e08_one_line_order_in_list(page_a, company_a):
    login(page_a, company_a)
    sidebar = page_a.locator("aside nav")

    sidebar.get_by_role("link", name="受注", exact=True).click()
    page_a.wait_for_url(re.compile(r"/orders/$"))
    page_a.get_by_role("link", name="新規登録").first.click()
    page_a.wait_for_url(re.compile(r"/orders/new/$"))

    mark = unique_mark("e08")
    order_no = new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", CUSTOMER_CODE),
        part_no=PART_NO,
        qty=10,
        unit_price=STANDARD_PRICE,
        mark=mark,
    )

    assert re.search(r"/orders/\d+/$", page_a.url)
    expect(page_a.locator(".seizou-toast[role=status]")).to_be_visible()
    shot(page_a, "E-08", "a-order-detail")

    sidebar.get_by_role("link", name="受注", exact=True).click()
    page_a.wait_for_url(re.compile(r"/orders/$"))
    page_a.fill("#list-q", mark)
    rows = page_a.locator("#rows tbody tr")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text(mark)
    expect(rows.first.locator("td").first).to_have_text(order_no)
    shot(page_a, "E-08", "a-order-in-list")
