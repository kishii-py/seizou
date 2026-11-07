"""E-50 受注の入力の下の行と書きかけの字 (C21。決め C21-1 = e。前の形は N154 の保存の帯) と、下の帯の形 (C113。決め UX-SAVEBAR-1 = c)。

手順: A 社で受注の新規登録 (`/orders/new/`) を幅 1280・高さ 720 の窓で開く → 送らずに下の保存のボタンを見る → 得意先を選ぶ
→ 1 行目の部品を選び、数量 10・単価 480 を入れる → 同じ URL を開き直し (離れる確認が出れば OK)、数量だけ 3 を入れて
(得意先なし) 下の保存のボタンを押す。受注を増やさない (誤りで止まる形で下の保存のボタンを見る)。
期待: 開いた直後から保存のボタンが窓の中にあり (下の行が窓の下の端に付く)、書きかけの字はまだ出ない。得意先を選んでも
得意先の欄は動かず、下の行の左に「保存していない変更があります」が出る。保存のボタンの字は打つたびに変わり、
右下の「AI に質問」の丸と重ならない。下の保存のボタンで誤りの画面に止まり、字は隠れる。

この試験が見る所: 開いた直後に `[data-save-bar]` が見えず、`form[data-enter-guard] button.btn-primary[type=submit]` の
`bounding_box()` が送らずに窓 (高さ 720) の中にあり、その真ん中の `elementFromPoint` がそのボタン。得意先を選ぶ前と後で
得意先の欄 (字で探す欄の包み) の文書の上の端が同じ (前の帯の形では 49 下がった) で、選んだ後に `[data-save-bar]` が見える。
数量と単価の後のボタンの字に「4,800」がある。保存のボタンの箱と `#assistant-float` (右下の丸) の箱が重ならない。数量だけの保存の後に
`#id_customer_error` が見え、URL が `/orders/new/` のままで、`[data-save-bar]` が見えない。ボタンは字で探さず、入力の form の主色の submit で引く。
開いた直後の下の帯 (`form[data-enter-guard] .seizou-save-row`) は、position が fixed・影が none・左上の丸みが 0px で、下の端が窓の下の端、
左と右の端が上の帯 (`header.seizou-header`) の左と右、左の端が左のメニュー (`.drawer-side > aside`) の右の端 (どれも ±1。メニューの幅の数は書かない)。

短い form (test_e50_short_form_band): A 社で休日の新規登録 (`/masters/holidays/new/`) を 1280×720 で開くと、帯が窓の下の端にあり、
カード (form) の下の端が帯の上の端より上 (間が空く)。窓を 390×844 にすると、帯の左の端が 0・右の端が窓の幅・下の端が 844 で、
横にはみ出さない。上の帯の「メニューを開く」を押すと、帯の真ん中の点の一番上が帯の外 (開いたメニューか重ねの幕) になる
(メニューは少し待ってから動いて出るので、wait_for_function で待つ)。
電話の幅の受注 (test_e50_phone_order_band): 390×844 で受注の新規登録に得意先・1 行目の部品・数量 10・単価 480 を入れ、一番下まで送ると、
カード (form) の下の端が帯の上の端以下で (最後の「行を足す」が帯に隠れない)、横にはみ出さない。帯の高さは print で出す
(`-rP` で見て門の記録に書く。見積もり 141px)。受注は保存しない。
見ない所 (理由): 発注・一括出荷・説明書の下の行 (同じ形。本体の手の確かめ)、小窓の入力と黄の知らせの後 (試験 test_core_save_bar が HTML を見る)、
Tab の送り・表示の設定の画面・説明書の編集の帯・上の帯に色を選んだ人の下の帯 (本体の手の確かめ)、下の保存のボタンで保存まで進む形 (E-08 が見る)、
離れる確認の字 (ブラウザが決める)。
下の帯の形は scripts/css/input.css の決まりなので、app.css を作り直すまでは帯の形の見方が落ちる。
"""

from urllib.parse import urlparse

from playwright.sync_api import expect

from .conftest import choose, login, seed_name, shot

VIEWPORT = {"width": 1280, "height": 720}
PHONE = {"width": 390, "height": 844}
CUSTOMER_CODE = "C001"
PART_NO = "WK-1001"
SUBMIT = "form[data-enter-guard] button.btn-primary[type=submit]"
BAR = "[data-save-bar]"
BAND = "form[data-enter-guard] .seizou-save-row"
MENU_OPEN = 'label[for=seizou-drawer][aria-label="メニューを開く"]'


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


def _band(page) -> dict:
    """下の帯・上の帯・左のメニュー・帯を囲む form の箱と、帯の見た目の 3 つの値と、窓の高さと幅を 1 回で読む (C113)。"""
    return page.evaluate(
        """(selector) => {
            const box = (element) => {
                if (!element) return null;
                const rect = element.getBoundingClientRect();
                return {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, height: rect.height};
            };
            const band = document.querySelector(selector);
            const style = getComputedStyle(band);
            return {
                band: box(band),
                position: style.position,
                shadow: style.boxShadow,
                radius: style.borderTopLeftRadius,
                header: box(document.querySelector("header.seizou-header")),
                aside: box(document.querySelector(".drawer-side > aside")),
                form: box(band.closest("form")),
                innerHeight: window.innerHeight,
                clientWidth: document.documentElement.clientWidth,
                scrollWidth: document.documentElement.scrollWidth,
            };
        }""",
        BAND,
    )


def _near(first: float, second: float) -> bool:
    return abs(first - second) <= 1


def _band_shape(band: dict) -> None:
    """帯は窓に対して置き (fixed)、影と角の丸みが無く、下の端が窓の下の端 (決め UX-SAVEBAR-1 = c)。"""
    assert band["position"] == "fixed", band
    assert band["shadow"] == "none", band
    assert band["radius"] == "0px", band
    assert _near(band["band"]["bottom"], band["innerHeight"]), band


def test_e50_order_save_bar(page_a, company_a):
    login(page_a, company_a)
    page_a.set_viewport_size(VIEWPORT)
    path = "/orders/new/"
    page_a.goto(f"{company_a.base_url}{path}")
    bar = page_a.locator(BAR)
    expect(bar).to_be_hidden()
    _save_in_window(page_a)
    # 下の帯は上の帯と同じ形: 本文の列の左右いっぱい (左のメニューの右の端から窓の右の端まで)・窓の下の端 (C113)
    band = _band(page_a)
    _band_shape(band)
    assert _near(band["band"]["left"], band["header"]["left"]), band
    assert _near(band["band"]["right"], band["header"]["right"]), band
    assert _near(band["band"]["left"], band["aside"]["right"]), band

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
    toggle_box = page_a.locator("#assistant-float").bounding_box()
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


def test_e50_short_form_band(page_a, company_a):
    """短い form でも帯は窓の下の端 (カードとの間が空く)。狭い幅では窓の左右いっぱいで、開いたメニューが帯の上に出る (C113)。"""
    login(page_a, company_a)
    page_a.set_viewport_size(VIEWPORT)
    page_a.goto(f"{company_a.base_url}/masters/holidays/new/")
    band = _band(page_a)
    _band_shape(band)
    assert band["form"]["bottom"] < band["band"]["top"], band
    assert _near(band["band"]["left"], band["header"]["left"]), band
    assert _near(band["band"]["right"], band["header"]["right"]), band
    shot(page_a, "E-50", "a-holiday-band")

    # 狭い幅 (メニューは閉じた形): 帯は窓の左右いっぱいで、横にはみ出さない
    page_a.set_viewport_size(PHONE)
    band = _band(page_a)
    _band_shape(band)
    assert _near(band["band"]["bottom"], PHONE["height"]), band
    assert _near(band["band"]["left"], 0), band
    assert _near(band["band"]["right"], band["clientWidth"]), band
    assert band["scrollWidth"] <= band["clientWidth"], band

    # メニューを開くと、開いたメニューか重ねの幕が帯の上に出る (メニューは少し待ってから動いて出るので、帯の外になるのを待つ)
    page_a.locator(MENU_OPEN).click()
    page_a.wait_for_function(
        """(selector) => {
            const band = document.querySelector(selector);
            const rect = band.getBoundingClientRect();
            const element = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
            return !!element && !band.contains(element);
        }""",
        arg=BAND,
    )


def test_e50_phone_order_band(page_a, company_a):
    """電話の幅で受注の明細を入れて一番下まで送っても、カードの下の端 (最後の「行を足す」) が帯に隠れない (C113)。受注は保存しない。"""
    login(page_a, company_a)
    page_a.set_viewport_size(PHONE)
    page_a.goto(f"{company_a.base_url}/orders/new/")
    choose(page_a, "customer", seed_name(company_a, "partners", CUSTOMER_CODE))
    choose(page_a, "lines-0-item", PART_NO)
    page_a.fill("input[name=lines-0-qty]", "10")
    page_a.fill("input[name=lines-0-unit_price]", "480")
    expect(page_a.locator(SUBMIT).first).to_contain_text("4,800")
    page_a.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
    band = _band(page_a)
    _band_shape(band)
    assert band["form"]["bottom"] <= band["band"]["top"], band
    assert band["scrollWidth"] <= band["clientWidth"], band
    print(f"E-50 390×844 の受注の帯の高さ: {band['band']['height']}px (見積もり 141px)")
    shot(page_a, "E-50", "a-phone-order-band")
