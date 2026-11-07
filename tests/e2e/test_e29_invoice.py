"""E-29 請求書 (仕様書の 6 番の 3 節)。

手順: 納品書を出した出荷を選んで請求書を作る → 印刷用 HTML と PDF。
期待: 出荷済みの物だけが選べ (R09)、支払期日が締日と支払の月と支払日から出る (決め 11-02 18:4x)。

この試験が見る所 (HTML だけ): A 社で数量 10 の受注を入れ、4 を出荷して納品書を出す。同じ受注にもう 1 回 2 を出荷する (納品書に入れない)。
左のメニューの「請求書」→「新規登録」で得意先 (標準の `<select>`) を選ぶと、出荷の欄 (checkbox) に 1 回目の出荷 (納品書番号の入った行) が
あり、2 回目の出荷 (納品書なし) の出荷番号が無い。納品書番号の行に印を付けて「請求書を作る」を押すと、請求書の詳細 (道が `/billing/invoices/<pk>/`) へ移る。
同じ置き場で回すたびに今日の出荷で請求書を作るので、同じ締め期間の 2 回目からは同じ得意先の同じ締め期間の 2 枚目として右上の赤の知らせで止まる (R23)。
そのときは知らせの「… を取り消して、この期間の全部の出荷で作り直す」のボタンで作り直しの画面へ進み、「… を取り消して作り直す」を押して詳細へ移る。
「印刷用の画面」(新しいタブ) に「請求書」・得意先の名前・`WK-1001`・この回の出荷番号で数量 4 の行が 1 つあり (作り直した請求書には前の回の出荷も入る)、
「支払期日」の行の日付 (`Y-m-d`) が、種の得意先の締日・支払の月・支払日と出荷日から試験の中で出した日と同じ。
請求書に入れた出荷は、もう一度「新規登録」を開いた出荷の欄に無い。
見ない所 (理由): PDF (本文は HTML だけと決めた)、金額と消費税の値 (部品の試験が見る)、0 時をまたぐ回し (出荷日がずれて期日を比べられない)。
"""

import calendar
import csv
import re
from datetime import date

from playwright.sync_api import expect

from .conftest import COMPANIES_DIR, fill_shipment, login, menu_link, new_order, seed_name, shot, unique_mark

CUSTOMER_CODE = "C001"
MONTH_OFFSETS = {"当月": 0, "翌月": 1, "翌々月": 2}


def _terms(company_id: str, code: str) -> tuple[int, int, int]:
    """種の取引先の (締日, 支払の月に足す月数, 支払日)。読むだけ。"""
    with (COMPANIES_DIR / company_id / "partners.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["取引先コード"] == code:
                return int(row["締日"]), MONTH_OFFSETS[row["支払の月"]], int(row["支払日"])
    raise LookupError(f"種の partners.csv に {code} が無い")


def _expected_due_date(closing_day: int, month_offset: int, payment_day: int, shipped: date) -> date:
    """支払期日。(1) 出荷日が締日 (月の日数より大きければ月末) までならその月、過ぎていれば翌月が締めの月、
    (2) 締めの月に支払の月 (当月 0・翌月 1・翌々月 2) を足す、(3) 支払日が月の日数より大きければ月末。"""

    def last_day(year: int, month: int) -> int:
        return calendar.monthrange(year, month)[1]

    index = shipped.year * 12 + shipped.month - 1
    if shipped.day > min(closing_day, last_day(shipped.year, shipped.month)):
        index += 1
    index += month_offset
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(payment_day, last_day(year, month)))


def _shipment_no(page) -> str:
    return page.locator("h1").inner_text().strip()


def _open_invoice_form(page, company, customer: str) -> None:
    menu_link(page, "請求書").click()
    page.wait_for_url(re.compile(r"/billing/invoices/$"))
    page.get_by_role("link", name="新規登録").first.click()
    page.wait_for_url(re.compile(r"/billing/invoices/new/$"))
    option = page.locator("select[name=customer] option").filter(has_text=customer).first
    page.select_option("select[name=customer]", value=option.get_attribute("value"))


def test_e29_billable_only_and_due_date(context, page_a, company_a):
    login(page_a, company_a)
    customer = seed_name(company_a, "partners", CUSTOMER_CODE)
    order_no = new_order(
        page_a, company_a, customer=customer, part_no="WK-1001", qty=10, unit_price=480, mark=unique_mark("e29")
    )
    shipped = date.today()

    fill_shipment(page_a, company_a, order_no=order_no, qty=4)
    expect(page_a.locator(".seizou-toast[role=status]")).to_be_visible()
    first_shipment = _shipment_no(page_a)

    menu_link(page_a, "納品書").click()
    page_a.wait_for_url(re.compile(r"/shipping/notes/$"))
    page_a.get_by_role("link", name="新規登録").first.click()
    page_a.wait_for_url(re.compile(r"/shipping/notes/new/$"))
    page_a.get_by_label(re.compile(rf"{re.escape(order_no)}(?!\d)")).check()
    page_a.locator("form[data-enter-guard] button.btn-primary[type=submit]").first.click()  # 主のボタンの字は画面ごとの動詞 (205 回目から) なので、字でなく主色の submit で押す
    page_a.wait_for_url(re.compile(r"/shipping/notes/\d+/$"))
    note_no = page_a.locator("h1").inner_text().strip()

    fill_shipment(page_a, company_a, order_no=order_no, qty=2)
    expect(page_a.locator(".seizou-toast[role=status]")).to_be_visible()
    second_shipment = _shipment_no(page_a)
    assert second_shipment != first_shipment

    _open_invoice_form(page_a, company_a, customer)
    labels = [text.strip() for text in page_a.locator("#id_shipments label").all_inner_texts()]
    with_note = [text for text in labels if re.search(rf"{re.escape(note_no)}(?!\d)", text)]
    assert len(with_note) == 1
    assert re.search(rf"{re.escape(first_shipment)}(?!\d)", with_note[0])
    assert not [text for text in labels if re.search(rf"{re.escape(second_shipment)}(?!\d)", text)]

    page_a.get_by_label(re.compile(rf"{re.escape(note_no)}(?!\d)")).check()
    with page_a.expect_navigation():
        page_a.locator("form[data-enter-guard] button.btn-primary[type=submit]").first.click()  # 主のボタンの字は画面ごとの動詞 (205 回目から) なので、字でなく主色の submit で押す
    stop = page_a.locator("[data-notice=stop]")
    if stop.count():
        # 前の回の請求書が同じ締め期間にある (R23)。知らせのボタンで作り直す (前の回の請求書を取り消し、この回の出荷も入れて 1 枚にする)
        stop.locator("[data-notice-link]").click()
        page_a.wait_for_url(re.compile(r"/billing/invoices/\d+/reissue/$"))
        page_a.get_by_role("button", name=re.compile("を取り消して作り直す")).click()
    page_a.wait_for_url(re.compile(r"/billing/invoices/\d+/$"))
    shot(page_a, "E-29", "a-invoice")

    with context.expect_page() as opened:
        page_a.get_by_role("link", name="印刷用の画面").click()
    printed = opened.value
    printed.wait_for_load_state("load")
    body = printed.locator("body")
    expect(body).to_contain_text("請求書")
    expect(body).to_contain_text(customer)
    expect(body).to_contain_text("WK-1001")
    line = printed.locator("table.report-lines tbody tr").filter(has_text=first_shipment).filter(has_text="WK-1001")
    expect(line).to_have_count(1)
    assert "4" in [cell.strip() for cell in line.locator("td").all_inner_texts()]
    due_cell = printed.locator("table.report-meta tr").filter(has_text="支払期日").locator("td")
    expected = _expected_due_date(*_terms(company_a.id, CUSTOMER_CODE), shipped)
    # 印刷の支払期日は「2026年12月31日」の形 (invoice.html の date:"Y年n月j日")
    expect(due_cell).to_have_text(f"{expected.year}年{expected.month}月{expected.day}日")
    shot(printed, "E-29", "a-print")

    # 2 回目の出荷の納品書を出しておく (得意先に請求の候補が残り、同じ締め期間の 2 回目以降でも請求書の新規登録に得意先が出る。
    # 「取り消して作り直す」が得意先の納品書を全部 1 枚にまとめるため、これが無いと 2 回目から下の確かめで止まる)
    page_a.goto(f"{company_a.base_url}/shipping/notes/new/")
    page_a.get_by_label(re.compile(rf"{re.escape(order_no)}(?!\d)")).check()
    page_a.locator("form[data-enter-guard] button.btn-primary[type=submit]").first.click()
    page_a.wait_for_url(re.compile(r"/shipping/notes/\d+/$"))

    page_a.goto(f"{company_a.base_url}/billing/invoices/new/")
    option = page_a.locator("select[name=customer] option").filter(has_text=customer).first
    page_a.select_option("select[name=customer]", value=option.get_attribute("value"))
    later = [text.strip() for text in page_a.locator("#id_shipments label").all_inner_texts()]
    assert not [text for text in later if re.search(rf"{re.escape(first_shipment)}(?!\d)", text)]
