"""E-76 受注の詳細の頭の右の緑のボタンが、小窓で保存した後に、読み直しなしで次の操作に替わる (本人の決め 11-12 19:0x。517 回目)。

手順 (A 社。入荷の設定は通す): 在庫の無い新しい部品を作る → その部品の受注 (数量 10) を入れる → 詳細の緑の「指示書を発行する」を押す →
小窓で数量と工程を入れて発行する。
期待: 入れた直後の緑は「指示書を発行する」(明細 1 行なので小窓を直に開く)。発行すると、小窓が閉じ、画面を読み直さずに緑が「出荷登録」に替わる。
手順 (B 社。入荷が無いと指示書と出荷を止める設定): 受注 (数量 10) を入れる → 詳細の緑の「入荷を記録する」を押す → 小窓を保存する。
期待: 入れた直後の緑は「入荷を記録する」で、小窓の数量には受注の数が入っている。保存すると、読み直さずに緑が「入荷を記録する」でなくなる。

この試験が見る所: `[data-detail-actions] .btn-primary` の数と字・`#seizou-modal[open]` の数・小窓の `input[name=qty]` の値・右上の済んだ知らせ・道。
見ない所 (理由): 明細が 2 行以上の時の行を選ぶメニュー・権限ごとの出し分け・在庫で足りる時 (tests/test_orders_detail_next_action.py が見る)。
B 社の種の部品は、ほかの E2E が在庫や指示書を足す事があるので、保存の後の字は「指示書を発行する」か「出荷登録」のどちらでも通す。
A 社は、在庫の無い事を確かにするために、回すたびに新しい部品を作る。部品・受注・指示書・入荷を増やす (2 社の DB に残る)。
"""

import re

from playwright.sync_api import expect

from .conftest import login, new_item, new_order, seed_name, shot, unique_mark

CUSTOMER_CODE = "C001"
SUBMIT = "form[data-enter-guard] button.btn-primary[type=submit]"
PRIMARY = "[data-detail-actions] .btn-primary"


def test_e76_issue_then_ship_without_reload(page_a, company_a):
    login(page_a, company_a)
    mark = unique_mark("e76")
    new_item(page_a, company_a, part_no=mark, name="E2E の作り物の部品 (緑のボタン)")
    new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", CUSTOMER_CODE),
        part_no=mark,
        qty=10,
        unit_price=480,
        mark=mark,
    )
    detail = page_a.url
    primary = page_a.locator(PRIMARY)
    expect(primary).to_have_count(1)
    expect(primary).to_contain_text("指示書を発行する")

    primary.click()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    box = page_a.locator("#seizou-modal-box")
    box.locator("input[name=qty]").fill("10")
    box.locator("input[type=checkbox][name=processes]").first.check()
    box.locator(SUBMIT).first.click()

    expect(page_a.locator(".seizou-toast[role=status]")).to_be_visible()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(0)
    expect(primary).to_have_count(1)
    expect(primary).to_contain_text("出荷登録")
    assert page_a.url == detail
    shot(page_a, "E-76", "a-issue-to-ship")


def test_e76_receive_first_at_stop_company(page_b, company_b):
    login(page_b, company_b)
    new_order(
        page_b,
        company_b,
        customer=seed_name(company_b, "partners", CUSTOMER_CODE),
        part_no="SB-2001",
        qty=10,
        unit_price=120,
        mark=unique_mark("e76b"),
    )
    detail = page_b.url
    primary = page_b.locator(PRIMARY)
    expect(primary).to_have_count(1)
    expect(primary).to_contain_text("入荷を記録する")

    primary.click()
    expect(page_b.locator("#seizou-modal[open]")).to_have_count(1)
    box = page_b.locator("#seizou-modal-box")
    expect(box.locator("input[name=qty]")).to_have_value(re.compile(r"^10(\.0+)?$"))
    box.locator(SUBMIT).first.click()

    expect(page_b.locator(".seizou-toast[role=status]")).to_be_visible()
    expect(page_b.locator("#seizou-modal[open]")).to_have_count(0)
    expect(primary).to_have_count(1)
    expect(primary).to_contain_text(re.compile("指示書を発行する|出荷登録"))
    expect(primary).not_to_contain_text("入荷を記録する")
    assert page_b.url == detail
    shot(page_b, "E-76", "b-receive-to-next")
