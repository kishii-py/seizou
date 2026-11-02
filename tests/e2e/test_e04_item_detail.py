"""E-04 部品マスターの詳細 (仕様書の 6 番の 3 節)。

手順: 一覧の行から詳細を開く。
期待: 項目の全部、図面の絵 (TIFF でも出る)、得意先ごとの品番の表、受注と出荷のタブ。

この試験が見る所 (1 本目): A 社の部品マスターの一覧の絞り込み (`#list-q`) に `WK-1001` を入れ、行の品番の字から詳細 (道が `/masters/items/<pk>/`) を開く。
項目の見出しに 自社品番・品名・図面番号・図面ファイル・単位・標準単価・備考 と、A 社の会社ごとの項目 (材質・図面の版) がある。
タブ「取引先ごとの品番」の件数が 1 以上で、種の `partner_items.csv` の `WK-1001` の行の取引先の品番がある。
(2 本目): 作り物の TIFF (LZW 圧縮) を付けた印の品番の部品を作ると、詳細の「ファイル」の節に表示用の PNG の絵 (`img` の `src` が `.png`) があり、
絵が読めた (`complete` かつ `naturalWidth` > 0)。黄色の 1 行 (`.alert-warning`) が無い。図面は種の部品に付けず、印の品番の部品に付ける。
(3 本目): 詳細のタブに「受注」と「出荷」がある。今の画面には無いので、理由つきの「失敗する前提」の印 (strict) にした。
見ない所 (理由): 項目の値 (見出しだけを見た)、受注と出荷のタブの中身 (タブが無い)。
"""

import csv
import re

import pytest
from playwright.sync_api import expect

from .conftest import COMPANIES_DIR, drawing_file, login, new_item, shot, unique_mark, wait_image_loaded

PART_NO = "WK-1001"
FIELD_HEADINGS = ("自社品番", "品名", "図面番号", "図面ファイル", "単位", "標準単価", "備考", "材質", "図面の版")


def _open_seed_detail(page, company) -> None:
    page.goto(f"{company.base_url}/masters/items/")
    page.fill("#list-q", PART_NO)
    row = page.locator("#rows tbody tr")
    expect(row).to_have_count(1)
    row.first.locator("td").first.locator("a").click()
    page.wait_for_url(re.compile(r"/masters/items/\d+/$"))


def _partner_part_no(company_id: str) -> str:
    with (COMPANIES_DIR / company_id / "partner_items.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["自社品番"] == PART_NO:
                return next(value for key, value in row.items() if key.endswith("品番") and key != "自社品番")
    raise LookupError(f"種の partner_items.csv に {PART_NO} が無い")


def test_e04_fields_and_partner_items(page_a, company_a):
    login(page_a, company_a)
    _open_seed_detail(page_a, company_a)
    labels = [text.strip() for text in page_a.locator("dl dt").all_inner_texts()]
    for heading in FIELD_HEADINGS:
        assert any(label.startswith(heading) for label in labels), heading
    tab = page_a.locator("label.tab").filter(has_text="取引先ごとの品番")
    expect(tab).to_have_count(1)
    assert int(tab.locator(".badge").inner_text().replace(",", "")) >= 1
    panels = " ".join(page_a.locator(".tab-content").evaluate_all("els => els.map(e => e.textContent)"))
    assert _partner_part_no(company_a.id) in panels
    shot(page_a, "E-04", "a-detail")


def test_e04_tiff_drawing_in_detail(page_a, company_a, tmp_path):
    login(page_a, company_a)
    new_item(
        page_a,
        company_a,
        part_no=unique_mark("e04"),
        name="E2E 試験の部品 (TIFF)",
        drawing=drawing_file(tmp_path, "tiff"),
    )
    image = page_a.locator("a[target=_blank] img[src$='.png']")
    expect(image).to_have_count(1)
    wait_image_loaded(page_a, image)
    expect(page_a.locator(".alert-warning")).to_have_count(0)
    shot(page_a, "E-04", "a-tiff")


@pytest.mark.xfail(reason="06 の E-04 の「受注と出荷のタブ」が今の画面で通らない: 部品の詳細のタブは「取引先ごとの品番」「標準の工程」だけ", strict=True, raises=AssertionError)
def test_e04_order_and_shipment_tabs(page_a, company_a):
    login(page_a, company_a)
    _open_seed_detail(page_a, company_a)
    tabs = [text.strip() for text in page_a.locator("label.tab").all_inner_texts()]
    assert any(text.startswith("受注") for text in tabs)
    assert any(text.startswith("出荷") for text in tabs)
