"""E-43 一覧の絞り込みの枠と印 (N117〜N128。11-04 の回帰の守り)。

手順: A 社の受注の一覧で状態「出荷済」を選ぶ → 「クリア」。注文残の盤で「出荷の残りがある物だけ」を外す。
請求書の一覧で「未入金だけ」を入れる。指示書の一覧で「未着手だけ」を入れる。
期待: 欄を変えると画面を移らずに表と「全 n 件」が差し替わり、URL に絞りの値が付き、「絞りを外す」が出る。「クリア」で絞りの無い一覧に戻る。
盤は既定で残のある明細だけを出し、外すと出荷済の明細も並ぶ。

この試験が見る所: 4 つの一覧で、画面に印 (`window.__e43`) を置いてから枠の欄 (`#list-f-<名前>`) を変え、URL の `f_<名前>=`・
`#list-count` の数・`#list-filter-clear` の「絞りを外す」・印が残る (開き直していない) ことを見る。受注の一覧は「クリア」で URL から `f_status` が消え、
状態の欄が空に戻る。盤は `f_open=0` で `#list-count` が増える (種に出荷済の明細がある)。請求書と指示書は件数が増えない。
見ない所 (理由): 納期と期間の日付の欄・得意先の選択 (同じ土台の欄。単体の試験 tests/test_core_list_filters.py と各部品の *_filters*.py が見る)、並び替えとページ送りと CSV の持ち越し
(単体の試験)、B 社の一覧 (同じ宣言)。
"""

import re

from playwright.sync_api import expect

from .conftest import login, shot

MARK = "window.__e43 = 1"


def count(page) -> int:
    return int(page.locator("#list-count").inner_text().replace(",", "").strip())


def open_list(page, company, path: str) -> int:
    page.goto(f"{company.base_url}{path}")
    expect(page.locator("#list-count")).to_be_visible()
    page.evaluate(MARK)
    return count(page)


def expect_swapped(page, param: str) -> None:
    """欄を変えた後: URL に param が付き (表と件数は同じ返事で差し替わる)、「絞りを外す」が出て、印が残る。"""
    page.wait_for_url(re.compile(re.escape(param)))
    expect(page.locator("#list-filter-clear").get_by_role("link", name="絞りを外す")).to_be_visible()
    assert page.evaluate("window.__e43") == 1, "一覧を開き直した (絞りの欄は表だけを差し替える)"


def test_e43_order_list_status_then_clear(page_a, company_a):
    login(page_a, company_a)
    open_list(page_a, company_a, "/orders/")
    page_a.locator("#list-f-status").select_option(label="出荷済")
    expect_swapped(page_a, "f_status=shipped")
    statuses = page_a.locator("#rows tbody tr").all_inner_texts()
    assert statuses and all("出荷済" in text for text in statuses)
    shot(page_a, "E-43", "a-orders-shipped")

    page_a.locator("form[hx-target='#rows']").get_by_role("link", name="クリア", exact=True).click()
    page_a.wait_for_url(re.compile(r"/orders/$"))
    expect(page_a.locator("#list-f-status")).to_have_value("")
    expect(page_a.locator("#list-filter-clear").get_by_role("link", name="絞りを外す")).to_have_count(0)


def test_e43_board_open_flag_off_shows_more(page_a, company_a):
    login(page_a, company_a)
    before = open_list(page_a, company_a, "/orders/lines/")
    flag = page_a.locator("#list-f-open")
    expect(flag).to_be_checked()
    flag.uncheck()
    page_a.wait_for_url(re.compile(r"f_open=0"))
    expect(page_a.locator("#list-count")).not_to_have_text(str(before))
    assert count(page_a) > before, "残の無い明細が増えない (種に出荷済の明細がある前提)"
    assert page_a.evaluate("window.__e43") == 1, "盤を開き直した"
    shot(page_a, "E-43", "a-board-all")


def test_e43_invoice_unpaid_only(page_a, company_a):
    login(page_a, company_a)
    before = open_list(page_a, company_a, "/billing/invoices/")
    page_a.locator("#list-f-unpaid").check()
    expect_swapped(page_a, "f_unpaid=1")
    assert count(page_a) <= before
    shot(page_a, "E-43", "a-invoices-unpaid")


def test_e43_workorder_unaccepted_only(page_a, company_a):
    login(page_a, company_a)
    before = open_list(page_a, company_a, "/workorders/")
    page_a.locator("#list-f-unaccepted").check()
    expect_swapped(page_a, "f_unaccepted=1")
    assert count(page_a) <= before
    shot(page_a, "E-43", "a-workorders-unaccepted")
