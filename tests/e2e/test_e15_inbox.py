"""E-15 受信箱 (仕様書の 6 番の 3 節)。

手順: A 社で注文を送る → B 社の受信箱を開いたまま待つ → 同じ注文をもう 1 回送る → 受け入れる → 受け入れのボタンを 2 回押す → 断る。
期待: 数秒後に操作なしで行が現れ、新しい行だけ色が付く。2 回送っても 1 行。受け入れると B 社の受注の一覧に載り、品番は B 社の品番に置き換わる。
受け入れる前は受注の一覧に無い。2 回押しても受注は 1 件。断ると状態が「断った」。

この試験が見る所: 2 社に同じ鍵が渡っていれば、B 社の受信箱を開いたまま、A 社で `WK-1001` の発注を送る。B 社の画面を読み直さずに
15 秒以内に新しい行が現れ、その行の欄に新しい行の色 (`bg-primary/10`) が付く。受け入れる前に B 社の受注の一覧を A 社の発注番号で
絞ると 0 行。受信箱の詳細の「受け入れる」を押すと、部品の欄に B 社の品番 (`SB-2001`) が初めから選ばれていて、受け入れの画面の「受け入れる」で B 社の受注の
詳細へ移り、得意先の注文番号が A 社の発注番号になる。受け入れた後に同じ絞り込みで 1 行。
見ない所 (理由): 同じ注文をもう 1 回送る・受け入れを 2 回押す・断る (今夜の範囲の外。冪等と二重押しは接続の試験が見る)。
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import has_partner_secret, login, send_purchase_order, shot, unique_mark

NO_SECRET_REASON = "2 社に同じ鍵が渡っていない (SEIZOU_E2E_PARTNER_SECRET)"
ARRIVE_WAIT_MS = 15_000
PARTNER_PART_NO = "WK-1001"
OWN_PART_NO = "SB-2001"


def test_e15_arrives_and_accept_makes_order(page_a, page_b, company_a, company_b):
    login(page_a, company_a)
    login(page_b, company_b)
    if not (has_partner_secret(page_a, company_a) and has_partner_secret(page_b, company_b)):
        pytest.skip(NO_SECRET_REASON)

    # B 社の受信箱を開いたままにする。今ある行の id を控え、新しい行だけを見分ける。
    page_b.goto(f"{company_b.base_url}/connect/inbox/")
    expect(page_b.locator("#inbox-poll")).to_have_count(1)
    known = page_b.locator("#rows tbody tr").evaluate_all("rows => rows.map(row => row.id)")

    po_no = send_purchase_order(
        page_a, company_a, partner_code="S001", part_no=PARTNER_PART_NO, qty=3, mark=unique_mark("e15")
    )

    # 読み直さずに待つ (2 秒ごとの更新で新しい行が先頭に足される)。
    page_b.wait_for_function(
        "known => [...document.querySelectorAll('#rows tbody tr')].some(row => !known.includes(row.id))",
        arg=known,
        timeout=ARRIVE_WAIT_MS,
    )
    new_ids = page_b.locator("#rows tbody tr").evaluate_all(
        "(rows, known) => rows.map(row => row.id).filter(id => !known.includes(id))", known
    )
    assert new_ids, "新しい行が見つからない"
    new_row = page_b.locator(f"#rows tbody tr[id='{new_ids[0]}']")
    expect(new_row.locator("td").first).to_have_class(re.compile(r"bg-primary/10"))
    shot(page_b, "E-15", "b-new-row")

    # 受け入れる前は、B 社の受注の一覧に無い。
    page_b.goto(f"{company_b.base_url}/orders/")
    page_b.fill("#list-q", po_no)
    expect(page_b.locator("#rows tbody tr")).to_have_count(0)

    inbox_pk = new_ids[0].removeprefix("row-")
    page_b.goto(f"{company_b.base_url}/connect/inbox/{inbox_pk}/")
    page_b.get_by_role("link", name="受け入れる", exact=True).click()
    page_b.wait_for_url(re.compile(rf"/connect/inbox/{inbox_pk}/accept/$"))
    expect(page_b.locator("select[name=item_1] option:checked")).to_contain_text(OWN_PART_NO)
    page_b.locator("form[data-enter-guard] button.btn-primary[type=submit]").first.click()  # 主のボタンの字は画面ごとの動詞 (205 回目から) なので、字でなく主色の submit で押す
    page_b.wait_for_url(re.compile(r"/orders/\d+/$"))
    expect(page_b.locator("main")).to_contain_text(po_no)
    expect(page_b.locator("main")).to_contain_text(OWN_PART_NO)

    page_b.goto(f"{company_b.base_url}/orders/")
    page_b.fill("#list-q", po_no)
    rows = page_b.locator("#rows tbody tr")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text(po_no)
    shot(page_b, "E-15", "b-order")
