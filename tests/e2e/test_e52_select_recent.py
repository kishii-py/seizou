"""E-52 受注の入力の部品の欄で、最近使った・2 段の候補・0 件の登録の行を見る (N162。決め 11-05 16:2x)。

手順: A 社で受注を 1 件入れる (今日の受注で WK-1001 を最近使った物にする) → 受注の新規登録の明細の部品の欄を押す →
「WK-100」を打つ → 入れ口を空にして、どの部品にも合わない印を打つ → 「部品マスターに登録する」を押す → Escape で小窓を閉じる。
期待: 字を打つ前は「最近使った」の見出しの下に最近使った部品が 5 件まで出て、候補は上の段に品番、下の段に品名などの 2 段で出る。
字を打つと見出しが消え、打った字に合う所に印が付く。0 件では「該当なし」の下に登録の行が出て、押すと画面を移らずに部品の新規登録の小窓が開く。

この試験が見る所: 欄に着いた時の空の検索の後、一覧 (`id_lines-0-item-ts-dropdown`) の `[data-group='recent']` の `.optgroup-header` の字が
「最近使った」で、その群の `.option` が 1〜5 件。その中の WK-1001 の候補の `[data-select-title]` に品番があり、`[data-select-sub]` が見えて字が空でない。
「WK-100」を打つと `[data-group='recent']` が 0 になり、WK-1001 の候補の `.highlight` の字が「WK-100」。合わない印を打つと
`[data-select-create]` の字が「部品マスターに登録する」で、`data-modal-form` が `/masters/items/new/` で終わる。押すと `#seizou-modal` に
`open` が付き、`#seizou-modal-box` の中に `form[hx-post]` が見え、URL は `/orders/new/` のまま。
見ない所 (理由): 得意先の欄 (同じ JS と口)、B 社 (同じ)、書けない人 (tests/test_core_select_options.py の権限の試験)、
登録の保存 (部品マスターに部品を増やさない)、入力の小窓の中の欄の新しいタブ (今の作品の入力の小窓に部品の欄が無い見込み)、一覧の位置 (E-06)。
最近使ったは日付の新しい順なので、A 社の DB に今日より後の日付の受注が 5 件より多いと WK-1001 が群に入らない (そのときは落ちる)。
受注を 1 件増やす (E-41・E-51 と同じく A 社の DB に残る)。
"""

from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect

from .conftest import SELECT_WAIT_MS, login, new_order, seed_name, shot, unique_mark

CUSTOMER_CODE = "C001"
PART_NO = "WK-1001"
TYPED = "WK-100"
FIELD = "lines-0-item"


def _asked(response, expected: str) -> bool:
    url = urlparse(response.url)
    if not url.path.endswith("/select/"):
        return False
    return parse_qs(url.query, keep_blank_values=True).get("q") == [expected]


def _open_part_field(page):
    """明細の部品の欄を押し、欄に着いた時の空の検索の返事と候補の表示を待つ。候補の locator を返す。"""
    select = page.locator(f"select[name='{FIELD}']")
    wrapper = select.locator("xpath=following-sibling::div[contains(@class,'ts-wrapper')][1]")
    wrapper.evaluate("element => element.scrollIntoView({block: 'center'})")
    options = page.locator(f"[id='{select.get_attribute('id')}-ts-dropdown'] .option")
    with page.expect_response(lambda response: _asked(response, ""), timeout=SELECT_WAIT_MS):
        wrapper.locator(".ts-control").click()
    options.first.wait_for(state="visible", timeout=SELECT_WAIT_MS)
    return options


def test_e52_recent_two_lines_and_create_row(page_a, company_a):
    login(page_a, company_a)
    mark = unique_mark("e52")
    new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", CUSTOMER_CODE),
        part_no=PART_NO,
        qty=1,
        unit_price=480,
        mark=mark,
    )

    # 1. 字を打つ前: 「最近使った」の見出しの下に、最近使った部品が 2 段で出る
    page_a.goto(f"{company_a.base_url}/orders/new/")
    _open_part_field(page_a)
    dropdown = page_a.locator(f"[id='id_{FIELD}-ts-dropdown']")
    expect(dropdown.locator("[data-group='recent'] .optgroup-header")).to_have_text("最近使った", timeout=SELECT_WAIT_MS)
    recent = dropdown.locator("[data-group='recent'] .option")
    assert 1 <= recent.count() <= 5
    used = recent.filter(has_text=PART_NO).first
    expect(used).to_be_visible()
    expect(used.locator("[data-select-title]")).to_contain_text(PART_NO)
    sub = used.locator("[data-select-sub]")
    expect(sub).to_be_visible()
    assert sub.inner_text().strip()
    shot(page_a, "E-52", "a-recent")

    # 2. 字を打つと見出しが消え、打った字に合う所に印が付く
    control = page_a.locator(f"select[name='{FIELD}']").locator(
        "xpath=following-sibling::div[contains(@class,'ts-wrapper')][1]//input"
    )
    with page_a.expect_response(lambda response: _asked(response, TYPED), timeout=SELECT_WAIT_MS):
        control.press_sequentially(TYPED)
    typed = dropdown.locator(".option").filter(has_text=PART_NO).first
    typed.wait_for(state="visible", timeout=SELECT_WAIT_MS)
    expect(dropdown.locator("[data-group='recent']")).to_have_count(0)
    expect(typed.locator(".highlight").first).to_have_text(TYPED)
    shot(page_a, "E-52", "a-typed")

    # 3. 0 件: 「該当なし」の下の登録の行を押すと、画面を移らずに部品の新規登録の小窓が開く (保存しない)
    control.fill("")
    with page_a.expect_response(lambda response: _asked(response, mark), timeout=SELECT_WAIT_MS):
        control.press_sequentially(mark)
    create = dropdown.locator("[data-select-create]")
    expect(create).to_be_visible(timeout=SELECT_WAIT_MS)
    expect(create).to_have_text("部品マスターに登録する")
    assert (create.get_attribute("data-modal-form") or "").endswith("/masters/items/new/")
    create.click()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1, timeout=SELECT_WAIT_MS)
    expect(page_a.locator("#seizou-modal-box form[hx-post]")).to_be_visible(timeout=SELECT_WAIT_MS)
    assert urlparse(page_a.url).path.endswith("/orders/new/")
    shot(page_a, "E-52", "a-create")
    page_a.keyboard.press("Escape")
