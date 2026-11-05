"""E-26 ダッシュボード (仕様書の 6 番の 3 節)。

手順: ログイン後のメニュー。
期待: ダッシュボードの下に大項目 製造部・生産管理部・出荷と請求・マスター・文書が並ぶ。製造部を開くと指示書と進捗が、
生産管理部を開くと受注と受信した注文が出る。

この試験が見る所: A 社にログインした後のダッシュボード (`/`) の左のメニューに、大項目の見出し「製造部」「生産管理部」
「出荷と請求」「マスター」「文書」がこの順で並び、どの大項目も閉じていること (ダッシュボードはどの大項目にも入らない)。
製造部を押すと入口「指示書」「進捗」が見え、生産管理部を押すと「受注」「受信した注文」が見え、製造部は閉じること
(1 つ開くとほかは閉じる)。
見ない所 (理由): 入口を押した先の画面 (それぞれの番号の試験が見る)、右の数字のカード (数は回すたびに変わる)。
"""

from playwright.sync_api import expect

from .conftest import is_logged_in, login, menu_name, shot

SECTIONS = ("製造部", "生産管理部", "出荷と請求", "マスター", "文書")


def test_e26_menu_after_login(page_a, company_a):
    login(page_a, company_a)
    assert is_logged_in(page_a, company_a)
    assert page_a.url.rstrip("/") == company_a.base_url

    menu = page_a.locator("aside nav")
    summaries = menu.locator("details > summary")
    expect(summaries).to_have_text(list(SECTIONS))
    expect(menu.locator("details[open]")).to_have_count(0)
    for label in ("受注", "指示書", "進捗", "出荷", "受信した注文"):
        expect(menu.get_by_role("link", name=menu_name(label), include_hidden=True)).to_have_count(1)
        expect(menu.get_by_role("link", name=menu_name(label))).to_be_hidden()
    shot(page_a, "E-26", "a-dashboard")

    menu.get_by_text("製造部", exact=True).click()
    expect(menu.get_by_role("link", name=menu_name("指示書"))).to_be_visible()
    expect(menu.get_by_role("link", name=menu_name("進捗"))).to_be_visible()
    shot(page_a, "E-26", "a-dashboard-manufacturing-open")

    menu.get_by_text("生産管理部", exact=True).click()
    expect(menu.get_by_role("link", name=menu_name("受注"))).to_be_visible()
    expect(menu.get_by_role("link", name=menu_name("受信した注文"))).to_be_visible()
    expect(menu.get_by_role("link", name=menu_name("指示書"))).to_be_hidden()
