"""E-59 一覧の期間の絞りの近道「今日・今週・今月・先月」(依頼書 N348。397 回目。受け渡しの seizou-部品の6案-20261107/指示書.md の 1)。

手順: A 社の受注の一覧 (/orders/) を開く → 受注日の近道の「今月」を押す → 「先月」を押す。
期待: 近道は受注日の欄の下に 今日・今週・今月・先月 の順に 4 つ並び、絞りの無い一覧ではどれも選ばれていない。
「今月」を押すと、受注日の始めと終わりの欄にボタンの始めと終わりの日付が入り、画面を移らずに表が取り直され、URL に
`f_ordered_from` と `f_ordered_to` が付き、「今月」だけが選ばれた印になり、「絞りを外す」が出る。「先月」を押すと 2 つの欄の値が
「先月」の日付に変わり、「今月」の印が外れる。

この試験が見る所: `#list-f-ordered-presets button[data-preset-start]` の数と字と `aria-pressed`・`#list-f-ordered` (始め) と
`[data-date-range="ordered"] input[type=date]` の 2 つ目 (終わり) の value・URL の `f_ordered_from=`・`f_ordered_to=`・
`#list-filter-clear` の「絞りを外す」・画面に置いた印 (`window.__e59`) が残る (開き直していない) こと。
見ない所 (理由): 表の行が今月の受注だけになること (絞りの中身は単体の試験 tests/test_core_list_filters.py が見る)、
ボタンの日付の値そのもの (会社の時刻で決める計算は単体の試験 tests/test_core_list_date_presets.py が見る。ここではボタンの
data 属性の値と欄と URL が同じことを見る)、
「今日」と「今週」(同じ JS と同じ描き直し)、納期の近道とほかの一覧 (同じ土台の部品)、B 社 (同じ土台)。
A 社の DB に何も足さない。
"""

import re

from playwright.sync_api import expect

from .conftest import login, shot

MARK = "window.__e59 = 1"
LABELS = ["今日", "今週", "今月", "先月"]


def test_e59_month_preset_fills_dates_and_filters(page_a, company_a):
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/orders/")
    presets = page_a.locator("#list-f-ordered-presets button[data-preset-start]")
    start_input = page_a.locator("#list-f-ordered")
    end_input = page_a.locator('[data-date-range="ordered"] input[type=date]').nth(1)
    expect(presets).to_have_count(4)
    expect(presets).to_have_text(LABELS)
    for index in range(4):
        expect(presets.nth(index)).to_have_attribute("aria-pressed", "false")
    page_a.evaluate(MARK)

    month = presets.filter(has_text="今月")
    start = month.get_attribute("data-preset-start")
    end = month.get_attribute("data-preset-end")
    month.click()
    expect(start_input).to_have_value(start)
    expect(end_input).to_have_value(end)
    # 期間の欄は打ち終わりを 800ms 待ってから取り直す (core/list.html の hx-trigger) ので、URL と印は expect の待ちに任せる。
    expect(page_a).to_have_url(re.compile(rf"[?&]f_ordered_from={re.escape(start)}(&|$)"))
    expect(page_a).to_have_url(re.compile(rf"[?&]f_ordered_to={re.escape(end)}(&|$)"))
    expect(presets.filter(has_text="今月")).to_have_attribute("aria-pressed", "true")
    for label in ("今日", "今週", "先月"):
        expect(presets.filter(has_text=label)).to_have_attribute("aria-pressed", "false")
    expect(page_a.locator("#list-filter-clear").get_by_role("link", name="絞りを外す")).to_be_visible()
    assert page_a.evaluate("window.__e59") == 1, "一覧を開き直した (近道は欄に値を入れて表だけを取り直す)"
    shot(page_a, "E-59", "a-orders-month-preset")

    last_month = presets.filter(has_text="先月")
    last_start = last_month.get_attribute("data-preset-start")
    last_end = last_month.get_attribute("data-preset-end")
    assert (last_start, last_end) != (start, end)
    last_month.click()
    expect(start_input).to_have_value(last_start)
    expect(end_input).to_have_value(last_end)
    expect(page_a).to_have_url(re.compile(rf"[?&]f_ordered_from={re.escape(last_start)}(&|$)"))
    expect(presets.filter(has_text="先月")).to_have_attribute("aria-pressed", "true")
    expect(presets.filter(has_text="今月")).to_have_attribute("aria-pressed", "false")
    assert page_a.evaluate("window.__e59") == 1, "一覧を開き直した"
