"""E-08 受注の入力 (仕様書の 6 番の 3 節)。

手順: 明細 2 行の受注を入れる → 数量に 0 → 単価を入れて金額を見る → 出荷した後に数量を出荷済みより下げる。
期待: 一覧に出る。0 は欄の横に赤の文で保存されない (入れた値は残る)。金額 = 数量 × 単価。出荷済みより下げると止まる。

この試験が見る所: ログイン → ダッシュボード → 左のメニューの「受注」→「新規登録」→ 明細 1 行の受注を保存
→ 受注の詳細 (道が `/orders/<pk>/`) と知らせ → 受注の一覧の絞り込みに印を入れて、その 1 行だけが出ること。
見ない所 (理由): 明細 2 行 (今夜の指示は 1 行)、数量 0 の赤 (余力の試験)、金額の計算 (services の試験が見る)、
出荷済みより下げる所 (編集の画面。今夜の範囲の外)。
"""

import re
from datetime import date

import pytest
from playwright.sync_api import expect

from .conftest import choose, login, menu_link, new_order, seed_name, shot, unique_mark

PART_NO = "WK-1001"
CUSTOMER_CODE = "C001"
STANDARD_PRICE = "480"


def test_e08_one_line_order_in_list(page_a, company_a):
    login(page_a, company_a)
    menu_link(page_a, "受注").click()
    page_a.wait_for_url(re.compile(r"/orders/$"))
    page_a.get_by_role("link", name="新規登録").first.click()
    page_a.wait_for_url(re.compile(r"/orders/new/$"))

    mark = unique_mark("e08")
    order_no = new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", CUSTOMER_CODE),
        part_no=PART_NO,
        qty=10,
        unit_price=STANDARD_PRICE,
        mark=mark,
    )

    assert re.search(r"/orders/\d+/$", page_a.url)
    expect(page_a.locator(".seizou-toast[role=status]")).to_be_visible()
    shot(page_a, "E-08", "a-order-detail")

    menu_link(page_a, "受注").click()
    page_a.wait_for_url(re.compile(r"/orders/$"))
    page_a.fill("#list-q", mark)
    rows = page_a.locator("#rows tbody tr")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text(mark)
    expect(rows.first.locator("td").first).to_have_text(order_no)
    shot(page_a, "E-08", "a-order-in-list")


@pytest.mark.xfail(reason="06 の E-08 の「欄の横に赤の文」が今の画面で通らない: 数量 0 は欄の下でなく、画面の上に赤の知らせ (R02) で止まる", strict=True)
def test_e08_zero_qty_is_red(page_a, company_a):
    """E-08 の 2 つ目の期待 (余力): 数量 0 は欄の横に赤の文で保存されず、入れた値は残る。

    見ない所 (理由): 単価を入れて金額を見る (金額は保存のときに services が計算する)。
    """
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/orders/new/")
    mark = unique_mark("e08z")
    choose(page_a, "customer", seed_name(company_a, "partners", CUSTOMER_CODE))
    page_a.fill("input[name=customer_order_no]", mark)
    page_a.fill("input[name=order_date]", date.today().isoformat())
    choose(page_a, "lines-0-item", PART_NO)
    page_a.fill("input[name=lines-0-qty]", "0")
    page_a.fill("input[name=lines-0-unit_price]", STANDARD_PRICE)
    page_a.locator("form[data-enter-guard] button.btn-primary[type=submit]").first.click()

    # 止まること (道が変わらない・入れた値が残る・画面の上に赤の知らせ R02) は今の画面で通る。先に見る。
    assert re.search(r"/orders/new/$", page_a.url)
    expect(page_a.locator("input[name=customer_order_no]")).to_have_value(mark)
    expect(page_a.locator(".alert-error")).to_contain_text("R02")
    shot(page_a, "E-08", "a-zero-qty")
    # 06 の期待は欄の横の赤の文。明細の行の中 (数量の欄の下) に赤の文があるか。今は無いので、上の印 (期待どおりに動かない理由つき) を付けてある。
    expect(page_a.locator("#order-lines .text-error").first).to_be_visible()


NEXT_FRAMES = "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"


def _menu_links_readable(page, row) -> list[str]:
    """開いた行のメニューの項目 (a) が全部、左のメニューの右にあり、真ん中の点の一番上がその項目であることを見る。字を返す。"""
    links = row.locator("details.row-menu[open] ul li a")
    expect(links.first).to_be_visible()
    sidebar_right = page.locator("aside").first.evaluate("aside => aside.getBoundingClientRect().right")
    texts = []
    for index in range(links.count()):
        link = links.nth(index)
        placed = link.evaluate(
            """link => {
                const box = link.getBoundingClientRect();
                const top = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
                return { left: box.left, onTop: !!top && link.contains(top) };
            }"""
        )
        assert placed["left"] >= sidebar_right, link.inner_text()
        assert placed["onTop"], link.inner_text()
        texts.append(link.inner_text().strip())
    return texts


def test_e08_line_menu_readable_beside_sidebar(page_a, company_a):
    """期待 (06 の E-08): 一覧に出る。0 は欄の横に赤の文で保存されない (入れた値は残る)。金額 = 数量 × 単価。出荷済みより下げると止まる。

    見る所: 受注の詳細の明細の ⋮ と右クリックのメニューが、幅 1280 の窓で左のメニューの裏に隠れず、項目の字が全部読めること (初見テスト R3)。
    見ない所: 06 の E-08 の期待そのもの (この関数は受注を作った後の詳細の明細のメニューだけを見る)。
    """
    login(page_a, company_a)
    new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", CUSTOMER_CODE),
        part_no=PART_NO,
        qty=10,
        unit_price=STANDARD_PRICE,
        mark=unique_mark("e08m"),
    )
    row = page_a.locator("tr[data-row-menu]").first
    # 行のメニューは画面の scroll で閉じる。押す前に行を窓の中へ送り、送った分の scroll の event (次の描画で届く) を待つ。
    row.scroll_into_view_if_needed()
    page_a.evaluate(NEXT_FRAMES)

    row.locator('summary[aria-label="行の操作"]').click()
    texts = _menu_links_readable(page_a, row)
    for label in ("出荷する", "指示書を発行する", "発注する"):
        assert label in texts, label
    shot(page_a, "E-08", "a-line-menu")

    page_a.keyboard.press("Escape")
    expect(row.locator("details.row-menu[open]")).to_have_count(0)
    row.click(button="right")
    texts = _menu_links_readable(page_a, row)
    assert "出荷する" in texts


def test_e08_amount_before_save(page_a, company_a):
    """期待 (06 の E-08 の一部): 金額 = 数量 × 単価。

    見る所: 受注の新規登録で部品と数量と単価を入れると、保存の前に明細の行の金額の枡に数量 × 単価が出ること (02 の「金額 [自動]」)。
    見ない所: 保存の金額 (services が得意先の端数の扱いで丸める。services の試験が見る)。保存はしない。
    """
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/orders/new/")
    choose(page_a, "lines-0-item", PART_NO)
    page_a.fill("input[name=lines-0-qty]", "10")
    page_a.press("input[name=lines-0-qty]", "Tab")
    page_a.fill("input[name=lines-0-unit_price]", STANDARD_PRICE)
    page_a.press("input[name=lines-0-unit_price]", "Tab")

    expect(page_a.locator("#order-lines [data-order-line]").first.locator("[data-line-amount]")).to_have_text("4,800")
    shot(page_a, "E-08", "a-amount-before-save")
