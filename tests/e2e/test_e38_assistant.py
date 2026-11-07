"""E-38 AI チャット (仕様書の 6 番の 3 節)。

手順: 右下の丸いボタン「AI に質問」で右下の窓を開き、「今週納期の注文残は」→ 答えの link を押す → 鍵の無い会社で窓を開く。
期待: 答えに数と link があり、link の先の画面の数と合う。鍵が無い会社では 1 文だけが出て入力の欄が無い (N34。BYOK)。

この試験は右下の丸いボタンが入力の画面の保存と設定の最後の行を隠さないことだけを見る (初見テスト R3 の「動きが違う」の 1 つ目。
決め C37 で入口を左のメニューの下へ移して丸を消したが、本人の決め 11-07 09:3x で右下の丸を戻した。入口は 2 つ)。
見ない所 (理由): 質問と答えと link (AI の API の鍵が要る)、鍵の無い会社の 1 文 (test client の試験が見る)。
"""

from .conftest import login, shot

VIEWPORT = {"width": 1280, "height": 720}
FORM_PATHS = ("/masters/items/new/", "/masters/partners/new/", "/orders/new/")


def _overlaps(first: dict, second: dict) -> bool:
    return (
        first["x"] < second["x"] + second["width"]
        and second["x"] < first["x"] + first["width"]
        and first["y"] < second["y"] + second["height"]
        and second["y"] < first["y"] + first["height"]
    )


def test_e38_button_does_not_cover_save(page_a, company_a):
    """期待 (06 の E-38): 答えに数と link があり、link の先の画面の数と合う。鍵が無い会社では 1 文だけが出て入力の欄が無い。

    見る所: 幅 1280・高さ 720 の窓で、右下の「AI に質問」のボタンが絵だけの丸 (幅と高さ 48px 以下・見える字が無い・aria-label が「AI に質問」。N160) で、部品・取引先・受注の新規登録を下まで送ると、主のボタン (保存。受注では「… で受注する」) とそのボタンが重ならず、
    主のボタンの真ん中の点の一番上がそのボタンであること。見ない所: 06 の E-38 の期待そのもの (AI の API の鍵が要る)。
    """
    login(page_a, company_a)
    page_a.set_viewport_size(VIEWPORT)
    for path in FORM_PATHS:
        page_a.goto(f"{company_a.base_url}{path}")
        page_a.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        toggle = page_a.locator("#assistant-float")
        # 主のボタンは字でなく「入力の form の中の主色の submit」で選ぶ (N138/N149 で字が「n 行・合計 ¥… で受注する」のように変わるため。conftest の _click_save と同じ形)
        save = page_a.locator("form[data-enter-guard] button.btn-primary[type=submit]").first
        toggle_box = toggle.bounding_box()
        save_box = save.bounding_box()
        assert toggle_box is not None and save_box is not None, path
        assert toggle_box["width"] <= 48 and toggle_box["height"] <= 48, path  # 絵だけの丸いボタン (N160。幅と高さ 40px)
        assert toggle.get_attribute("aria-label") == "AI に質問", path
        assert toggle.inner_text().strip() == "", path  # 字は吹き出し (tooltip) と aria-label だけ
        assert not _overlaps(toggle_box, save_box), path
        on_top = page_a.evaluate(
            """([x, y]) => {
                const element = document.elementFromPoint(x, y);
                const button = element && element.closest("button");
                return !!button && button.type === "submit" && button.classList.contains("btn-primary");
            }""",
            [save_box["x"] + save_box["width"] / 2, save_box["y"] + save_box["height"] / 2],
        )
        assert on_top, path  # 主のボタンの字は画面ごとの動詞 (205 回目から) なので、字でなく主色の submit がいちばん上にあることで見る
    shot(page_a, "E-38", "a-order-form-bottom")


def test_e38_button_does_not_cover_last_setting_edit(page_a, company_a):
    """期待 (06 の E-38): 答えに数と link があり、link の先の画面の数と合う。鍵が無い会社では 1 文だけが出て入力の欄が無い。

    見る所: 同じ窓で設定の画面の表の最後の「編集」を押せること (Playwright の trial の押下が、押せる位置まで送り、上に別の要素が無いことを見る)。
    見ない所: 06 の E-38 の期待そのもの。押した先の編集の画面 (押さない)。
    """
    login(page_a, company_a)
    page_a.set_viewport_size(VIEWPORT)
    page_a.goto(f"{company_a.base_url}/settings/")
    last_edit = page_a.locator("table").first.get_by_role("link", name="編集", exact=True).last
    last_edit.click(trial=True)
