"""E-63 上の帯の ☆ で登録した画面が、左のメニューの「ブックマーク」の組に並ぶ (依頼書 N348。400 回目。受け渡しの seizou-部品の6案-20261107/指示書.md の 5)。

手順: A 社の受注の一覧 (/orders/) を開く → ☆ を押して「このページを登録」を押す → 左のメニューの「ブックマーク」を開く →
最初の画面 (/) へ移り、「ブックマーク」の「受注」を押す → ☆ を押して「外す」を押す。
期待: 登録すると左のメニューに「ブックマーク」の組が出て、開くと「受注」の入口があり、今の画面の印 (aria-current="page") が付き、
先は /orders/。/ では同じ入口に今の画面の印が無く、押すと /orders/ へ移る。外すと入口の数が登録の前に戻る。

この試験が見る所: `summary[aria-label="ブックマーク"]` (上の帯の ☆)・「このページを登録」と「外す」のボタン・`#menu-bookmarks` の数・
`#menu-bookmarks a` の数と字と `href` と `aria-current`・URL。
最初から「受注」が登録済みなら、先に「外す」で外してから登録し直す (このとき終わりの数は最初より 1 少ない)。
見ない所 (理由): 並びの順と、見つからなくなった画面の「消す」(単体の試験 tests/test_bookmarks_services.py と
tests/test_bookmarks_views.py が見る)、部署で開けない画面を並べないこと (単体の試験 tests/test_bookmarks_sidebar.py)、
一覧でない画面で登録できないこと (単体の試験)、B 社 (同じ土台)。
DB に残る物: 無し (登録して外すので、A 社の登録の表は元に戻る。最初から登録済みだった場合だけ、その登録が消えたまま残る)。
"""

import re

from playwright.sync_api import expect

from .conftest import login, shot

ORDERS_PATH = "/orders/"
ORDERS_LABEL = "受注"


def star_box(page):
    """上の帯の ☆ の details (summary の aria-label が「ブックマーク」。左のメニューの組の summary には aria-label が無い)。"""
    return page.locator("details:has(> summary[aria-label='ブックマーク'])")


def open_star(page) -> None:
    box = star_box(page)
    if not box.evaluate("details => details.open"):
        box.locator("> summary").click()
    expect(box.locator(".dropdown-content")).to_be_visible()


def open_sidebar_group(page) -> None:
    group = page.locator("#menu-bookmarks")
    expect(group).to_have_count(1)
    if not group.evaluate("details => details.open"):
        group.locator("> summary").click()
    expect(group.locator("ul")).to_be_visible()


def press_and_back(page, button_name: str) -> None:
    """☆ の小窓のボタンを押し、送った後に受注の一覧へ戻ったことを待つ。"""
    with page.expect_navigation():
        star_box(page).get_by_role("button", name=button_name, exact=True).click()
    expect(page).to_have_url(re.compile(rf"{re.escape(ORDERS_PATH)}$"))


def test_e63_registered_screen_appears_in_sidebar(page_a, company_a):
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}{ORDERS_PATH}")
    links = page_a.locator("#menu-bookmarks a")
    before = links.count()

    open_star(page_a)
    was_registered = star_box(page_a).get_by_role("button", name="外す", exact=True).count() > 0
    if was_registered:
        press_and_back(page_a, "外す")
        open_star(page_a)
    press_and_back(page_a, "このページを登録")

    open_sidebar_group(page_a)
    current = page_a.locator('#menu-bookmarks a[aria-current="page"]')
    expect(current).to_have_count(1)
    expect(current).to_contain_text(ORDERS_LABEL)
    expect(current).to_have_attribute("href", ORDERS_PATH)
    # 組の見出しの矢印と字の色は transition で変わるので、変わり終わってから撮る (絵だけのための待ち)。
    page_a.wait_for_timeout(400)
    shot(page_a, "E-63", "a-sidebar-bookmarks")

    page_a.goto(f"{company_a.base_url}/")
    open_sidebar_group(page_a)
    entry = page_a.locator("#menu-bookmarks").get_by_role("link", name=ORDERS_LABEL, exact=True)
    expect(entry).to_have_count(1)
    expect(entry).not_to_have_attribute("aria-current", "page")
    entry.click()
    expect(page_a).to_have_url(f"{company_a.base_url}{ORDERS_PATH}")

    open_star(page_a)
    press_and_back(page_a, "外す")
    expected = before - 1 if was_registered else before
    expect(page_a.locator("#menu-bookmarks a")).to_have_count(expected)
