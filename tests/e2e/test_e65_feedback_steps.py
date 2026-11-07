"""E-65 フィードバックの送る画面の左右と、詳細の「この件の進み具合」の段の帯 (依頼書 N348。396 回目。受け渡しの seizou-フィードバックの段と送る画面-20261107/指示書.md)。

手順: A 社で /feedback/new/ を開く → 種類「不具合」・題・困りごとを入れて保存する。
期待: 送る画面は、AI の鍵のある会社では左に AI の聞き取りのカード、右に入力の形が並び、鍵の無い会社では入力の形だけが出る
(どちらでも通る)。保存すると詳細へ移り、題の行の下に段の帯が出る。段は 4 つで、下書きの件は最初の段 (書いた) だけが済んだ印 (✓ と主色) で、
ほかの 3 つは済んだ印も直した・見送ったの色も無い。詳細にはフィードバックの画面の上のタブが無い (依頼書 N321 の決め)。

この試験が見る所: `#feedback-chat` の有無に応じた `#feedback-columns` の数と `#feedback-form-column form` (鍵の無い会社は
`form[data-enter-guard]`)・保存の後の URL・`section#feedback-steps` の `data-steps-done`・`li[data-step]` の数と class と `data-content`・
`#feedback-tabs` の数。
見ない所 (理由): AI の段の「送信」(本物の AI に送らない)、確認済み・GitHub に送った・直した・見送ったの段 (世話役の操作と GitHub が要る。
段の描き分けは単体の試験 tests/test_feedback_steps.py が見る)、「送った件」の字 (E-58 が見る)、みんなの Issue (E-74)、
画面の絵を添える欄、B 社 (同じ部品)。
DB に残る物: A 社にフィードバック 1 件 (下書き。題は `E2E-fb-<日時>`)。
"""

import re

from playwright.sync_api import expect

from .conftest import login, shot, unique_mark

SUBMIT = "form[data-enter-guard] button.btn-primary[type=submit]"
PROBLEM = "E2E の作り物の困りごと (試験が書いた)"
STEP_DONE_OR_TONE = re.compile(r"\bstep-(primary|success|error)\b")


def test_e65_detail_shows_steps_band(page_a, company_a):
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/feedback/new/")
    expect(page_a.locator("form[data-enter-guard]")).to_have_count(1)
    if page_a.locator("#feedback-chat").count():
        # AI の鍵のある会社: 左に聞き取りのカード、右に入力の形。
        expect(page_a.locator("#feedback-columns")).to_have_count(1)
        expect(page_a.locator("#feedback-form-column form[data-enter-guard]")).to_have_count(1)
    else:
        # 鍵の無い会社: 入力の形だけ。
        expect(page_a.locator("#feedback-columns")).to_have_count(0)

    title = unique_mark("fb")
    page_a.select_option("select[name=kind]", value="bug")
    page_a.fill("input[name=title]", title)
    page_a.fill("textarea[name=problem]", PROBLEM)
    page_a.locator(SUBMIT).first.click()
    page_a.wait_for_url(re.compile(r"/feedback/\d+/$"))

    band = page_a.locator('section#feedback-steps[data-steps-done="draft"]')
    expect(band).to_be_visible()
    steps = band.locator("li[data-step]")
    expect(steps).to_have_count(4)
    expect(steps.first).to_have_class(re.compile(r"\bstep-primary\b"))
    expect(steps.first).to_have_attribute("data-content", "✓")
    for index in range(1, 4):
        expect(steps.nth(index)).not_to_have_class(STEP_DONE_OR_TONE)
    expect(page_a.locator("#feedback-tabs")).to_have_count(0)
    expect(page_a.locator("main")).to_contain_text(title)
    shot(page_a, "E-65", "a-feedback-steps")
