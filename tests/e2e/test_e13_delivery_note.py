"""E-13 納品書 (仕様書の 6 番の 3 節)。

手順: 出荷を選んで納品書 → ブラウザの印刷 → PDF のボタン。
期待: 新しいタブに A4 1 枚。得意先・品目・数量・単価・金額・合計・会社の名前と住所と電話と振込先とロゴ。数量が出荷と一致。PDF の日本語が化けない。

この試験が見る所 (HTML だけ): A 社で数量 10 の受注を入れて 4 を出荷し、左のメニューの「納品書」→「新規登録」で、その出荷
(候補の字に受注番号が入る) に印を付けて「保存」すると、納品書の詳細へ移る。「印刷用の画面」の link は新しいタブで開き、
その中に「納品書」・得意先の名前・`WK-1001`・数量 4 がある。
見ない所 (理由): PDF (本文は HTML だけと決めた。PDF の日本語は部品の試験が見る)、A4 1 枚・単価・金額・合計・会社の住所と振込先とロゴ
(印刷の見た目は今夜の範囲の外)。
"""

import re

from playwright.sync_api import expect

from .conftest import fill_shipment, login, menu_link, new_order, seed_name, shot, unique_mark

CUSTOMER_CODE = "C001"


def test_e13_note_and_print(context, page_a, company_a):
    login(page_a, company_a)
    customer = seed_name(company_a, "partners", CUSTOMER_CODE)
    order_no = new_order(
        page_a,
        company_a,
        customer=customer,
        part_no="WK-1001",
        qty=10,
        unit_price=480,
        mark=unique_mark("e13"),
    )
    fill_shipment(page_a, company_a, order_no=order_no, qty=4)
    expect(page_a.locator(".seizou-toast[role=status]")).to_be_visible()

    menu_link(page_a, "納品書").click()
    page_a.wait_for_url(re.compile(r"/shipping/notes/$"))
    page_a.get_by_role("link", name="新規登録").first.click()
    page_a.wait_for_url(re.compile(r"/shipping/notes/new/$"))

    page_a.get_by_label(re.compile(rf"{re.escape(order_no)}(?!\d)")).check()
    page_a.locator("form[data-enter-guard] button.btn-primary[type=submit]").first.click()  # 主のボタンの字は画面ごとの動詞 (205 回目から) なので、字でなく主色の submit で押す
    page_a.wait_for_url(re.compile(r"/shipping/notes/\d+/$"))
    shot(page_a, "E-13", "a-note")

    with context.expect_page() as opened:
        page_a.get_by_role("link", name="印刷用の画面").click()
    printed = opened.value
    printed.wait_for_load_state("load")
    body = printed.locator("body")
    expect(body).to_contain_text("納品書")
    expect(body).to_contain_text(customer)
    expect(body).to_contain_text("WK-1001")
    line = printed.locator("table.report-lines tbody tr").filter(has_text="WK-1001")
    expect(line).to_have_count(1)
    assert "4" in [cell.strip() for cell in line.locator("td").all_inner_texts()]
    shot(printed, "E-13", "a-print")
