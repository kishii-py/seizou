"""E-45 小窓の箱の幅と上の隙間 (152 回目。11-04 の回帰の守り)。

手順: 幅 1280 の画面で、部品マスターの一覧の品名の字から部品の小窓 (見るだけ) を開く → 閉じる → 注文残の盤の行の ⋮ の「出荷する」で入力の小窓を開く
→ 画面の高さを低くして、入力の小窓の上と下を見る。
期待: 見るだけの小窓は前の幅 (32rem) より広い。入力の小窓はそれより広い。小窓の箱は画面の上に貼り付かず、上と下に隙間がある。

この試験が見る所: `#seizou-modal-box` の箱の大きさ (`bounding_box`)。部品の小窓の幅が 600px より広い (48rem = 768px の見込み)。
出荷の入力の小窓の幅が部品の小窓より広い (64rem = 1024px の見込み)。画面の高さを 480px にした後の入力の小窓の上が 16px 以上で、
下の端が画面の下から 16px 以上の上にある (上下に 2rem の隙間の見込み)。
見ない所 (理由): 少ない中身の小窓 (28rem。印 `.seizou-modal-narrow` を付けた画面がまだ無い)、幅 390 の画面 (画面の幅から 2rem を引いた幅。精査が見る)、
小窓の中の巻き (中身の量で変わる)。
"""

from playwright.sync_api import expect

from .conftest import login, new_order, seed_name, shot, unique_mark

PART_NO = "WK-1001"
NARROW_OLD_PX = 600  # 前の箱の幅 (max-w-lg = 32rem = 512px) より広く、既定の 48rem (768px) より狭い線
GAP_PX = 16
LOW_HEIGHT = 480
# 押す前に ⋮ を見える所へ送り、2 つ先の描画まで待つ (盤の表は画面より広い。E-41 と同じ)。
NEXT_FRAMES = "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"


def _open_box(page):
    expect(page.locator("#seizou-modal[open]")).to_have_count(1)
    box = page.locator("#seizou-modal-box")
    expect(box).to_have_css("opacity", "1")
    return box


def test_e45_modal_box_width_and_gap(page_a, company_a):
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/masters/items/")
    page_a.fill("#list-q", PART_NO)
    expect(page_a.locator("#rows tbody tr")).to_have_count(1)
    page_a.locator("#rows tbody tr").first.locator("a.master-link").first.click()
    view_width = _open_box(page_a).bounding_box()["width"]
    assert view_width > NARROW_OLD_PX, f"見るだけの小窓の幅が {view_width}px (前の 32rem のまま)"
    shot(page_a, "E-45", "a-view-modal")
    page_a.keyboard.press("Escape")
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(0)

    mark = unique_mark("e45")
    new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", "C001"),
        part_no=PART_NO,
        qty=10,
        unit_price=480,
        mark=mark,
    )
    page_a.goto(f"{company_a.base_url}/orders/lines/?q={mark}")
    row = page_a.locator("#rows tbody tr[data-row-menu]").first
    menu = row.locator('summary[aria-label="行の操作"]')
    menu.scroll_into_view_if_needed()
    page_a.evaluate(NEXT_FRAMES)
    menu.click()
    row.get_by_role("link", name="出荷する").click()
    box = _open_box(page_a)
    form_width = box.bounding_box()["width"]
    assert form_width > view_width, f"入力の小窓 ({form_width}px) が見るだけの小窓 ({view_width}px) より広くない"
    shot(page_a, "E-45", "a-form-modal")

    page_a.set_viewport_size({"width": 1280, "height": LOW_HEIGHT})
    page_a.evaluate(NEXT_FRAMES)
    low = box.bounding_box()
    assert low["y"] >= GAP_PX, f"入力の小窓の上が画面の上から {low['y']}px (上に貼り付いている)"
    assert low["y"] + low["height"] <= LOW_HEIGHT - GAP_PX, "入力の小窓の下の端が画面の下に付いている"
    shot(page_a, "E-45", "a-form-modal-low")
