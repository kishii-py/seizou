"""E-26 ダッシュボード (仕様書の 6 番の 3 節)。

手順: ログイン後のメニュー。
期待: 受注・指示書・進捗・出荷・受信箱・マスターが並ぶ。

この試験が見る所: A 社にログインした後のダッシュボード (`/`) の左のメニューに、入口「受注」「指示書」「進捗」
「出荷」「受信箱」と、区分の見出し「マスター」があること。
見ない所 (理由): 入口を押した先の画面 (それぞれの番号の試験が見る)、右の数字のカード (数は回すたびに変わる)。
"""

from playwright.sync_api import expect

from .conftest import is_logged_in, login, shot


def test_e26_menu_after_login(page_a, company_a):
    login(page_a, company_a)
    assert is_logged_in(page_a, company_a)
    assert page_a.url.rstrip("/") == company_a.base_url

    menu = page_a.locator("aside nav")
    for label in ("受注", "指示書", "進捗", "出荷", "受信箱"):
        expect(menu.get_by_role("link", name=label, exact=True)).to_have_count(1)
    expect(menu.get_by_text("マスター", exact=True)).to_have_count(1)

    shot(page_a, "E-26", "a-dashboard")
