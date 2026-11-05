"""E-42 生産管理ボード (注文残の盤) の複数行の一括出荷と、帯の「発注する」(N124。11-04 の回帰の守り)。

手順: A 社で受注を 2 件入れる → 盤を 2 件の注文番号の共通の印で絞る → 2 行を選ぶ → 帯の「出荷する」→ 一括出荷の小窓で数量を入れて「保存」
→ もう一度 2 行を選ぶ → 帯の「発注する」。
期待: 2 行を選ぶと帯が出て「2 行を選んでいます」。一括出荷の小窓に 2 行の明細が出て、保存すると小窓が閉じ、知らせが出て、
2 行の「出荷済み」が増え、帯が隠れる。帯の「発注する」は、選んだ 2 明細が埋まった発注の小窓を開く。

この試験が見る所: 数量 10 の受注 2 件 (得意先の注文番号は `<印>-1`・`<印>-2`) を盤 (`/orders/lines/?q=<印>`) で 2 行に絞り、
`pick_rows` で 2 行を選ぶと `#board-bulk` の隠しが外れて `[data-board-count]` が 2。帯の「出荷する」で小窓の題が「一括出荷」、
明細の表が 2 行・数量の欄 (`qty_<pk>`) が 2 つ。2 つとも 2 にして「保存」すると小窓が閉じ、知らせに「2 件の出荷」、2 行の「出荷済み」が 2、帯が隠れる。
帯の「発注する」で小窓の題が「発注の新規登録」、明細の 1 行目と 2 行目の部品の欄 (`lines-0-item`・`lines-1-item`) に値がある。
見ない所 (理由): 発注の保存 (協力企業へ送る道を通る。E-16 と E-40 が見る)、受注より多い数の知らせ (E-12)、相手に届く出荷の知らせ
(手入力の受注なので送らない)、「この行を出さない」(単体の試験 tests/test_orders_board_bulk.py が見る)。
行の選び方は `pick_rows` の 1 か所 (N137 の行の押下。選ぶ欄は sr-only で、欄の check() は枡に押下を取られる)。
"""

import re

from playwright.sync_api import expect

from .conftest import login, new_order, seed_name, shot, unique_mark

CUSTOMER_CODE = "C001"
PART_NO = "WK-1001"
NUMBER = r"^{}(\.0+)?$"


def board_cell(row, page, label: str):
    """盤の行 (row) の、見出しが label の枡 (E-41 と同じ形)。"""
    headers = [text.strip() for text in page.locator("#rows thead th").all_inner_texts()]
    return row.locator("td").nth(headers.index(label))


def pick_rows(rows, page) -> None:
    """盤の行を全部選ぶ (行の押下。N137。link と ⋮ の外の「数量」の枡を押し、行の選ぶ欄に check が付くのを待つ)。"""
    for index in range(rows.count()):
        row = rows.nth(index)
        board_cell(row, page, "数量").click()
        expect(row.locator("[data-board-pick]")).to_be_checked()


def test_e42_bulk_ship_and_po_from_board(page_a, company_a):
    login(page_a, company_a)
    mark = unique_mark("e42")
    customer = seed_name(company_a, "partners", CUSTOMER_CODE)
    for suffix in ("1", "2"):
        new_order(
            page_a, company_a, customer=customer, part_no=PART_NO, qty=10, unit_price=480, mark=f"{mark}-{suffix}"
        )

    page_a.goto(f"{company_a.base_url}/orders/lines/?q={mark}")
    rows = page_a.locator("#rows tbody tr[data-row-menu]")
    expect(rows).to_have_count(2)
    band = page_a.locator("#board-bulk")
    expect(band).to_be_hidden()

    pick_rows(rows, page_a)
    expect(band).to_be_visible()
    expect(band.locator("[data-board-count]")).to_have_text("2")
    expect(band).to_contain_text("2 行を選んでいます")
    shot(page_a, "E-42", "a-band")

    band.get_by_role("button", name="出荷する").click()
    box = page_a.locator("#seizou-modal-box")
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    expect(box.locator("h3")).to_have_text("一括出荷")
    expect(box.locator("form[hx-post] tbody tr")).to_have_count(2)
    qty_inputs = box.locator("input[name^=qty_]")
    expect(qty_inputs).to_have_count(2)
    for index in range(2):
        qty_inputs.nth(index).fill("2")
    shot(page_a, "E-42", "a-bulk-modal")
    box.locator("form[data-enter-guard] button.btn-primary[type=submit]").first.click()  # ボタンの字は N138 で対象と数が入るので、字でなく小窓の form の主色の submit で押す (169 回目の conftest と同じ形)

    expect(page_a.locator("#seizou-modal[open]")).to_have_count(0)
    expect(page_a.locator(".seizou-toast[role=status]").first).to_contain_text("2 件の出荷")
    rows = page_a.locator("#rows tbody tr[data-row-menu]")
    expect(rows).to_have_count(2)
    for index in range(2):
        expect(board_cell(rows.nth(index), page_a, "出荷済み")).to_have_text(re.compile(NUMBER.format(2)))
    expect(band).to_be_hidden()
    shot(page_a, "E-42", "a-after")

    # 帯の「発注する」: 小窓が開き、選んだ 2 明細が明細の 1 行目と 2 行目に埋まっている所まで (保存しない)。
    pick_rows(rows, page_a)
    expect(band.locator("[data-board-count]")).to_have_text("2")
    band.get_by_role("button", name="発注する").click()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    expect(box.locator("h3")).to_have_text("発注の新規登録")
    for index in range(2):
        expect(box.locator(f"select[name=lines-{index}-item]")).not_to_have_value("")
    shot(page_a, "E-42", "a-po-modal")
    # N132 は書きかけの印のある小窓を Esc で閉じるときに確認を出す (Playwright は受け手の無い確認をキャンセルにする)。確認の無い版では呼ばれない。
    page_a.once("dialog", lambda dialog: dialog.accept())
    page_a.keyboard.press("Escape")
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(0)
