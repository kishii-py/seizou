"""E-62 上の帯の知らせの鈴 (依頼書 N348。403 回目。受け渡しの seizou-部品の6案-20261107/指示書.md の 4)。

手順: (前半) A 社の / で鈴を押す → Esc で閉じる → / を読み直す。(後半) B 社で鈴を 1 回開いて閉じる (印を 0 にする) →
A 社から B 社へ発注を送る → B 社の / を読み直す → 鈴を押す → Esc で閉じる → 読み直す。
期待: (前半) 鈴を押すと小窓に「知らせ」の題と、知らせの並びか「新しい知らせはありません。」のどちらかが出て、上の帯の赤い数は消える。
読み直しても赤い数は出ない (開いた = 見た)。(後半) 発注が届くと B 社の鈴に 1 以上の赤い数が付き、鈴の小窓に「新」の印の付いた
受信した注文が並び、押す先は受信した注文の詳細。開いて閉じて読み直すと赤い数は消える。

この試験が見る所: `#seizou-bell` の見え方・`#seizou-modal[open]` の数・`#seizou-bell-title` の字・`#seizou-bell-list` か
`#seizou-bell-empty` の見え方・`#seizou-bell-badge` の見え方と字・`#seizou-bell-list a[data-new]` の数と `href`。
後半は 2 社に同じ鍵が渡っていなければ E-15 と同じ理由で skip する。
見ない所 (理由): 前半の開く前の赤い数 (管理者を作った日時と種の行の日時で 0 にも 1 以上にもなる)、フィードバックの直した・見送った・返事の
知らせ (GitHub と世話役の操作が要る。数え方は単体の試験 tests/test_core_bell.py が見る)、99 を超えた「99+」と 10 件の上限
(単体の試験)、見られない部署の人 (単体の試験)、知らせを押した後の受信した注文の詳細 (E-15 が見る)。
DB に残る物: 前半は A 社に鈴を開いた日時の行 (BellOpened) 1 つ。後半は B 社の BellOpened と、A 社に発注 1 件・B 社に受信した注文 1 件
(E-15・E-16 と同じ)。
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import has_partner_secret, login, send_purchase_order, shot, unique_mark

NO_SECRET_REASON = "2 社に同じ鍵が渡っていない (SEIZOU_E2E_PARTNER_SECRET)"
PARTNER_PART_NO = "WK-1001"
ARRIVE_WAIT_MS = 15_000
BADGE_TEXT = re.compile(r"^[1-9]\d*\+?$")
INBOX_URL = re.compile(r"/connect/inbox/\d+/$")


def open_bell(page) -> None:
    """鈴を押し、小窓が開いて「知らせ」の題が出るまで待つ。"""
    page.locator("#seizou-bell").click()
    expect(page.locator("#seizou-modal[open]")).to_have_count(1)
    expect(page.locator("#seizou-bell-title")).to_have_text("知らせ")


def close_bell(page) -> None:
    page.keyboard.press("Escape")
    expect(page.locator("#seizou-modal[open]")).to_have_count(0)


def test_e62_bell_opens_and_clears_badge(page_a, company_a):
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/")
    expect(page_a.locator("#seizou-bell")).to_be_visible()

    open_bell(page_a)
    expect(page_a.locator("#seizou-bell-list, #seizou-bell-empty").first).to_be_visible()
    # 小窓の返事の先頭の oob が、上の帯の赤い数を hidden の空の箱に差し替える。
    expect(page_a.locator("#seizou-bell-badge")).to_be_hidden()
    shot(page_a, "E-62", "a-bell-open")
    close_bell(page_a)

    page_a.goto(f"{company_a.base_url}/")
    expect(page_a.locator("#seizou-bell")).to_be_visible()
    expect(page_a.locator("#seizou-bell-badge")).to_be_hidden()


def test_e62_new_inbox_counts_on_the_bell(page_a, page_b, company_a, company_b):
    login(page_a, company_a)
    login(page_b, company_b)
    if not (has_partner_secret(page_a, company_a) and has_partner_secret(page_b, company_b)):
        pytest.skip(NO_SECRET_REASON)

    # B 社で鈴を 1 回開いて閉じ、赤い数を 0 にしてから送る。
    page_b.goto(f"{company_b.base_url}/")
    open_bell(page_b)
    close_bell(page_b)
    page_b.goto(f"{company_b.base_url}/")
    expect(page_b.locator("#seizou-bell-badge")).to_be_hidden()

    send_purchase_order(page_a, company_a, partner_code="S001", part_no=PARTNER_PART_NO, qty=3, mark=unique_mark("e62"))

    # 赤い数は画面を描く時に数える (取り直しは無い) ので、届くまで / を読み直す。
    badge = page_b.locator("#seizou-bell-badge")
    deadline = ARRIVE_WAIT_MS
    while True:
        page_b.goto(f"{company_b.base_url}/")
        if badge.is_visible() or deadline <= 0:
            break
        page_b.wait_for_timeout(1_000)
        deadline -= 1_000
    expect(badge).to_be_visible()
    expect(badge).to_have_text(BADGE_TEXT)
    shot(page_b, "E-62", "b-bell-badge")

    open_bell(page_b)
    new_links = page_b.locator("#seizou-bell-list a[data-new]")
    expect(new_links.first).to_be_visible()
    hrefs = new_links.evaluate_all("links => links.map(link => link.getAttribute('href'))")
    assert any(INBOX_URL.search(href or "") for href in hrefs), f"「新」の知らせに受信した注文の詳細への link が無い: {hrefs!r}"
    shot(page_b, "E-62", "b-bell-open")
    close_bell(page_b)

    page_b.goto(f"{company_b.base_url}/")
    expect(page_b.locator("#seizou-bell")).to_be_visible()
    expect(page_b.locator("#seizou-bell-badge")).to_be_hidden()
