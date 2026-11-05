"""E-40 発注側のライブ更新 (N84)。

手順: A 社で注文を送る → A 社の発注の一覧をその発注番号で絞って開いたままにする → B 社の受信箱で受信した注文を受け入れる。
期待: A 社の一覧のその発注の行が、開き直さずに数秒で「相手が受け入れた」になり、行の欄が一瞬色づく (seizou-changed)。
絞り込みは保たれる。

この試験が見る所: 2 社に同じ鍵が渡っていれば、B 社の受信箱を開いて今ある行の id を控え、A 社で `WK-1001` の発注を送る。
A 社で発注の一覧を `?q=<発注番号>` で開き、画面に印 (`window.__e40 = 1`) と、`#rows` の中に `td.seizou-changed` が現れたら
`window.__e40Flash` を真にする MutationObserver を置く。B 社の受信箱で新しい行を待ち (E-15 と同じ形)、詳細の「受け入れる」→ 受け入れの画面の「受け入れる」。
A 社の画面のその行が 15 秒以内に「相手が受け入れた」になり、印が残る (開き直していない)・色が付いた・URL に `q=` が残り行が 1 行。
見ない所 (理由): 断られた・出荷の知らせ・検収 (相手の画面の手順が長い。単体の試験 tests/test_purchasing_live.py が 4 つとも見る)、
発注の詳細の画面 (単体の試験が見る)、色の濃さ (目で見る)。
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import has_partner_secret, login, send_purchase_order, shot, unique_mark

NO_SECRET_REASON = "2 社に同じ鍵が渡っていない (SEIZOU_E2E_PARTNER_SECRET)"
ARRIVE_WAIT_MS = 15_000
LIVE_WAIT_MS = 15_000
PARTNER_PART_NO = "WK-1001"

# A 社の一覧に置く印と、色づけを見張る MutationObserver (#rows の中身が入れ替わるたびに td.seizou-changed を探す)。
WATCH_SCRIPT = """() => {
  window.__e40 = 1;
  window.__e40Flash = false;
  const rows = document.getElementById('rows');
  new MutationObserver(() => {
    if (rows.querySelector('td.seizou-changed')) { window.__e40Flash = true; }
  }).observe(rows, { childList: true, subtree: true });
}"""


def test_e40_accept_updates_purchase_list_without_reload(page_a, page_b, company_a, company_b):
    login(page_a, company_a)
    login(page_b, company_b)
    if not (has_partner_secret(page_a, company_a) and has_partner_secret(page_b, company_b)):
        pytest.skip(NO_SECRET_REASON)

    # B 社の受信箱で今ある行の id を控え、新しい行だけを見分ける。
    page_b.goto(f"{company_b.base_url}/connect/inbox/")
    expect(page_b.locator("#inbox-poll")).to_have_count(1)
    known = page_b.locator("#rows tbody tr").evaluate_all("rows => rows.map(row => row.id)")

    po_no = send_purchase_order(
        page_a, company_a, partner_code="S001", part_no=PARTNER_PART_NO, qty=3, mark=unique_mark("e40")
    )

    # A 社の発注の一覧をその発注番号で絞って開き、印と見張りを置く。
    page_a.goto(f"{company_a.base_url}/purchasing/orders/?q={po_no}")
    expect(page_a.locator("#purchaseorder-poll")).to_have_count(1)
    rows_a = page_a.locator("#rows tbody tr")
    expect(rows_a).to_have_count(1)
    expect(rows_a.first).to_contain_text(po_no)
    page_a.evaluate(WATCH_SCRIPT)

    # B 社: 受信した注文を待ち、詳細の「受け入れる」→ 受け入れの画面の「受け入れる」。
    page_b.wait_for_function(
        "known => [...document.querySelectorAll('#rows tbody tr')].some(row => !known.includes(row.id))",
        arg=known,
        timeout=ARRIVE_WAIT_MS,
    )
    new_ids = page_b.locator("#rows tbody tr").evaluate_all(
        "(rows, known) => rows.map(row => row.id).filter(id => !known.includes(id))", known
    )
    assert new_ids, "新しい行が見つからない"
    inbox_pk = new_ids[0].removeprefix("row-")
    page_b.goto(f"{company_b.base_url}/connect/inbox/{inbox_pk}/")
    page_b.get_by_role("link", name="受け入れる", exact=True).click()
    page_b.wait_for_url(re.compile(rf"/connect/inbox/{inbox_pk}/accept/$"))
    page_b.locator("form[data-enter-guard] button.btn-primary[type=submit]").first.click()  # 主のボタンの字は画面ごとの動詞 (205 回目から) なので、字でなく主色の submit で押す
    page_b.wait_for_url(re.compile(r"/orders/\d+/$"))

    # A 社: 開き直さずに、その行が「相手が受け入れた」になり、色が付く。
    expect(page_a.locator("#rows tbody tr").first).to_contain_text("相手が受け入れた", timeout=LIVE_WAIT_MS)
    assert page_a.evaluate("window.__e40") == 1, "A 社の一覧を開き直した"
    page_a.wait_for_function("() => window.__e40Flash === true", timeout=LIVE_WAIT_MS)
    assert "q=" in page_a.url
    expect(page_a.locator("#rows tbody tr")).to_have_count(1)
    shot(page_a, "E-40", "a-accepted")
