"""E-08 受注の入力 (仕様書の 6 番の 3 節)。

手順: 明細 2 行の受注を入れる → 数量に 0 → 単価を入れて金額を見る → 出荷した後に数量を出荷済みより下げる。
期待: 一覧に出る。0 は欄の横に赤の文で保存されない (入れた値は残る)。金額 = 数量 × 単価。出荷済みより下げると止まる。

この試験が見る所: ログイン → ダッシュボード → 左のメニューの「受注」→「新規登録」→ 明細 1 行の受注を保存
→ 受注の詳細 (道が `/orders/<pk>/`) と知らせ → 受注の一覧の絞り込みに印を入れて、その 1 行だけが出ること。
見ない所 (理由): 明細 2 行 (今夜の指示は 1 行)、数量 0 の赤 (余力の試験)、金額の計算 (services の試験が見る)、
出荷済みより下げる所 (編集の画面。今夜の範囲の外)。
"""

import re
from datetime import date

import pytest
from playwright.sync_api import expect

from .conftest import choose, login, menu_link, new_order, seed_name, shot, unique_mark

PART_NO = "WK-1001"
CUSTOMER_CODE = "C001"
STANDARD_PRICE = "480"


def test_e08_one_line_order_in_list(page_a, company_a):
    login(page_a, company_a)
    menu_link(page_a, "受注").click()
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

    menu_link(page_a, "受注").click()
    page_a.wait_for_url(re.compile(r"/orders/$"))
    page_a.fill("#list-q", mark)
    rows = page_a.locator("#rows tbody tr")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text(mark)
    expect(rows.first.locator("td").first).to_have_text(order_no)
    shot(page_a, "E-08", "a-order-in-list")


@pytest.mark.xfail(reason="06 の E-08 の「欄の横に赤の文」が今の画面で通らない: 数量 0 は欄の下でなく、画面の上に赤の知らせ (R02) で止まる", strict=True)
def test_e08_zero_qty_is_red(page_a, company_a):
    """E-08 の 2 つ目の期待 (余力): 数量 0 は欄の横に赤の文で保存されず、入れた値は残る。

    見ない所 (理由): 単価を入れて金額を見る (金額は保存のときに services が計算する)。
    """
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/orders/new/")
    mark = unique_mark("e08z")
    choose(page_a, "customer", seed_name(company_a, "partners", CUSTOMER_CODE))
    page_a.fill("input[name=customer_order_no]", mark)
    page_a.fill("input[name=order_date]", date.today().isoformat())
    choose(page_a, "lines-0-item", PART_NO)
    page_a.fill("input[name=lines-0-qty]", "0")
    page_a.fill("input[name=lines-0-unit_price]", STANDARD_PRICE)
    page_a.get_by_role("button", name="保存", exact=True).click()

    # 止まること (道が変わらない・入れた値が残る・画面の上に赤の知らせ R02) は今の画面で通る。先に見る。
    assert re.search(r"/orders/new/$", page_a.url)
    expect(page_a.locator("input[name=customer_order_no]")).to_have_value(mark)
    expect(page_a.locator(".alert-error")).to_contain_text("R02")
    shot(page_a, "E-08", "a-zero-qty")
    # 06 の期待は欄の横の赤の文。明細の行の中 (数量の欄の下) に赤の文があるか。今は無いので、上の印 (期待どおりに動かない理由つき) を付けてある。
    expect(page_a.locator("#order-lines .text-error").first).to_be_visible()
