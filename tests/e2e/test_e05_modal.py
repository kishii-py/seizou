"""E-05 小窓 (仕様書の 6 番の 3 節)。

手順: 受注の入力と出荷の入力で品目の名前を押す → 受注の詳細と指示書の詳細でも押す → 「詳細を開く」→ 戻る → 小窓を開いて Esc。
期待: どの画面でも同じ小窓が開き、詳細へ移れ、戻ると元の画面。Esc で閉じ、入力の途中の値は残る。

この試験が見る所: 部品マスターの一覧を `WK-1001` で 1 行に絞り (絵に小窓を映すため)、品名の字 (小窓の link) を押すと `#seizou-modal` に `open` が付いて品番が出る。
Esc で `open` が外れる。もう一度開いて「詳細を見る」を押すと部品の詳細 (道が `/masters/items/<pk>/`) に
「取引先ごとの品番」のタブがあり、ブラウザの戻るで一覧の道に戻る。
見ない所 (理由): 受注・出荷の入力と、受注・指示書の詳細の中の小窓 (今夜は部品マスターの一覧だけ)、入力の途中の値
(部品マスターの一覧に入力が無い)。
"""

import re

from playwright.sync_api import expect

from .conftest import login, shot

PART_NO = "WK-1001"


def test_e05_modal_then_detail(page_a, company_a):
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/masters/items/")
    # 画面全体の絵は、ページが窓より長いと小窓 (最前面の層) が映らない。品番で絞って 1 画面に収めてから開く。
    page_a.fill("#list-q", PART_NO)
    expect(page_a.locator("#rows tbody tr")).to_have_count(1)
    first_row = page_a.locator("#rows tbody tr").first
    part_no = first_row.locator("td").first.inner_text().strip()
    link = first_row.locator("a.master-link").first
    modal = page_a.locator("#seizou-modal")

    link.click()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    expect(modal).to_contain_text(part_no)
    # 開く動きが終わる (不透明になる) のを待ってから絵を撮る。固定の待ちは使わない。
    expect(modal.locator(".modal-box")).to_have_css("opacity", "1")
    shot(page_a, "E-05", "a-modal")

    page_a.keyboard.press("Escape")
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(0)

    link.click()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    modal.get_by_role("link", name="詳細を見る").click()
    page_a.wait_for_url(re.compile(r"/masters/items/\d+/$"))
    expect(page_a.locator("input[aria-label='取引先ごとの品番']")).to_have_count(1)
    shot(page_a, "E-05", "a-detail")

    page_a.go_back()
    # 絞り込みの字は道の ?q= に残る (戻ると絞り込んだ一覧)。道の本体が一覧であることを見る。
    page_a.wait_for_url(re.compile(r"/masters/items/(\?.*)?$"))
