"""E-50 受注の入力の保存の帯 (N154。案 5-B)。

手順: A 社で受注の新規登録 (`/orders/new/`) を開く → 得意先を選ぶ → 1 行目の部品を選び、数量 10・単価 480 を入れる
→ 画面の下の端まで送る → 帯の「破棄」を押して 1 回目の確かめは「キャンセル」、2 回目は「OK」→ 開き直した画面で数量だけ 3 を入れて
(得意先なし) 帯の保存を押す。受注を増やさない (誤りで止まる形で帯の保存を見る)。
期待: 開いた直後は帯が無い。字を入れると上の帯のすぐ下に帯が出て、帯のボタンの字が画面の下の保存のボタンと同じ字に打つたびに変わる。
下へ送っても帯は上の帯の下に貼り付く。「キャンセル」では何も変わらず、「OK」で空の入力の画面に開き直り、離れるときの確認は出ない。
帯の保存は画面の下の保存のボタンを押し、誤りで止まった画面になる。

この試験が見る所: `[data-save-bar]` が開いた直後は見えず、得意先を選ぶと見える。`[data-save-bar-label]` の字が
`form[data-enter-guard] button.btn-primary[type=submit]` の字と同じ (得意先の後と、数量と単価の後。後の字は「4,800」を含む)。
下の端まで送った後の帯の `bounding_box()` の y が 60〜70 (上の帯の高さ 64px の下)。dialog の種類 (1 回目は dismiss・2 回目は accept)
と、1 回目の後も帯と数量 10 が残る事、2 回目の後に帯が見えず数量が空で、dialog に `beforeunload` が無い事。
帯の保存の後に得意先の欄の下に赤の字があり、URL が `/orders/new/` のまま。ボタンは字で探さず、帯の印と入力の form の主色の submit で引く。
見ない所 (理由): 発注の入力 (同じ土台。本体の手の確かめ)、小窓の入力 (試験 test_core_save_bar が HTML を見る)、
黄の知らせの後 (同じ試験)、幅 390 の見た目 (本体の手の確かめ)、帯の保存で保存まで進む形 (E-08 が下のボタンで保存を見る)。
"""

from urllib.parse import urlparse

from playwright.sync_api import expect

from .conftest import choose, login, seed_name, shot

CUSTOMER_CODE = "C001"
PART_NO = "WK-1001"
SUBMIT = "form[data-enter-guard] button.btn-primary[type=submit]"
BAR = "[data-save-bar]"
BAR_LABEL = "[data-save-bar-label]"
BAR_SAVE = "[data-save-bar-save]"
BAR_DISCARD = "[data-save-bar-discard]"


def _expect_bar_label_is_submit(page) -> str:
    text = page.locator(SUBMIT).first.inner_text().strip()
    expect(page.locator(BAR_LABEL)).to_have_text(text)
    return text


def test_e50_order_save_bar(page_a, company_a):
    login(page_a, company_a)
    path = "/orders/new/"
    page_a.goto(f"{company_a.base_url}{path}")
    bar = page_a.locator(BAR)
    expect(bar).to_be_hidden()

    choose(page_a, "customer", seed_name(company_a, "partners", CUSTOMER_CODE))
    expect(bar).to_be_visible()
    _expect_bar_label_is_submit(page_a)

    choose(page_a, "lines-0-item", PART_NO)
    page_a.fill("input[name=lines-0-qty]", "10")
    page_a.fill("input[name=lines-0-unit_price]", "480")
    expect(page_a.locator(SUBMIT).first).to_contain_text("4,800")
    assert "4,800" in _expect_bar_label_is_submit(page_a)

    page_a.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    box = bar.bounding_box()
    assert box is not None
    assert 60 <= box["y"] <= 70, box

    seen = []

    def on_dialog(dialog):
        seen.append(dialog.type)
        if len(seen) == 1:
            dialog.dismiss()
        else:
            dialog.accept()

    page_a.on("dialog", on_dialog)
    page_a.locator(BAR_DISCARD).click()
    expect(bar).to_be_visible()
    expect(page_a.locator("input[name=lines-0-qty]")).to_have_value("10")
    assert seen == ["confirm"]

    page_a.locator(BAR_DISCARD).click()
    # 「破棄」の OK は同じ URL を開き直す (location.assign)。遠い置き場では開き直しが終わる前に次の入力が走るので、読み込みを待つ
    page_a.wait_for_load_state("load")
    expect(page_a.locator("input[name=lines-0-qty]")).to_have_value("")
    expect(bar).to_be_hidden()
    assert urlparse(page_a.url).path == path
    assert "beforeunload" not in seen
    assert seen == ["confirm", "confirm"]

    page_a.fill("input[name=lines-0-qty]", "3")
    expect(bar).to_be_visible()
    page_a.locator(BAR_SAVE).click()
    expect(page_a.locator("#id_customer_error")).to_be_visible()
    assert urlparse(page_a.url).path == path
    expect(bar).to_be_hidden()
    shot(page_a, "E-50", "a-order-save-bar")
