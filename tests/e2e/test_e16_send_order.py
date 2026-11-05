"""E-16 注文を送る (仕様書の 6 番の 3 節)。

手順: 送る → 送った物の一覧 → 相手を止めた状態で送る。
期待: 送った物が残り、相手の返事 (受け取った) が出る。相手が止まっていれば「届かない」の表示と送り直すボタン。

この試験が見る所: 2 社にログインし、2 社に同じ鍵が渡っていれば、A 社の左のメニューの「発注」→「新規登録」で、取引先 S001・
`WK-1001`・数量 5 の発注を保存する。発注の詳細の状態が「下書き」でなく、「送った記録」のタブに 1 行あって相手の返事
「受け取った」が出る (「断られた」「返事が読めません」「届きません」でない)。
見ない所 (理由): 相手を止めた状態で送る (相手を止める手順は本体。E2E の fixture は止めない)、発注の状態の字
(02 の「相手が受けた」でなく、今の送る口は「送った」にする。違いは要望に書く)。
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import has_partner_secret, login, menu_link, send_purchase_order, shot, unique_mark

NO_SECRET_REASON = "2 社に同じ鍵が渡っていない (SEIZOU_E2E_PARTNER_SECRET)"


def test_e16_send_and_reply(page_a, page_b, company_a, company_b):
    login(page_a, company_a)
    login(page_b, company_b)
    if not (has_partner_secret(page_a, company_a) and has_partner_secret(page_b, company_b)):
        pytest.skip(NO_SECRET_REASON)

    page_a.goto(company_a.base_url)
    menu_link(page_a, "発注").click()
    page_a.wait_for_url(re.compile(r"/purchasing/orders/$"))
    page_a.get_by_role("link", name="新規登録").first.click()
    page_a.wait_for_url(re.compile(r"/purchasing/orders/new/$"))

    po_no = send_purchase_order(
        page_a, company_a, partner_code="S001", part_no="WK-1001", qty=5, mark=unique_mark("e16")
    )
    assert po_no

    status = page_a.locator("h1 ~ .badge").first
    expect(status).not_to_have_text("下書き")

    page_a.locator("input[aria-label='送った記録']").check()
    records = page_a.locator(".tab-content").nth(0)
    expect(records.locator("li")).to_have_count(1)
    expect(records).to_contain_text("受け取った")
    for failed in ("断られた", "返事が読めません", "届きません", "鍵の置き場"):
        expect(records).not_to_contain_text(failed)
    shot(page_a, "E-16", "a-sent")
