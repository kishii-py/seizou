"""E-46 受注の入力の明細の合計の枠 (N149。案 5-A)。

手順: A 社で受注の新規登録を開く → 得意先と 1 行目の部品を選び、数量 10・単価 480 を入れる → 「行を足す」で 2 行目に 5 × 100 を入れる
→ 2 行目の「消す」に印を付ける。保存しない (受注を増やさない)。
期待: 明細の見出しの右の枠 (明細の行の数・数量・金額) と保存のボタンの字が、打つたびに同じ数に変わる。
消す印の行は数えない。行の金額の枡と枠の金額が同じ字になる。

この試験が見る所: 1 行目を入れた後の枠「1」「10」「4,800」とボタン「1 行・合計 ¥4,800 で受注する」と行の金額の枡「4,800」、
2 行目を入れた後の「2」「15」「5,300」と「2 行・合計 ¥5,300 で受注する」、消す印の後の「1」「10」「4,800」。
ボタンは字で探さず、入力の form の主色の submit で引く (E-41 と同じ形)。
見ない所 (理由): 保存 (E-08 が見る。この試験は受注を増やさない)、金額の閲覧が無い人の枠 (試験 test_core_line_totals が字で見る)、
発注と一括出荷の小窓の枠 (同じ JS と断片。試験 test_core_line_totals が字で見る)、数でない数量の「—」(ブラウザの数の欄は数でない字を空として渡す)。
"""

from playwright.sync_api import expect

from .conftest import choose, login, seed_name, shot

CUSTOMER_CODE = "C001"
PART_NO = "WK-1001"
SUBMIT = "form[data-enter-guard] button.btn-primary[type=submit]"


def _expect_totals(page, rows: str, qty: str, amount: str) -> None:
    expect(page.locator("[data-line-totals-rows]")).to_have_text(rows)
    expect(page.locator("[data-line-totals-qty]")).to_have_text(qty)
    expect(page.locator("[data-line-totals-amount]")).to_have_text(amount)


def test_e46_order_totals_follow_typing(page_a, company_a):
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/orders/new/")
    submit = page_a.locator(SUBMIT).first
    _expect_totals(page_a, "0", "0", "0")
    expect(submit).to_have_text("受注する")

    choose(page_a, "customer", seed_name(company_a, "partners", CUSTOMER_CODE))
    choose(page_a, "lines-0-item", PART_NO)
    page_a.fill("input[name=lines-0-qty]", "10")
    page_a.fill("input[name=lines-0-unit_price]", "480")
    _expect_totals(page_a, "1", "10", "4,800")
    expect(submit).to_have_text("1 行・合計 ¥4,800 で受注する")
    expect(page_a.locator("#order-lines [data-line-amount]").first).to_have_text("4,800")

    page_a.locator("[data-add-order-line]").click()
    choose(page_a, "lines-1-item", PART_NO)
    page_a.fill("input[name=lines-1-qty]", "5")
    page_a.fill("input[name=lines-1-unit_price]", "100")
    _expect_totals(page_a, "2", "15", "5,300")
    expect(submit).to_have_text("2 行・合計 ¥5,300 で受注する")

    page_a.check("input[name=lines-1-DELETE]")
    _expect_totals(page_a, "1", "10", "4,800")
    expect(submit).to_have_text("1 行・合計 ¥4,800 で受注する")
    shot(page_a, "E-46", "a-order-totals")
