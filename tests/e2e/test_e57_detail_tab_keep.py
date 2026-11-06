"""E-57 受注の詳細で、小窓で保存した後の取り直しでも選んでいたタブを保つ (初見テスト R5 の r5-a-05 の 5 節。候補 C26 の 3)。

手順: A 社で数量 10 の受注を入れる (指示書・出荷・請求書が全部 0 なので、サーバーは「指示書」で開く) → 「請求書」のタブを選ぶ →
明細の ⋮ の「出荷する」の小窓で 3 を出荷して保存する。
期待: 小窓が閉じて #order-live が取り直され、「出荷」のタブの件数が 1 になっても、「請求書」のタブが選ばれたまま。
取り直しの後にサーバーが選ぶタブは「出荷」(件数のある最初のタブ。候補 C26 の 2) なので、選んだタブを保つ js
(core の js/seizou-detail-tabs.js) が無ければ「出荷」に移る。

この試験が見る所: `input[aria-label='請求書']` と `input[aria-label='出荷']` の checked・「出荷」のタブの件数の字
(`label:has(input[aria-label='出荷']) .badge`)・`#seizou-modal[open]` の数・右上の済んだ知らせ。
見ない所 (理由): 指示書の小窓で指示書を作ってから「出荷」を選ぶ形 (候補の 1 つ目の例。指示書の小窓は工程の欄の入れ方が長いので、
候補が許す 2 つ目の例にした。夜の記録の「迷った事」)、指示書の無い受注で「出荷」を選んで出荷する形 (取り直しの後にサーバーも
「出荷」を選ぶので、js が無くても通ってしまう)、B 社 (詳細の土台と js は 2 社で同じ)、ほかの詳細の取り直し。
受注を 1 つと出荷を 1 件増やす (A 社の DB に残る)。
"""

from playwright.sync_api import expect

from .conftest import login, new_order, seed_name, shot, unique_mark

CUSTOMER_CODE = "C001"
PART_NO = "WK-1001"
SUBMIT = "form[data-enter-guard] button.btn-primary[type=submit]"
# 送った後の scroll の event は次の描画で届く (行のメニューは画面の scroll で閉じる)。押す前に 2 つ先の描画まで待つ。
NEXT_FRAMES = "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"


def test_e57_tab_kept_after_modal_save(page_a, company_a):
    login(page_a, company_a)
    new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", CUSTOMER_CODE),
        part_no=PART_NO,
        qty=10,
        unit_price=480,
        mark=unique_mark("e57"),
    )
    invoices = page_a.locator("input[name='detail-tabs'][aria-label='請求書']")
    shipments = page_a.locator("input[name='detail-tabs'][aria-label='出荷']")
    shipment_count = page_a.locator("label:has(input[name='detail-tabs'][aria-label='出荷']) .badge")
    expect(page_a.locator("input[name='detail-tabs'][aria-label='指示書']")).to_be_checked()
    expect(shipment_count).to_have_text("0")

    invoices.check()
    expect(invoices).to_be_checked()

    row = page_a.locator("tr[data-row-menu]").first
    row.scroll_into_view_if_needed()
    page_a.evaluate(NEXT_FRAMES)
    row.locator('summary[aria-label="行の操作"]').click()
    row.get_by_role("link", name="出荷する").click()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    box = page_a.locator("#seizou-modal-box")
    box.locator("input[name=qty]").fill("3")
    box.locator(SUBMIT).first.click()

    expect(page_a.locator(".seizou-toast[role=status]")).to_be_visible()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(0)
    expect(shipment_count).to_have_text("1")
    expect(invoices).to_have_count(1)
    expect(invoices).to_be_checked()
    expect(shipments).not_to_be_checked()
    shot(page_a, "E-57", "a-tab-kept")
