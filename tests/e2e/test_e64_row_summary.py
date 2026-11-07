"""E-64 一覧の行の「要点を見る」(目の印) で、その記録の要点の小窓が開く (依頼書 N348。401 回目。受け渡しの seizou-部品の6案-20261107/指示書.md の 6)。

手順: A 社の受注の一覧 (/orders/) で最初の行の目の印を押す → 小窓の「詳細を見る」を押す。指示書の一覧 (/workorders/) で
最初の行の目の印を押す → Esc で閉じる。
期待: 受注の一覧の右端に「要点」の列があり、目の印を押すと画面を移らずに小窓が開き、題が行の受注番号、「詳細を見る」の先が
その受注の詳細 (目の印の href と同じ)。押すと詳細へ移る。指示書の一覧でも小窓が開き、「工程の並び」と「詳細を見る」が出て、
Esc で閉じる。

この試験が見る所: `#rows thead th` の「要点」・行の `a[data-row-summary]` の `href`・1 列目の link の字・`#seizou-modal[open]` の数・
`#seizou-modal-box h3` の字・`#seizou-modal-box` の「詳細を見る」の `href`・URL・指示書の小窓の「工程の並び」の字。
受注の一覧に行が無ければ、数量 10 の受注を 1 件作ってから見る。指示書の一覧に行が無ければ、その半分は理由を付けて skip する。
見ない所 (理由): 小窓の明細の先頭 5 行と合計の値 (単体の試験 tests/test_core_row_summary.py が作り物で見る)、金額の閲覧が無い人
(合計を出さない。単体の試験)、発注・出荷・請求書の一覧 (依頼書 N346 が同じ印を足す。今は E2E を作らない)、B 社 (同じ土台)。
DB に残る物: 受注の一覧に行が無かった場合だけ、A 社に受注 1 件が残る。
"""

import pytest
from playwright.sync_api import expect

from .conftest import login, new_order, seed_name, shot, unique_mark

CUSTOMER_CODE = "C001"
PART_NO = "WK-1001"
SUMMARY = "a[data-row-summary]"


def test_e64_eye_opens_summary_modal(page_a, company_a):
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/orders/")
    expect(page_a.locator("#rows thead th").filter(has_text="要点")).to_have_count(1)
    rows = page_a.locator("#rows tbody tr")
    if rows.count() == 0:
        new_order(
            page_a,
            company_a,
            customer=seed_name(company_a, "partners", CUSTOMER_CODE),
            part_no=PART_NO,
            qty=10,
            unit_price=480,
            mark=unique_mark("e64"),
        )
        page_a.goto(f"{company_a.base_url}/orders/")
    expect(rows.first).to_be_visible()

    row = rows.first
    eye = row.locator(SUMMARY)
    detail_path = eye.get_attribute("href")
    order_no = row.locator("td").first.locator("a").inner_text().strip()
    assert detail_path and order_no

    eye.click()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    box = page_a.locator("#seizou-modal-box")
    expect(box.locator("h3")).to_have_text(order_no)
    detail_link = box.get_by_role("link", name="詳細を見る")
    expect(detail_link).to_have_attribute("href", detail_path)
    assert page_a.url.endswith("/orders/"), "目の印で画面を移った (小窓を開くだけのはず)"
    shot(page_a, "E-64", "a-orders-summary-modal")
    detail_link.click()
    expect(page_a).to_have_url(f"{company_a.base_url}{detail_path}")

    page_a.goto(f"{company_a.base_url}/workorders/")
    workorder_rows = page_a.locator("#rows tbody tr")
    if workorder_rows.count() == 0:
        pytest.skip("A 社の指示書の一覧に行が無い (受注の半分は通った)")
    workorder_rows.first.locator(SUMMARY).click()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    expect(box).to_contain_text("工程の並び")
    expect(box.get_by_role("link", name="詳細を見る")).to_be_visible()
    page_a.keyboard.press("Escape")
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(0)
