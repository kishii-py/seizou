"""E-53 発注の入力の明細の部品の欄 (字を打って探す欄。単位だけを写し、単価は写さない。初見テスト R5 の V50・V140。候補 C01-先 の 5)。

手順: A 社で発注の新規登録を開く → 1 行目の部品の欄に「ロッドカバー」を打つ → WK-1010 を押す
→ 「行を足す」で足した 2 行目の部品の欄で WK-1011 を選ぶ。保存しない (発注を増やさない)。
期待: 「ロッドカバー」を打つと候補が WK-1010 と WK-1011 の 2 件に絞られる。WK-1010 を押すと単位の欄が「個」になり、
単価の欄は空のまま、欄の下に「単位を部品から写しました。」が出る。足した 2 行目でも選べて、2 行目の単位が「個」になる。

この試験が見る所: 1 行目の一覧 (`id_lines-0-item-ts-dropdown`) の `.option` が q=ロッドカバー の返事の後に 2 件で、WK-1010 と WK-1011 が 1 件ずつ。
押した後の `input[name=lines-0-unit]` が「個」、`input[name=lines-0-unit_price]` が空、部品の欄の枡の `p[role=status]` の字。
`[data-add-line]` で足した行の `select[name=lines-1-item]` が `choose` で選べ、`input[name=lines-1-unit]` が「個」。
見ない所 (理由): 取引先の欄 (E-15・E-16・E-40 が道具の `send_purchase_order` で選ぶ)、保存と送る (E-16・E-40 が見る。この試験は発注を増やさない)、
2 段の候補と最近使った (E-52 が受注の部品の欄で見る。同じ JS と口の土台)、B 社 (発注の入力のコードは 2 社で同じ)、
生産管理ボードの「発注する」の小窓の中の候補の一覧 (小窓の中では一覧を欄の包みの中に描く。本体が画面の手で見る)。
A 社の品名に「ロッドカバー」が入るのは WK-1010 と WK-1011 の 2 つだけ (examples/companies/a_machining/items.csv)。
A 社の DB に「ロッドカバー」を品名に含む部品が足されていると、候補が 2 件にならず落ちる。
"""

from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect

from .conftest import SELECT_WAIT_MS, choose, login, shot

WORD = "ロッドカバー"
FIRST = "WK-1010"
SECOND = "WK-1011"
COPIED = "単位を部品から写しました。"


def _asked(response, expected: str) -> bool:
    """候補の口 (道が /select/ で終わる) への q=expected の返事か (conftest の choose と同じ見方)。"""
    url = urlparse(response.url)
    if not url.path.endswith("/select/"):
        return False
    return parse_qs(url.query, keep_blank_values=True).get("q") == [expected]


def test_e53_purchase_item_select_copies_unit_only(page_a, company_a):
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/purchasing/orders/new/")

    select = page_a.locator("select[name='lines-0-item']")
    wrapper = select.locator("xpath=following-sibling::div[contains(@class,'ts-wrapper')][1]")
    options = page_a.locator(f"[id='{select.get_attribute('id')}-ts-dropdown'] .option")
    with page_a.expect_response(lambda response: _asked(response, ""), timeout=SELECT_WAIT_MS):
        wrapper.locator(".ts-control").click()
    options.first.wait_for(state="visible", timeout=SELECT_WAIT_MS)
    with page_a.expect_response(lambda response: _asked(response, WORD), timeout=SELECT_WAIT_MS):
        wrapper.locator(".ts-control input").press_sequentially(WORD)
    expect(options).to_have_count(2, timeout=SELECT_WAIT_MS)
    expect(options.filter(has_text=FIRST)).to_have_count(1)
    expect(options.filter(has_text=SECOND)).to_have_count(1)

    options.filter(has_text=FIRST).first.click()
    expect(select).not_to_have_value("", timeout=SELECT_WAIT_MS)
    expect(page_a.locator("input[name=lines-0-unit]")).to_have_value("個")
    expect(page_a.locator("input[name=lines-0-unit_price]")).to_have_value("")
    expect(select.locator("xpath=..").locator("p[role=status]")).to_have_text(COPIED)

    page_a.locator("[data-add-line]").click()
    choose(page_a, "lines-1-item", SECOND)
    expect(page_a.locator("input[name=lines-1-unit]")).to_have_value("個")
    expect(page_a.locator("input[name=lines-1-unit_price]")).to_have_value("")
    shot(page_a, "E-53", "a-po-item-select")
