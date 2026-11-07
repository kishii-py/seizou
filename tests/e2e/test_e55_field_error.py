"""E-55 空のまま保存したときの字と、直した欄の赤枠と誤りの字をその場で消す (初見テスト R5 の V36・V37。候補 C25 の 2 と 3)。

手順: A 社で数量 10 の受注を入れて 4 を出荷し、納品書を出して請求書を作る (E-29 と同じ手順) → 請求書の詳細の「入金を記録する」
(小窓) で金額を空のまま押す → 金額にキーで 1000 と打つ → Tab で離れる → 金額を空に戻す → 押す → 1000 で押す。
同じことを、入金の記録の URL を直に開いた画面の全体でも行う。
期待: 止まると金額の欄の下に「金額を入れてください。」が出て、金額の欄に aria-invalid="true" が付く。打った直後に aria-invalid が外れ、
誤りの字は行の高さを残して見えなくなり、「入金の方法」の欄と金額の欄の y の差は打つ前と同じ。Tab で離れても、空に戻しても、
誤りは出し直さない。もう一度押すとまた止まり、誤りの字が見える。1000 で押すと、小窓は閉じて入金の枠に 1 件増え、
画面の全体は請求書の詳細へ移ってもう 1 件増える。

この試験が見る所: `#id_amount_error` の字と見え方 (computed style の visibility)・金額の欄の aria-invalid の有り無し・
`select[name=method]` と `input[name=amount]` の `bounding_box()` の y の差 (小窓では止まった赤い帯が 4 で消えて両方が上へ動くので、
欄どうしの差で比べる)・`#seizou-modal[open]` の数・`#payments tbody tr` の数・画面の全体の保存の後の道。
見ない所 (理由): 止まった知らせを閉じる動き (E-56)、B 社 (入力のカードのコードは 2 社で同じ)、選ぶ欄 (tom-select) の箱の赤
(元の select と人が触る箱の赤の付き方を、この版では確かめていない。夜の記録の「迷った事」)。
請求書を 1 つと入金を 2 件増やす (E-29 と同じく A 社の DB に残る)。
"""

import re

from playwright.sync_api import expect

from .conftest import fill_shipment, login, new_customer, new_order, shot, unique_mark

PART_NO = "WK-1001"
SUBMIT = "form[data-enter-guard] button.btn-primary[type=submit]"
EMPTY_AMOUNT = "金額を入れてください。"


def new_invoice(page, company, mark: str) -> str:
    """数量 10 の受注を入れて 4 を出荷し、納品書を出して請求書を作る (E-29 と同じ手順)。請求書の詳細の URL を返す。

    得意先は回すたびに新しく作る (同じ得意先の同じ締め期間の請求書は 1 枚だけ = R23。この試験は入金を残すので作り直しも通らない)。
    """
    customer = new_customer(page, company, mark)
    order_no = new_order(page, company, customer=customer, part_no=PART_NO, qty=10, unit_price=480, mark=mark)
    fill_shipment(page, company, order_no=order_no, qty=4)
    expect(page.locator(".seizou-toast[role=status]")).to_be_visible()

    page.goto(f"{company.base_url}/shipping/notes/new/")
    page.get_by_label(re.compile(rf"{re.escape(order_no)}(?!\d)")).check()
    page.locator(SUBMIT).first.click()
    page.wait_for_url(re.compile(r"/shipping/notes/\d+/$"))
    note_no = page.locator("h1").inner_text().strip()

    page.goto(f"{company.base_url}/billing/invoices/new/")
    option = page.locator("select[name=customer] option").filter(has_text=customer).first
    page.select_option("select[name=customer]", value=option.get_attribute("value"))
    page.get_by_label(re.compile(rf"{re.escape(note_no)}(?!\d)")).check()
    page.locator(SUBMIT).first.click()
    page.wait_for_url(re.compile(r"/billing/invoices/\d+/$"))
    return page.url


def _gap(scope) -> float:
    """「入金の方法」の欄の上の端と、金額の欄の上の端の差 (px)。"""
    return scope.locator("select[name=method]").bounding_box()["y"] - scope.locator("input[name=amount]").bounding_box()["y"]


def _has_invalid(amount) -> bool:
    return amount.evaluate("element => element.hasAttribute('aria-invalid')")


def _visibility(error) -> str:
    return error.evaluate("element => getComputedStyle(element).visibility")


def _fix_and_resubmit(page, scope, code: str, where: str) -> None:
    """止まった画面で、金額を打って誤りが消え、離れても空に戻しても出し直さず、押すとまた止まるまでを見る。"""
    amount = scope.locator("input[name=amount]")
    error = scope.locator("#id_amount_error")

    scope.locator(SUBMIT).first.click()
    expect(error).to_be_visible()
    expect(error).to_contain_text(EMPTY_AMOUNT)
    expect(amount).to_have_attribute("aria-invalid", "true")
    before = _gap(scope)
    shot(page, code, f"{where}-stopped")

    amount.click()
    amount.press_sequentially("1000")
    assert not _has_invalid(amount)
    expect(error).to_have_count(1)  # 字は残っている (行の高さを残す)
    expect(error).to_be_hidden()
    assert _visibility(error) == "hidden"
    assert abs(_gap(scope) - before) < 0.5, "誤りの字の行の高さが残っていない (下の欄が上へ動いた)"
    shot(page, code, f"{where}-typed")

    amount.press("Tab")
    assert not _has_invalid(amount)
    assert _visibility(error) == "hidden"

    amount.fill("")
    assert not _has_invalid(amount)
    assert _visibility(error) == "hidden"

    scope.locator(SUBMIT).first.click()
    expect(scope.locator("#id_amount_error")).to_be_visible()
    expect(scope.locator("input[name=amount]")).to_have_attribute("aria-invalid", "true")


def test_e55_fixed_field_clears_its_error(page_a, company_a):
    login(page_a, company_a)
    invoice_url = new_invoice(page_a, company_a, unique_mark("e55"))
    rows = page_a.locator("#payments tbody tr")

    # 小窓
    page_a.goto(invoice_url)
    page_a.locator("#payments a[data-modal-form]").click()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    box = page_a.locator("#seizou-modal-box")
    expect(box.locator("input[name=amount]")).to_have_value("")
    _fix_and_resubmit(page_a, box, "E-55", "modal")
    box.locator("input[name=amount]").fill("1000")
    box.locator(SUBMIT).first.click()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(0)
    expect(rows).to_have_count(1)
    assert page_a.url == invoice_url

    # 画面の全体 (保存で請求書の詳細へ移る)
    page_a.goto(f"{invoice_url}payments/new/")
    main = page_a.locator("main")
    expect(main.locator("input[name=amount]")).to_have_value("")
    _fix_and_resubmit(page_a, main, "E-55", "page")
    main.locator("input[name=amount]").fill("1000")
    main.locator(SUBMIT).first.click()
    page_a.wait_for_url(re.compile(r"/billing/invoices/\d+/$"))
    assert page_a.url.split("?")[0] == invoice_url
    expect(rows).to_have_count(2)
    shot(page_a, "E-55", "after")
