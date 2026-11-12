"""E-56 止まった後に同じ画面の欄を直したら、止まった赤い知らせを閉じる (初見テスト R5 の V36。候補 C25 の 4。決め 11-06 21:4x C25-3 = b)。

手順: (1) A 社 (`over_shipment` = `stop`) の出荷の新規登録で受注 10 に数量 11 を入れて保存 → 数量に 10 と打つ → 保存。
(2) 入金の記録の画面の全体 (URL を直に開く) で金額を空のまま押す → 6 秒待つ → 金額に 8800 と打つ → 空に戻して押す → × で閉じる → 金額に 1 字打つ。
(3) 入金の記録の小窓で金額を空のまま押す → 金額に 8800 と打つ。
(4) B 社 (`over_shipment` = `warn`) の出荷の新規登録で受注 10 に数量 11 を入れて保存 → 数量に 10 と打つ。
期待: (1) 右上に止まった赤い知らせ (R01) が 1 つ出て、開いただけでは閉じない。数量を打った直後に閉じ (右上の枠も無くなる)、
数量の欄の下の「残り 10」は見えたまま、道は `/shipping/new/` のまま。保存すると移った先で緑の知らせが出る。
(2) 右上に「記録できません。」の赤い知らせと欄の下の誤りの字が出る。6 秒待っても残る (時間では閉じない)。打った直後に知らせが閉じ、
誤りの字も見えなくなる。× で閉じた後に欄を直しても、誤りは起きない (画面の JS の誤り 0)。
(3) 小窓の箱の中に止まった赤い帯が 1 つ出て、打った直後に消える。右上の知らせの枠は出ない。
(4) 確かめの黄の知らせと「このまま保存」は、数量を打っても 1 つのまま。

この試験が見る所: `#seizou-toasts [data-notice="stop"]`・`#seizou-toasts [data-notice="check"]`・`#seizou-toasts` の数、
`#seizou-modal-box [data-notice="stop"]` の数、`#id_amount_error` の見え方、`#id_qty_helptext` の字、道、`pageerror` の数。
見ない所 (理由): 電話の幅 (本体の手の確かめで 390×844 を撮る)、生産計画の盤とメモの書く枠 (入力の土台の form の外。C57-5 の依頼)。
受注を 3 つ (A 社に 2 つ・B 社に 1 つ)・A 社の出荷を 2 つ・納品書と請求書を 1 つずつ増やし、入金は増やさない (E-12 と E-29 と同じく 2 社の DB に残る)。
"""

from urllib.parse import urlparse

from playwright.sync_api import expect

from .conftest import fill_receiving, fill_shipment, login, new_order, seed_name, shot, unique_mark
from .test_e55_field_error import SUBMIT, new_invoice

CUSTOMER_CODE = "C001"
STOP = '#seizou-toasts [data-notice="stop"]'
CHECK = '#seizou-toasts [data-notice="check"]'
MODAL_STOP = '#seizou-modal-box [data-notice="stop"]'
WAIT_MS = 6_000  # 済んだ知らせが消える 5 秒より長く待つ


def _retype(field, text: str) -> None:
    """欄の字を選んでから打ち直す (1 字目で欄の input の event が出る)。"""
    field.click()
    field.select_text()
    field.press_sequentially(text)


def test_e56_shipment_stop_closes_when_qty_is_fixed(page_a, company_a):
    login(page_a, company_a)
    order_no = new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", CUSTOMER_CODE),
        part_no="WK-1001",
        qty=10,
        unit_price=480,
        mark=unique_mark("e56"),
    )

    fill_shipment(page_a, company_a, order_no=order_no, qty=11)
    stop = page_a.locator(STOP)
    expect(stop).to_have_count(1)
    expect(stop).to_contain_text("R01")
    page_a.wait_for_load_state("load")
    expect(stop).to_have_count(1)  # 開いただけでは閉じない (選ぶ欄の JS が作るときに change を出さない)
    shot(page_a, "E-56", "a-stopped")

    _retype(page_a.locator("input[name=qty]"), "10")
    expect(stop).to_have_count(0)
    expect(page_a.locator("#seizou-toasts")).to_have_count(0)
    expect(page_a.locator("#id_qty_helptext")).to_contain_text("残り 10")
    assert urlparse(page_a.url).path == "/shipping/new/"
    shot(page_a, "E-56", "a-fixed")

    page_a.locator(SUBMIT).first.click()
    expect(page_a.locator(".seizou-toast[role=status]")).to_be_visible()
    assert urlparse(page_a.url).path != "/shipping/new/"


def test_e56_payment_stop_closes_on_page_and_in_modal(page_a, company_a):
    errors = []
    page_a.on("pageerror", lambda error: errors.append(str(error)))
    login(page_a, company_a)
    invoice_url = new_invoice(page_a, company_a, unique_mark("e56p"))

    # 画面の全体
    page_a.goto(f"{invoice_url}payments/new/")
    amount = page_a.locator("main input[name=amount]")
    error = page_a.locator("#id_amount_error")
    stop = page_a.locator(STOP)
    page_a.locator(SUBMIT).first.click()
    expect(stop).to_have_count(1)
    expect(stop).to_contain_text("記録できません。")
    expect(error).to_be_visible()
    page_a.wait_for_timeout(WAIT_MS)
    expect(stop).to_have_count(1)  # 時間では閉じない
    shot(page_a, "E-56", "page-stopped")

    amount.click()
    amount.press_sequentially("8800")
    expect(stop).to_have_count(0)
    expect(error).to_be_hidden()
    shot(page_a, "E-56", "page-fixed")

    amount.fill("")
    page_a.locator(SUBMIT).first.click()
    expect(stop).to_have_count(1)
    stop.locator("[data-toast-close]").click()
    expect(stop).to_have_count(0)
    amount.click()
    amount.press_sequentially("1")
    expect(stop).to_have_count(0)
    assert errors == []

    # 小窓
    page_a.goto(invoice_url)
    # 入金の枠の小窓のボタンは「入金を記録する」と「調整を記録する」の 2 つ (510 回目から)。入金の方を URL で選ぶ
    page_a.locator('#payments a[data-modal-form*="/payments/new/"]').click()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    box = page_a.locator("#seizou-modal-box")
    band = page_a.locator(MODAL_STOP)
    box.locator(SUBMIT).first.click()
    expect(band).to_have_count(1)
    expect(band).to_contain_text("記録できません。")
    shot(page_a, "E-56", "modal-stopped")

    box.locator("input[name=amount]").click()
    box.locator("input[name=amount]").press_sequentially("8800")
    expect(band).to_have_count(0)
    expect(page_a.locator("#seizou-toasts")).to_have_count(0)
    assert errors == []


def test_e56_warning_stays_when_qty_is_fixed(page_b, company_b):
    login(page_b, company_b)
    order_no = new_order(
        page_b,
        company_b,
        customer=seed_name(company_b, "partners", CUSTOMER_CODE),
        part_no="SB-2001",
        qty=10,
        unit_price=45,
        mark=unique_mark("e56b"),
    )
    # B 社は支給品・預かり品の入荷が無い明細の出荷を止める (受入の決まり = stop) ので、先に入荷を記録する
    fill_receiving(page_b, company_b, order_no=order_no, part_no="SB-2001", qty=10)

    fill_shipment(page_b, company_b, order_no=order_no, qty=11)
    warning = page_b.locator(CHECK)
    expect(warning).to_have_count(1)
    expect(page_b.get_by_role("button", name="このまま保存")).to_have_count(1)

    _retype(page_b.locator("input[name=qty]"), "10")
    page_b.wait_for_timeout(WAIT_MS)
    expect(warning).to_have_count(1)
    expect(page_b.get_by_role("button", name="このまま保存")).to_have_count(1)
    shot(page_b, "E-56", "b-warn-stays")
