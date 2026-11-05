"""E-41 生産管理ボード (注文残の盤) の行の小窓の出荷 (N124。11-04 の回帰の守り)。

手順: A 社で受注を 1 件入れる → 注文残の盤をその受注の注文番号で絞る → 行の ⋮ の「出荷する」→ 小窓で数量を入れて「保存」。
期待: 小窓に出荷の入力が開き、保存すると小窓が閉じ、右上に知らせが出て、画面を移らずにその行の「出荷済み」が増える。

この試験が見る所: 数量 10 の受注 (得意先の注文番号に印) を入れ、盤 (`/orders/lines/?q=<印>`) で 1 行に絞る。⋮ の「出荷する」で
`#seizou-modal` に `open` が付き、`#seizou-modal-box` の中に `form[hx-post]` と数量の欄がある。数量 3 で「保存」すると `open` が外れ、
`.seizou-toast[role=status]` が出て、行の差し替えの後の「出荷済み」が 3・「残」が 7 になる。画面の印 (`window.__e41`) が残る (開き直していない)。
見ない所 (理由): 受注より多い数の赤と黄の知らせ (E-12 が全体の画面で見る)、相手へ送る出荷の知らせ (手入力の受注なので送らない)、
指示書と発注と実績の小窓 (同じ土台。E-42 が発注の帯を見る)。
"""

import re

from playwright.sync_api import expect

from .conftest import login, new_order, seed_name, shot, unique_mark

CUSTOMER_CODE = "C001"
PART_NO = "WK-1001"
# 盤の表は幅 1280 の画面より広く、⋮ は表の箱を横に送った先にある。送った後の scroll の event は次の描画で届き、行のメニューは
# 表の箱の scroll で閉じる。押す前に ⋮ そのものを見える所へ送り、2 つ先の描画まで待つ (E-05 の形)。
NEXT_FRAMES = "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
NUMBER = r"^{}(\.0+)?$"


def board_cell(row, page, label: str):
    """盤の行 (row) の、見出しが label の枡。見出しの字は前後の空白を落として比べる (受注番号の見出しには選ぶ欄がある)。"""
    headers = [text.strip() for text in page.locator("#rows thead th").all_inner_texts()]
    return row.locator("td").nth(headers.index(label))


def test_e41_ship_from_board_row_modal(page_a, company_a):
    login(page_a, company_a)
    mark = unique_mark("e41")
    new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", CUSTOMER_CODE),
        part_no=PART_NO,
        qty=10,
        unit_price=480,
        mark=mark,
    )

    page_a.goto(f"{company_a.base_url}/orders/lines/?q={mark}")
    rows = page_a.locator("#rows tbody tr[data-row-menu]")
    expect(rows).to_have_count(1)
    row = rows.first
    expect(board_cell(row, page_a, "出荷済み")).to_have_text(re.compile(NUMBER.format(0)))
    page_a.evaluate("window.__e41 = 1")

    menu = row.locator('summary[aria-label="行の操作"]')
    menu.scroll_into_view_if_needed()
    page_a.evaluate(NEXT_FRAMES)
    menu.click()
    row.get_by_role("link", name="出荷する").click()

    modal = page_a.locator("#seizou-modal")
    box = page_a.locator("#seizou-modal-box")
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    expect(box.locator("form[hx-post]")).to_have_count(1)
    expect(box.locator("input[name=qty]")).to_be_visible()
    expect(modal.locator(".modal-box")).to_have_css("opacity", "1")
    shot(page_a, "E-41", "a-modal")

    box.locator("input[name=qty]").fill("3")
    box.locator("form[data-enter-guard] button.btn-primary[type=submit]").first.click()  # ボタンの字は N138 で対象と数が入るので、字でなく小窓の form の主色の submit で押す (169 回目の conftest と同じ形)

    expect(page_a.locator("#seizou-modal[open]")).to_have_count(0)
    expect(page_a.locator(".seizou-toast[role=status]").first).to_be_visible()
    row = page_a.locator("#rows tbody tr[data-row-menu]").first
    expect(board_cell(row, page_a, "出荷済み")).to_have_text(re.compile(NUMBER.format(3)))
    expect(board_cell(row, page_a, "残")).to_have_text(re.compile(NUMBER.format(7)))
    assert page_a.evaluate("window.__e41") == 1, "盤を開き直した (小窓の保存は画面を移らない)"
    assert "/orders/lines/" in page_a.url
    shot(page_a, "E-41", "a-after")
