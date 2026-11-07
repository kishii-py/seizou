"""E-60 指示書の一覧の「工程の進み」の列に、工程ごとの名前・数・棒が並ぶ (依頼書 N348。398 回目。受け渡しの seizou-部品の6案-20261107/指示書.md の 2)。

手順: (前半) A 社の指示書の一覧 (/workorders/) を開く。(後半) 数量 10 の受注を入れ、工程 `TURN`・数量 10 の指示書を発行し、
実績の新規登録でその工程に 3 を入れてから、一覧をその指示書番号で絞る。
期待: (前半) 表の見出しに「工程の進み」があり、工程のある行の欄に工程ごとの 1 行 (名前・「実績/予定」の数・棒) が並ぶ。
棒の値 (aria-valuenow) は 0〜100 の整数で、行の data-step-progress と同じ。(後半) 3/10 の工程の棒は 30 で、字は 3/10、
棒の中の色の幅は 30%。

この試験が見る所: `#rows thead th` の「工程の進み」・`ul[aria-label="工程の進み"]` の `li[data-step-progress]` の数と値・
各 li の `[role="progressbar"]` の `aria-valuenow`・`aria-valuemin`・`aria-valuemax`・名前の `span[title]`・数の字の形。
後半は、絞った 1 行の li が 1 つで、`data-step-progress` と `aria-valuenow` が 30、字が 3/10、棒の中の span の style が width: 30%。
見ない所 (理由): 予定に届いた工程の緑 (後半で 10 まで入れると、予定を超える知らせの段も絡むので入れない。色の出し分けは単体の試験
tests/test_workorders_list_progress.py が見る)、工程の無い指示書の「—」(A 社の種の指示書は工程を持つ)、CSV の字 (単体の試験)、
B 社 (同じ土台)。前半は種の値が 0% でも通る (A 社の種に実績の行が無い。tests/test_core_screen_queries.py)。
DB に残る物: 前半は無し。後半は A 社に受注 1・指示書 1・実績 1 が残る。
"""

import re
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from .conftest import choose, login, new_order, seed_name, shot, unique_mark

CUSTOMER_CODE = "C001"
PART_NO = "WK-1001"
PROCESS_CODE = "TURN"
SUBMIT = "form[data-enter-guard] button.btn-primary[type=submit]"
PROGRESS = 'ul[aria-label="工程の進み"]'
COUNT_TEXT = re.compile(r"^\d[\d,]*(\.\d+)?/\d[\d,]*(\.\d+)?$")


def _count_text(step):
    """li の頭の行 (名前と数の 2 つの span) の 2 つ目 = 「実績/予定」の字。"""
    return step.locator(":scope > span").first.locator(":scope > span").nth(1)


def test_e60_list_shows_one_bar_per_step(page_a, company_a):
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/workorders/")
    expect(page_a.locator("#rows thead th").filter(has_text="工程の進み")).to_have_count(1)
    rows = page_a.locator("#rows tbody tr")
    if rows.count() == 0:
        pytest.skip("A 社の指示書の一覧に行が無い (種の指示書が無い)")
    with_steps = rows.filter(has=page_a.locator(PROGRESS))
    if with_steps.count() == 0:
        pytest.skip("A 社の指示書の一覧に工程のある行が無い")

    steps = with_steps.first.locator(f"{PROGRESS} > li[data-step-progress]")
    assert steps.count() >= 1
    for index in range(steps.count()):
        step = steps.nth(index)
        pct = step.get_attribute("data-step-progress")
        assert re.fullmatch(r"\d{1,3}", pct) and 0 <= int(pct) <= 100, f"data-step-progress が 0〜100 の整数でない: {pct!r}"
        bar = step.locator('[role="progressbar"]')
        expect(bar).to_have_count(1)
        expect(bar).to_have_attribute("aria-valuenow", pct)
        expect(bar).to_have_attribute("aria-valuemin", "0")
        expect(bar).to_have_attribute("aria-valuemax", "100")
        expect(step.locator("span[title]")).to_have_count(1)
        expect(_count_text(step)).to_have_text(COUNT_TEXT)
    shot(page_a, "E-60", "a-workorders-progress")


def test_e60_bar_grows_with_progress(context, page_a, company_a):
    login(page_a, company_a)
    order_no = new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", CUSTOMER_CODE),
        part_no=PART_NO,
        qty=10,
        unit_price=480,
        mark=unique_mark("e60"),
    )

    # 指示書の発行は E-11 と同じ (受注の明細を受注番号で選び、数量 10、工程は TURN の 1 つ)。
    page_a.goto(f"{company_a.base_url}/workorders/new/")
    choose(page_a, "order_line", order_no)
    page_a.fill("input[name=qty]", "10")
    page_a.get_by_role("checkbox", name=re.compile(rf"^{re.escape(PROCESS_CODE)}\b")).check()
    page_a.locator(SUBMIT).first.click()
    page_a.wait_for_url(re.compile(r"/workorders/\d+/$"))
    workorder_no = page_a.locator("h1").inner_text().strip()

    # 実績の新規登録で、その指示書の工程に 3 を入れる (E-11 と同じ)。
    other = context.new_page()
    other.goto(f"{company_a.base_url}/progress/records/new/")
    choose(other, "workorder_step", workorder_no)
    other.fill("input[name=qty]", "3")
    other.locator(SUBMIT).first.click()
    other.wait_for_url(lambda url: urlparse(url).path != "/progress/records/new/")
    other.close()

    page_a.goto(f"{company_a.base_url}/workorders/?q={workorder_no}")
    # キーワードは部分一致なので、番号の後ろに数字が続く別の指示書を除く (E-11 と同じ)。
    row = page_a.locator("#rows tbody tr").filter(has_text=re.compile(rf"{re.escape(workorder_no)}(?!\d)"))
    expect(row).to_have_count(1)
    steps = row.locator(f"{PROGRESS} > li[data-step-progress]")
    expect(steps).to_have_count(1)
    step = steps.first
    expect(step).to_have_attribute("data-step-progress", "30")
    expect(step.locator('[role="progressbar"]')).to_have_attribute("aria-valuenow", "30")
    expect(_count_text(step)).to_have_text("3/10")
    expect(step.locator('[role="progressbar"] > span')).to_have_attribute("style", re.compile(r"width:\s*30%"))
    shot(page_a, "E-60", "a-workorders-progress-30")
