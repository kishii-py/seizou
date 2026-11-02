"""E-24 図面 (仕様書の 6 番の 3 節)。

手順: 作り物の TIFF (G4 圧縮を含む) を品目に付ける → 壊れたファイル → ログアウトして図面の URL を開く。
期待: 詳細と小窓に絵。壊れた物は知らせが出て品目は保存される。ログインなしでは開けない。

この試験が見る所 (1 本目): G4 圧縮の白黒の TIFF を付けた印の品番の部品を作ると、詳細に絵 (表示用の PNG) があり読めて、黄色の 1 行 (`.alert-warning`) が無い。
一覧の絞り込みに印の品番を入れて品名の字 (小窓の link) を押すと、小窓 (`#seizou-modal` に `open`) に `img[alt=図面]` があり絵が読めた。
(2 本目): 壊れた TIFF (正しい TIFF の頭の数十 bytes だけ) を付けても部品は保存されて詳細へ移り、詳細と小窓に黄色の 1 行があり、字に
「図面の表示用の絵は作れませんでした」がある (N23)。
(3 本目): 詳細の元のファイルの link の URL を、ログインしたまま開くと 200。ログアウトの後に同じ URL を開いても 200 でない。今の画面では
図面の URL は開発のサーバーの media の道で、ログインを見ないので、理由つきの「失敗する前提」の印 (strict) にした (N23 の要望)。
ログインしたままで 200 でなければ、前提の崩れとして `RuntimeError` で落とす (試験の誤りを失敗する前提の印に隠さない)。
見ない所 (理由): 絵の中身 (画素。読めたことだけを見た)、PNG の図面と PDF の図面、差し替えと外した古いファイルの掃除。
"""

import re
from urllib.parse import urljoin

import pytest
from playwright.sync_api import expect

from .conftest import drawing_file, login, new_item, shot, unique_mark, wait_image_loaded

BROKEN_NOTICE = "図面の表示用の絵は作れませんでした"


def _open_modal(page, company, part_no: str):
    page.goto(f"{company.base_url}/masters/items/")
    page.fill("#list-q", part_no)
    expect(page.locator("#rows tbody tr")).to_have_count(1)
    page.locator("#rows tbody tr").first.locator("a.master-link").first.click()
    expect(page.locator("#seizou-modal[open]")).to_have_count(1)
    modal = page.locator("#seizou-modal")
    expect(modal.locator(".modal-box")).to_have_css("opacity", "1")
    return modal


def test_e24_g4_tiff_in_detail_and_modal(page_a, company_a, tmp_path):
    login(page_a, company_a)
    part_no = unique_mark("e24g")
    new_item(page_a, company_a, part_no=part_no, name="E2E 試験の部品 (G4)", drawing=drawing_file(tmp_path, "g4"))
    image = page_a.locator("a[target=_blank] img[src$='.png']")
    expect(image).to_have_count(1)
    wait_image_loaded(page_a, image)
    expect(page_a.locator(".alert-warning")).to_have_count(0)
    shot(page_a, "E-24", "a-g4-detail")

    modal = _open_modal(page_a, company_a, part_no)
    picture = modal.locator("img[alt=図面]")
    expect(picture).to_have_count(1)
    wait_image_loaded(page_a, picture)
    shot(page_a, "E-24", "a-g4-modal")


def test_e24_broken_file_notice_and_saved(page_a, company_a, tmp_path):
    login(page_a, company_a)
    part_no = unique_mark("e24b")
    new_item(
        page_a, company_a, part_no=part_no, name="E2E 試験の部品 (壊れた図面)", drawing=drawing_file(tmp_path, "broken")
    )
    notice = page_a.locator(".alert-warning")
    expect(notice).to_have_count(1)
    expect(notice).to_contain_text(BROKEN_NOTICE)

    modal = _open_modal(page_a, company_a, part_no)
    expect(modal.locator(".alert-warning")).to_contain_text(BROKEN_NOTICE)
    shot(page_a, "E-24", "a-broken")


@pytest.mark.xfail(reason="06 の E-24 の「ログインなしでは開けない」が今の画面で通らない: 図面の URL は開発のサーバーの media の道で、ログインを見ない (N23 の要望)", strict=True, raises=AssertionError)
def test_e24_drawing_url_needs_login(context, page_a, company_a, tmp_path):
    login(page_a, company_a)
    new_item(
        page_a, company_a, part_no=unique_mark("e24u"), name="E2E 試験の部品 (URL)", drawing=drawing_file(tmp_path, "tiff")
    )
    link = page_a.locator("a[href$='.tif']").first
    url = urljoin(company_a.base_url, link.get_attribute("href"))
    if context.request.get(url).status != 200:
        raise RuntimeError("前提が崩れている: ログインしたままなのに図面の URL が 200 で開かない")

    page_a.locator("summary[aria-label=利用者のメニュー]").click()
    page_a.locator("header details").get_by_role("button", name="ログアウト").click()
    page_a.wait_for_url(re.compile(r"/login/"))
    assert context.request.get(url, max_redirects=0).status != 200
