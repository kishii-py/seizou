"""E-50 受注の入力の下の行と書きかけの字 (C21。決め C21-1 = e。前の形は N154 の保存の帯)。

手順: A 社で受注の新規登録 (`/orders/new/`) を幅 1280・高さ 720 の窓で開く → 送らずに下の保存のボタンを見る → 得意先を選ぶ
→ 1 行目の部品を選び、数量 10・単価 480 を入れる → 同じ URL を開き直し (離れる確認が出れば OK)、数量だけ 3 を入れて
(得意先なし) 下の保存のボタンを押す。受注を増やさない (誤りで止まる形で下の保存のボタンを見る)。
期待: 開いた直後から保存のボタンが窓の中にあり (下の行が窓の下の端に付く)、書きかけの字はまだ出ない。得意先を選んでも
得意先の欄は動かず、下の行の左に「保存していない変更があります」が出る。保存のボタンの字は打つたびに変わり、
左のメニューの「AI に質問」の入口と重ならない。下の保存のボタンで誤りの画面に止まり、字は隠れる。

この試験が見る所: 開いた直後に `[data-save-bar]` が見えず、`form[data-enter-guard] button.btn-primary[type=submit]` の
`bounding_box()` が送らずに窓 (高さ 720) の中にあり、その真ん中の `elementFromPoint` がそのボタン。得意先を選ぶ前と後で
得意先の欄 (字で探す欄の包み) の文書の上の端が同じ (前の帯の形では 49 下がった) で、選んだ後に `[data-save-bar]` が見える。
数量と単価の後のボタンの字に「4,800」がある。保存のボタンの箱と `#assistant-toggle` の箱が重ならない。数量だけの保存の後に
`#id_customer_error` が見え、URL が `/orders/new/` のままで、`[data-save-bar]` が見えない。ボタンは字で探さず、入力の form の主色の submit で引く。
見ない所 (理由): 発注・一括出荷・説明書の下の行 (同じ形。本体の手の確かめ)、小窓の入力と黄の知らせの後 (試験 test_core_save_bar が HTML を見る)、
幅 390 の見た目と Tab の送り (本体の手の確かめ)、下の保存のボタンで保存まで進む形 (E-08 が見る)、離れる確認の字 (ブラウザが決める)。
下の行の貼り付けは scripts/css/input.css の決まりなので、app.css を作り直すまでは窓の中の見方が落ちる。
"""

from urllib.parse import urlparse

from playwright.sync_api import expect

from .conftest import choose, login, seed_name, shot

VIEWPORT = {"width": 1280, "height": 720}
CUSTOMER_CODE = "C001"
PART_NO = "WK-1001"
SUBMIT = "form[data-enter-guard] button.btn-primary[type=submit]"
BAR = "[data-save-bar]"


def _overlaps(first: dict, second: dict) -> bool:
    return (
        first["x"] < second["x"] + second["width"]
        and second["x"] < first["x"] + first["width"]
        and first["y"] < second["y"] + second["height"]
        and second["y"] < first["y"] + first["height"]
    )


def _page_top(locator) -> float:
    """文書の上からの上の端 (送った量を足す。選ぶときに窓が送られても、欄そのものが動いたかだけを比べる)。"""
    return locator.evaluate("(element) => element.getBoundingClientRect().top + window.scrollY")


def _save_in_window(page) -> None:
    """保存のボタンが送らずに窓の中にあり、真ん中の点の一番上がそのボタン (下の行が窓の下の端に付いている)。"""
    box = page.locator(SUBMIT).first.bounding_box()
    assert box is not None
    assert 0 <= box["y"] and box["y"] + box["height"] <= VIEWPORT["height"], box
    on_top = page.evaluate(
        """([x, y]) => {
            const element = document.elementFromPoint(x, y);
            const button = element && element.closest("button");
            return !!button && button.type === "submit" && button.classList.contains("btn-primary");
        }""",
        [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
    )
    assert on_top, box


def test_e50_order_save_bar(page_a, company_a):
    login(page_a, company_a)
    page_a.set_viewport_size(VIEWPORT)
    path = "/orders/new/"
    page_a.goto(f"{company_a.base_url}{path}")
    bar = page_a.locator(BAR)
    expect(bar).to_be_hidden()
    _save_in_window(page_a)

    customer = page_a.locator("select[name='customer']").locator("xpath=following-sibling::div[contains(@class,'ts-wrapper')][1]")
    before = _page_top(customer)
    choose(page_a, "customer", seed_name(company_a, "partners", CUSTOMER_CODE))
    expect(bar).to_be_visible()
    assert _page_top(customer) == before
    _save_in_window(page_a)

    choose(page_a, "lines-0-item", PART_NO)
    page_a.fill("input[name=lines-0-qty]", "10")
    page_a.fill("input[name=lines-0-unit_price]", "480")
    expect(page_a.locator(SUBMIT).first).to_contain_text("4,800")
    expect(bar).to_be_visible()
    _save_in_window(page_a)
    toggle_box = page_a.locator("#assistant-toggle").bounding_box()
    save_box = page_a.locator(SUBMIT).first.bounding_box()
    assert toggle_box is not None and save_box is not None
    assert not _overlaps(toggle_box, save_box), (toggle_box, save_box)

    # 書きかけのまま開き直す。離れる確認 (beforeunload) が出れば OK で進む (確認の有無と字はブラウザが決めるので見ない)
    page_a.on("dialog", lambda dialog: dialog.accept())
    page_a.goto(f"{company_a.base_url}{path}")
    expect(page_a.locator("input[name=lines-0-qty]")).to_have_value("")
    expect(bar).to_be_hidden()

    page_a.fill("input[name=lines-0-qty]", "3")
    expect(bar).to_be_visible()
    page_a.locator(SUBMIT).first.click()
    expect(page_a.locator("#id_customer_error")).to_be_visible()
    assert urlparse(page_a.url).path == path
    expect(bar).to_be_hidden()
    shot(page_a, "E-50", "a-order-save-bar")
