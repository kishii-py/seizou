"""E-75 調整の記録と取消を、請求書の詳細の小窓で行う (売掛の 2 段目 N409b。入口を入金と同じ小窓にした 510 回目)。

手順: A 社で請求書を 1 枚作る (E-55 と同じ手順) → 入金の記録の画面の全体で 1,000 を記録する → 請求書の詳細の入金の枠の
「調整を記録する」を押す → 小窓の金額の欄を -660 に打ち直す → 押す → 調整の行の「取り消す」を押す → 小窓の「取り消す」を押す。
期待: 「調整を記録する」で小窓が開き (道は請求書の詳細のまま)、金額の欄には残をマイナスにした数が入っていて、保存のボタンは
「-¥<残> の調整を記録する」。-660 と打つと、ボタンは「-¥660 の調整を記録する」になる (マイナスは ¥ の前)。押すと小窓が閉じ、
右上に「…に調整 (振込手数料 -¥660) を記録しました。」が出て、入金の枠に調整の行が 1 つ増え、「調整」が -¥660 になる。
行の「取り消す」で小窓に黄の 1 行「…の調整 (…金額 -¥660) を取り消します。」が出て、押すと小窓が閉じ、行の状態が「取消」になり、
行の「取り消す」が無くなり、「調整」が ¥0 に戻る。

この試験が見る所: `#seizou-modal[open]` の数・道・小窓の `input[name=amount]` の値・保存のボタンの字・右上の済んだ知らせの字・
`#payments [data-adjustment-row]` の数と字・入金の枠の「調整」の字・`pageerror` の数。
見ない所 (理由): 前回の残に数えた調整の取消の黄の知らせ (R36。会社の設定を「載せる」に変える手順が要るので、
tests/test_billing_adjustment_views.py が応答の HTML で見る)、金額の閲覧の無い人 (同じ試験のファイルが見る)、電話の幅 (本体の手の確かめ)。
請求書を 1 つ・入金を 1 件・調整を 1 件 (取消) 増やす (E-55 と同じく A 社の DB に残る)。
"""

import re

from playwright.sync_api import expect

from .conftest import login, shot, unique_mark
from .test_e55_field_error import SUBMIT, new_invoice

ADJUST = '#payments a[data-modal-form*="/adjustments/new/"]'
ROWS = "#payments [data-adjustment-row]"
CANCEL = "form[data-enter-guard] button.btn-error[type=submit]"


def _summary(page, name: str) -> str:
    """入金の枠の 合計・入金済み・調整・残 のうち、名前が name の物の字。"""
    box = page.locator("#payments dl div").filter(has=page.locator("dt", has_text=re.compile(rf"^{name}$")))
    return box.locator("dd").inner_text().strip()


def test_e75_adjustment_is_recorded_and_cancelled_in_modal(page_a, company_a):
    errors = []
    page_a.on("pageerror", lambda error: errors.append(str(error)))
    login(page_a, company_a)
    invoice_url = new_invoice(page_a, company_a, unique_mark("e75"))

    # 入金を 1,000 だけ記録して、残を残す
    page_a.goto(f"{invoice_url}payments/new/")
    page_a.locator("main input[name=amount]").fill("1000")
    page_a.locator(SUBMIT).first.click()
    page_a.wait_for_url(invoice_url)
    balance = _summary(page_a, "残")
    assert re.fullmatch(r"¥[\d,]+", balance), balance

    # 調整の記録 (小窓)
    page_a.locator(ADJUST).click()
    modal = page_a.locator("#seizou-modal[open]")
    expect(modal).to_have_count(1)
    assert page_a.url == invoice_url
    box = page_a.locator("#seizou-modal-box")
    amount = box.locator("input[name=amount]")
    button = box.locator(SUBMIT).first
    expect(amount).to_have_value("-" + balance.removeprefix("¥").replace(",", ""))
    expect(button).to_have_text(re.compile(rf"^\s*-{re.escape(balance)} の調整を記録する\s*$"))

    amount.click()
    amount.select_text()
    amount.press_sequentially("-660")
    expect(button).to_have_text(re.compile(r"^\s*-¥660 の調整を記録する\s*$"))
    shot(page_a, "E-75", "modal-create")

    button.click()
    expect(modal).to_have_count(0)
    expect(page_a.locator(".seizou-toast[role=status]")).to_contain_text("に調整 (振込手数料 -¥660) を記録しました。")
    rows = page_a.locator(ROWS)
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("-¥660")
    expect(rows.first).to_contain_text("振込手数料")
    assert _summary(page_a, "調整") == "-¥660"
    assert page_a.url == invoice_url
    shot(page_a, "E-75", "recorded")

    # 調整の取消 (小窓)
    rows.first.locator("a[data-modal-form]").click()
    expect(modal).to_have_count(1)
    expect(box).to_contain_text("金額 -¥660) を取り消します。")
    shot(page_a, "E-75", "modal-cancel")

    box.locator(CANCEL).first.click()
    expect(modal).to_have_count(0)
    expect(rows).to_have_count(1)
    expect(rows.first.locator("a[data-modal-form]")).to_have_count(0)
    expect(rows.first).to_contain_text("取消")
    assert _summary(page_a, "調整") == "¥0"
    assert _summary(page_a, "残") == balance
    assert page_a.url == invoice_url
    assert errors == []
