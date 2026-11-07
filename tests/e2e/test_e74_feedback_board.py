"""E-74 フィードバックの「みんなの Issue」のタブのカンバン (5 つの列) と、一覧の形への切り替えと、4 つの幅の見え方 (依頼書 N325。決め 11-07 09:3x〜10:1x)。

手順: A 社で /feedback/?tab=issues を開く → 右上の「一覧」を押す → 「カンバン」を押して戻す → 4 つの幅 (390・820・1280・1600) で撮る。
期待: カンバン (#issue-board) か、GitHub を読めなかったときの 1 文が出る。「一覧」で URL に view=list が付き、カンバンが無くなる。
「カンバン」で URL が ?tab=issues に戻る。どの幅でも、画面の全体は横にはみ出さない (5 つの列はカンバンの中で横に流れる)。

この試験が見る所: `#issue-board` と `#issue-table` の数・URL・`#issue-views` の選び (`btn-active`)・
`document.documentElement.scrollWidth` と窓の幅。
見ない所 (理由): 👍 を押す形 (本物の GitHub に 👍 が付くので押さない。押した後に「うちも済み」になることは
tests/test_feedback_views.py が requests を差し替えて見る)、列の分け方とカードの字 (本物の Issue の数と状態で変わる。
tests/test_feedback_github.py と tests/test_feedback_views.py が作り物で見る)、B 社 (盤は 2 社で同じ)。
GitHub は本物を読む (読めないときは 1 文が出る形を見る)。A 社の DB に何も足さない。
"""

import re

from playwright.sync_api import expect

from .conftest import VIEWPORT, login, shot

#: 撮る幅 (名前と px)
WIDTHS = {"phone": 390, "tablet": 820, "laptop": 1280, "wide": 1600}
HEIGHT = 900
#: GitHub を読めなかったときの 1 文の頭 (services.board_issues)
UNREADABLE = "GitHub の Issue を読めませんでした"


def _board_or_unreadable(page) -> bool:
    """盤か読めなかった 1 文のどちらかが見えるのを待つ。盤なら True。"""
    expect(page.locator("#issue-board").or_(page.get_by_text(UNREADABLE))).to_be_visible()
    return page.locator("#issue-board").count() == 1


def test_e74_feedback_board(page_a, company_a):
    login(page_a, company_a)
    page_a.set_viewport_size(VIEWPORT)
    page_a.goto(f"{company_a.base_url}/feedback/?tab=issues")
    readable = _board_or_unreadable(page_a)
    views = page_a.locator("#issue-views")
    expect(views.locator("a")).to_have_count(2)
    expect(views.locator("a.btn-active")).to_have_text("カンバン")

    views.get_by_role("link", name="一覧", exact=True).click()
    expect(page_a).to_have_url(re.compile(r"/feedback/\?tab=issues&view=list$"))
    expect(page_a.locator("#issue-board")).to_have_count(0)
    expect(views.locator("a.btn-active")).to_have_text("一覧")
    expect(page_a.locator("#issue-table")).to_have_count(1 if readable else 0)
    shot(page_a, "E-74", "a-feedback-board-list")

    views.get_by_role("link", name="カンバン", exact=True).click()
    expect(page_a).to_have_url(re.compile(r"/feedback/\?tab=issues$"))
    for name, width in WIDTHS.items():
        page_a.set_viewport_size({"width": width, "height": HEIGHT})
        _board_or_unreadable(page_a)
        scroll = page_a.evaluate("() => document.documentElement.scrollWidth")
        assert scroll <= width, f"幅 {width} で画面が横にはみ出した (scrollWidth {scroll})"
        shot(page_a, "E-74", f"a-feedback-board-{name}")
    page_a.set_viewport_size(VIEWPORT)
