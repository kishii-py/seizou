"""E-77 つながりの無い取引先への発注を「発注書を送った」で「送った」にし、緑の「受入を記録する」で一部を受け入れると「一部納品」になる
(本人の決め 11-12 14:3x・18:2x・21:0x・21:1x。518〜519 回目)。

手順 (A 社): つながりの無い協力企業を 1 社作る → その取引先への発注 (数量 10) を入れる (相手へ送られず「下書き」のまま) →
詳細の緑の「発注書を送った」を押し、小窓の「発注書を送った」を押す → 緑の「受入を記録する」を押し、数量を 4 にして保存する。
期待: 入れた直後は、黄の 1 行「この取引先とは seizou でつながっていません。…」と緑の「発注書を送った」が出て、「再送」は出ない。
小窓には、押す前の説明の 1 文 (本人の決めの字) が出る。押すと、画面を読み直さずに、状態が「送った」・黄の 1 行が
「発注書を seizou の外で送った発注です (…)。…」・緑が「受入を記録する」に替わる。緑を押すと受入の小窓が数量 10 (受入の残) で開き、
4 にして保存すると、読み直さずに状態が「一部納品」になり、緑の「受入を記録する」は残る。

この試験が見る所: `[data-screen-head]` の状態の字・`#po-alert` の字・`[data-detail-actions] .btn-primary` の数と字・「再送」の form の数・
`#seizou-modal[open]` の数・小窓の字と `input[name=qty]` の値・道。
見ない所 (理由): 今日やることの数と一覧の絞り・取消の黄の確かめ・後からつながりを登録した時の「変更」・権限 (tests/test_purchasing_sent_outside.py が見る)、
B 社 (発注の詳細の土台は 2 社で同じ)。取引先を 1 社・発注を 1 つ・受入を 1 件増やす (A 社の DB に残る)。
"""

import re
from datetime import datetime

from playwright.sync_api import expect

from .conftest import SAVE_WAIT_MS, choose, login, shot, unique_mark

PART_NO = "WK-1002"
SUBMIT = "form[data-enter-guard] button.btn-primary[type=submit]"
PRIMARY = "[data-detail-actions] .btn-primary"
EXPLAIN = "発注書 (印刷用の画面か PDF) を、FAX・郵送・メールなどで取引先に送った後に押します。"
# 発注の詳細は 2 秒ごとに取り直す。小窓を閉じた後の入れ替えを待つ上限
LIVE_WAIT_MS = 10_000


def _new_unlinked_subcontractor(page, company, mark: str) -> str:
    """取引先の新規登録で、つながりを登録しない協力企業を 1 社作り、名前を返す (取引先コードは 10 字以内)。"""
    page.goto(f"{company.base_url}/masters/partners/new/")
    name = f"E2E 協力企業 {mark}"
    page.fill("input[name=code]", f"S{datetime.now():%d%H%M%S}")
    page.fill("input[name=name]", name)
    page.select_option("select[name=role]", value="subcontractor")
    page.locator(SUBMIT).first.click()
    page.wait_for_url(re.compile(r"/masters/partners/\d+/$"), timeout=SAVE_WAIT_MS)
    return name


def _new_purchase_order(page, company, *, partner: str, mark: str) -> None:
    """発注の新規登録で明細 1 行 (数量 10) の発注を入れて保存する (つながりの無い取引先なので、相手へは送られない)。"""
    page.goto(f"{company.base_url}/purchasing/orders/new/")
    choose(page, "partner", partner)
    page.fill("textarea[name=note]", mark)
    choose(page, "lines-0-item", PART_NO)
    page.fill("input[name=lines-0-qty]", "10")
    page.fill("input[name=lines-0-unit_price]", "120")
    page.locator(SUBMIT).first.click()
    page.wait_for_url(re.compile(r"/purchasing/orders/\d+/$"), timeout=SAVE_WAIT_MS)


def test_e77_sent_outside_then_partial_receipt(page_a, company_a):
    login(page_a, company_a)
    mark = unique_mark("e77")
    partner = _new_unlinked_subcontractor(page_a, company_a, mark)
    _new_purchase_order(page_a, company_a, partner=partner, mark=mark)
    detail = page_a.url
    head = page_a.locator("[data-screen-head]")
    alert = page_a.locator("#po-alert")
    primary = page_a.locator(PRIMARY)

    expect(head).to_contain_text("下書き")
    expect(alert).to_contain_text("この取引先とは seizou でつながっていません。")
    expect(primary).to_have_count(1)
    expect(primary).to_contain_text("発注書を送った")
    expect(page_a.locator('form[action$="/send/"]')).to_have_count(0)

    primary.click()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    box = page_a.locator("#seizou-modal-box")
    expect(box).to_contain_text(EXPLAIN)
    expect(box).to_contain_text(partner)
    box.locator(SUBMIT).first.click()

    expect(page_a.locator("#seizou-modal[open]")).to_have_count(0)
    expect(alert).to_contain_text("発注書を seizou の外で送った発注です", timeout=LIVE_WAIT_MS)
    expect(head).to_contain_text("送った", timeout=LIVE_WAIT_MS)
    expect(primary).to_have_count(1, timeout=LIVE_WAIT_MS)
    expect(primary).to_contain_text("受入を記録する", timeout=LIVE_WAIT_MS)
    assert page_a.url == detail
    shot(page_a, "E-77", "a-sent-outside")

    primary.click()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    qty = box.locator("input[name=qty]")
    expect(qty).to_have_value(re.compile(r"^10(\.0+)?$"))
    qty.fill("4")
    box.locator(SUBMIT).first.click()

    expect(page_a.locator("#seizou-modal[open]")).to_have_count(0)
    expect(head).to_contain_text("一部納品", timeout=LIVE_WAIT_MS)
    expect(primary).to_have_count(1, timeout=LIVE_WAIT_MS)
    expect(primary).to_contain_text("受入を記録する")
    assert page_a.url == detail
    shot(page_a, "E-77", "a-partial")
