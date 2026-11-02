"""E-12 出荷の入力 (仕様書の 6 番の 3 節)。

手順: `stop` の会社 (A 社) で受注 10 に出荷 11 → 出荷 4 と 6 (分納) → `warn` の会社で 11 → 「このまま保存」→ 分納を許さない会社で残より少ない数。
期待: A 社の 11 は赤の知らせで止まる。4 と 6 は通る。`warn` の会社は黄の知らせと「このまま保存」が出て、押すと保存され残が -1 で出る。
分納を許さない会社は残の数しか入らない (決め 11-02 13:0x。理由の欄は無い)。

この試験が見る所: A 社 (`over_shipment` = `stop`) で数量 10 の受注を入れ、左のメニューの「出荷」→「新規登録」で、その受注の明細に
数量 11 を入れて「保存」すると、赤の知らせ (`alert-error` と `R01`) が出て道が `/shipping/new/` のまま。数量 4 なら保存できて、
受注の詳細の明細の表の「残」が 6 になる。
見ない所 (理由): 6 の 2 回目と、`warn` の会社の黄の知らせと「このまま保存」・残 -1、分納を許さない会社 (余力の試験。今夜の範囲の外)。
"""

import re
from urllib.parse import urlparse

from playwright.sync_api import expect

from .conftest import fill_shipment, login, new_order, seed_name, shot, unique_mark

CUSTOMER_CODE = "C001"


def test_e12_stop_then_partial(page_a, company_a):
    login(page_a, company_a)
    order_no = new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", CUSTOMER_CODE),
        part_no="WK-1001",
        qty=10,
        unit_price=480,
        mark=unique_mark("e12"),
    )
    order_url = page_a.url

    page_a.locator("aside nav").get_by_role("link", name="出荷", exact=True).click()
    page_a.wait_for_url(re.compile(r"/shipping/$"))
    page_a.get_by_role("link", name="新規登録").first.click()
    page_a.wait_for_url(re.compile(r"/shipping/new/$"))

    fill_shipment(page_a, company_a, order_no=order_no, qty=11)
    alert = page_a.locator(".alert-error")
    expect(alert).to_have_count(1)
    expect(alert).to_contain_text("R01")
    assert urlparse(page_a.url).path == "/shipping/new/"
    shot(page_a, "E-12", "a-stop")

    fill_shipment(page_a, company_a, order_no=order_no, qty=4)
    expect(page_a.locator(".seizou-toast[role=status]")).to_be_visible()
    assert urlparse(page_a.url).path != "/shipping/new/"

    page_a.goto(order_url)
    remaining = page_a.locator("#order-lines-title ~ div tbody tr").first.locator("td").last
    expect(remaining).to_have_text(re.compile(r"^6(\.0+)?$"))
    shot(page_a, "E-12", "a-remaining")



def test_e12_partial_second_and_warn(page_a, page_b, company_a, company_b):
    """E-12 の続き (余力)。A 社の分納 (4 の次の 6 が通り残 0) と、`warn` の B 社の「このまま保存」(11 で残 -1)。

    見ない所 (理由): 分納を許さない会社 (答えの YAML にその設定を持つ会社が 2 社の中に無い)。
    """
    login(page_a, company_a)
    order_no = new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", CUSTOMER_CODE),
        part_no="WK-1001",
        qty=10,
        unit_price=480,
        mark=unique_mark("e12a"),
    )
    order_url = page_a.url
    for qty in (4, 6):
        fill_shipment(page_a, company_a, order_no=order_no, qty=qty)
        expect(page_a.locator(".seizou-toast[role=status]")).to_be_visible()
        page_a.goto(f"{company_a.base_url}/shipping/new/")
    page_a.goto(order_url)
    remaining = page_a.locator("#order-lines-title ~ div tbody tr").first.locator("td").last
    expect(remaining).to_have_text(re.compile(r"^0(\.0+)?$"))

    login(page_b, company_b)
    order_no_b = new_order(
        page_b,
        company_b,
        customer=seed_name(company_b, "partners", CUSTOMER_CODE),
        part_no="SB-2001",
        qty=10,
        unit_price=45,
        mark=unique_mark("e12b"),
    )
    order_url_b = page_b.url
    fill_shipment(page_b, company_b, order_no=order_no_b, qty=11)
    warning = page_b.locator(".alert-warning")
    expect(warning).to_have_count(1)
    shot(page_b, "E-12", "b-warn")
    page_b.get_by_role("button", name="このまま保存").click()
    expect(page_b.locator(".seizou-toast[role=status]")).to_be_visible()
    page_b.goto(order_url_b)
    remaining_b = page_b.locator("#order-lines-title ~ div tbody tr").first.locator("td").last
    expect(remaining_b).to_have_text(re.compile(r"^-1(\.0+)?$"))
