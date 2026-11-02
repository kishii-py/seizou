"""E-29 請求書 (仕様書の 6 番の 3 節)。

手順: 納品書を出した出荷を選んで請求書を作る → 印刷用 HTML と PDF。
期待: 出荷済みの物だけが選べ (R09)、支払期日が締日と支払の月と支払日から出る (決め 11-02 18:4x)。

この試験が見る所 (HTML だけ): A 社で数量 10 の受注を入れ、4 を出荷して納品書を出す。同じ受注にもう 1 回 2 を出荷する (納品書に入れない)。
左のメニューの「請求書」→「新規登録」で得意先 (標準の `<select>`) を選ぶと、出荷の欄 (checkbox) に 1 回目の出荷 (納品書番号の入った行) が
あり、2 回目の出荷 (納品書なし) の出荷番号が無い。納品書番号の行に印を付けて「保存」すると、請求書の詳細 (道が `/billing/invoices/<pk>/`) へ移る。
「印刷用の画面」(新しいタブ) に「請求書」・得意先の名前・`WK-1001`・数量 4 の行があり、「支払期日」の行の日付 (`Y-m-d`) が、
種の得意先の締日・支払の月・支払日と出荷日から試験の中で出した日と同じ。請求書に入れた出荷は、もう一度「新規登録」を開いた出荷の欄に無い。
見ない所 (理由): PDF (本文は HTML だけと決めた)、金額と消費税の値 (部品の試験が見る)、0 時をまたぐ回し (出荷日がずれて期日を比べられない)。
"""

import calendar
import csv
import re
from datetime import date

from playwright.sync_api import expect

from .conftest import COMPANIES_DIR, fill_shipment, login, new_order, seed_name, shot, unique_mark

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
    page.locator("aside nav").get_by_role("link", name="請求書", exact=True).click()
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

    page_a.locator("aside nav").get_by_role("link", name="納品書", exact=True).click()
    page_a.wait_for_url(re.compile(r"/shipping/notes/$"))
    page_a.get_by_role("link", name="新規登録").first.click()
    page_a.wait_for_url(re.compile(r"/shipping/notes/new/$"))
    page_a.get_by_label(re.compile(rf"{re.escape(order_no)}(?!\d)")).check()
    page_a.get_by_role("button", name="保存", exact=True).click()
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
    page_a.get_by_role("button", name="保存", exact=True).click()
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
    line = printed.locator("table.report-lines tbody tr").filter(has_text="WK-1001")
    expect(line).to_have_count(1)
    assert "4" in [cell.strip() for cell in line.locator("td").all_inner_texts()]
    due_cell = printed.locator("table.report-meta tr").filter(has_text="支払期日").locator("td")
    expected = _expected_due_date(*_terms(company_a.id, CUSTOMER_CODE), shipped)
    expect(due_cell).to_have_text(expected.isoformat())
    shot(printed, "E-29", "a-print")

    page_a.goto(f"{company_a.base_url}/billing/invoices/new/")
    option = page_a.locator("select[name=customer] option").filter(has_text=customer).first
    page_a.select_option("select[name=customer]", value=option.get_attribute("value"))
    later = [text.strip() for text in page_a.locator("#id_shipments label").all_inner_texts()]
    assert not [text for text in later if re.search(rf"{re.escape(first_shipment)}(?!\d)", text)]
