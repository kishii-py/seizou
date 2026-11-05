"""E-38 AI チャット (仕様書の 6 番の 3 節)。

手順: 右下の窓に「今週納期の注文残は」→ 答えの link を押す → 鍵の無い会社で窓を開く。
期待: 答えに数と link があり、link の先の画面の数と合う。鍵が無い会社では 1 文だけが出て入力の欄が無い (N34。BYOK)。

この試験は右下のボタンが入力の画面の保存と設定の最後の行を隠さないことだけを見る (初見テスト R3 の「動きが違う」の 1 つ目)。
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

    見る所: 幅 1280・高さ 720 の窓で、部品・取引先・受注の新規登録を下まで送ると、主のボタン (保存。受注では「… で受注する」) と右下の「AI に質問」のボタンが重ならず、
    主のボタンの真ん中の点の一番上がそのボタンであること。見ない所: 06 の E-38 の期待そのもの (AI の API の鍵が要る)。
    """
    login(page_a, company_a)
    page_a.set_viewport_size(VIEWPORT)
    for path in FORM_PATHS:
        page_a.goto(f"{company_a.base_url}{path}")
        page_a.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        toggle = page_a.locator("#assistant-toggle")
        # 主のボタンは字でなく「入力の form の中の主色の submit」で選ぶ (N138/N149 で字が「n 行・合計 ¥… で受注する」のように変わるため。conftest の _click_save と同じ形)
        save = page_a.locator("form[data-enter-guard] button.btn-primary[type=submit]").first
        toggle_box = toggle.bounding_box()
        save_box = save.bounding_box()
        assert toggle_box is not None and save_box is not None, path
        assert not _overlaps(toggle_box, save_box), path
        on_top = page_a.evaluate(
            """([x, y]) => {
                const element = document.elementFromPoint(x, y);
                const button = element && element.closest("button");
                return button ? button.textContent : "";
            }""",
            [save_box["x"] + save_box["width"] / 2, save_box["y"] + save_box["height"] / 2],
        )
        assert "保存" in on_top, path
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
