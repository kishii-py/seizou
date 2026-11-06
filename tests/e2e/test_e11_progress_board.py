"""E-11 進捗 (仕様書の 6 番の 3 節)。

手順: 実績を入れる → 別のタブで一覧を開いたまま実績を入れる → 予定を超える実績。
期待: 一覧が操作なしで更新される。超えると設定どおり (既定は黄の知らせと「このまま保存」。押すと実数が記録される)。

この試験が見る所: A 社で数量 10 の受注を入れ、左のメニューの「指示書」→「新規登録」で受注の明細 (字を打って探す欄に受注番号を打って選ぶ)・数量 10・
工程 (複数選びの `<select>`) に種の `TURN` を選んで「保存」する。発行の後に進捗の盤 (`/progress/`) を開くと、その指示書の行が 1 行あり、
予定 10・実績の合計 0・残 10 (盤は新しい指示書の行を自動で足さないので、発行の後に開く。N22 の要望)。盤の `window` に印の値を置き、
同じ文脈の 2 枚目のページで実績の新規登録 (`/progress/new/`) に、指示書番号を打って選んだ指示書の工程 (選んだ値が盤の行の id の数 = 工程の予定の pk と同じ) と実績の数 3 を入れて
「保存」する。盤を読み直さずに待つと、15 秒以内に同じ行の「実績の合計」が 3、「残」が 7 になる (盤は 10 秒ごとに取りに行く。N22)。
印の値が残っている (ページ全体を読み直していない)。
見ない所 (理由): 予定を超える実績の黄の知らせと「このまま保存」・盤の残の赤 (余力の試験。今夜の範囲の外)、盤の工程の pill の絞り込み。
"""

import re
from urllib.parse import urlparse

from playwright.sync_api import expect

from .conftest import choose, login, menu_link, new_order, seed_name, shot, unique_mark

CUSTOMER_CODE = "C001"
PROCESS_CODE = "TURN"
MARK_KEY = "__e2e_board_mark"
MARK_VALUE = "kept"


def _number(text: str) -> re.Pattern:
    return re.compile(rf"^{text}(\.0+)?$")


def test_e11_board_updates_without_reload(context, page_a, company_a):
    login(page_a, company_a)
    customer = seed_name(company_a, "partners", CUSTOMER_CODE)
    order_no = new_order(
        page_a, company_a, customer=customer, part_no="WK-1001", qty=10, unit_price=480, mark=unique_mark("e11")
    )

    menu_link(page_a, "指示書").click()
    page_a.wait_for_url(re.compile(r"/workorders/$"))
    page_a.get_by_role("link", name="新規登録").first.click()
    page_a.wait_for_url(re.compile(r"/workorders/new/$"))
    choose(page_a, "order_line", order_no)
    page_a.fill("input[name=qty]", "10")
    # 工程は 1 行 1 工程の checkbox (205 回目から。字は「<工程コード> <名前>」)
    page_a.get_by_role("checkbox", name=re.compile(rf"^{re.escape(PROCESS_CODE)}\b")).check()
    page_a.locator("form[data-enter-guard] button.btn-primary[type=submit]").first.click()  # 主のボタンの字は画面ごとの動詞 (205 回目から) なので、字でなく主色の submit で押す
    page_a.wait_for_url(re.compile(r"/workorders/\d+/$"))
    workorder_no = page_a.locator("h1").inner_text().strip()

    page_a.goto(f"{company_a.base_url}/progress/")
    page_a.evaluate("([key, value]) => { window[key] = value; }", [MARK_KEY, MARK_VALUE])
    row = page_a.locator("tr[id^=progress-step-]").filter(has_text=re.compile(rf"{re.escape(workorder_no)}(?!\d)"))
    expect(row).to_have_count(1)
    cells = row.locator("td")
    expect(cells.nth(3)).to_have_text(_number("10"))
    expect(cells.nth(4)).to_have_text(_number("0"))
    expect(cells.nth(5)).to_have_text(_number("10"))
    step_pk = re.fullmatch(r"progress-step-(\d+)", row.get_attribute("id")).group(1)
    shot(page_a, "E-11", "a-before")

    other = context.new_page()
    other.goto(f"{company_a.base_url}/progress/new/")
    choose(other, "workorder_step", workorder_no)
    expect(other.locator("select[name=workorder_step]")).to_have_value(step_pk)
    other.fill("input[name=qty]", "3")
    other.locator("form[data-enter-guard] button.btn-primary[type=submit]").first.click()  # 主のボタンの字は画面ごとの動詞 (205 回目から) なので、字でなく主色の submit で押す
    other.wait_for_url(lambda url: urlparse(url).path != "/progress/new/")

    expect(cells.nth(4)).to_have_text(_number("3"), timeout=15_000)
    expect(cells.nth(5)).to_have_text(_number("7"), timeout=15_000)
    assert page_a.evaluate("key => window[key]", MARK_KEY) == MARK_VALUE
    shot(page_a, "E-11", "a-after")
