"""E-06 選ぶ欄 (品目・得意先) (仕様書の 6 番の 3 節)。

手順: 「ｱ」と「あ」で打つ → 31 件以上ある欄で下まで読む → 字を全部消す → 矢印キーと Enter で選ぶ → 日本語を打って変換の確定の Enter → 小窓の中の選ぶ欄。
期待: 同じ候補が出る。次のページが足される。全件に戻る。矢印と Enter で選べる。変換の確定の Enter で候補が選ばれない。小窓の中で一覧が切れない。

この試験が見る所: 受注の入力の明細の部品の欄で、候補の一覧が明細の表の箱に切られずにマウスで押せること (初見テスト R3 の「止まる」)、
人が普通に打つ速さで字を打ってすぐ Enter を押すと打った字の部品が選ばれること (新規登録と、最初から値の入っている編集の画面)。
見ない所 (理由): 「ｱ」と「あ」・下まで読む・字を全部消す・変換の確定の Enter・小窓の中の選ぶ欄 (今の作品の小窓に選ぶ欄が無い)。
得意先の欄は部品の欄と同じ JS (seizou-select.js) なので、別の関数を足さない。
"""

import re
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect

from .conftest import SELECT_WAIT_MS, login, new_order, seed_name, shot, unique_mark

PART_NO = "WK-1001"
TYPED_PART_NO = "WK-1021"
CUSTOMER_CODE = "C001"
FIELD = "lines-0-item"
TYPE_DELAY_MS = 100  # 人が普通に打つ速さ (1 字 0.1 秒)
# 送った後の scroll の event と置き直しは次の描画で動く。測る前に 2 つ先の描画まで待つ。
NEXT_FRAMES = "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"


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


def _type_then_enter(page) -> None:
    page.keyboard.type(TYPED_PART_NO, delay=TYPE_DELAY_MS)
    page.keyboard.press("Enter")


def test_e06_part_candidates_clickable_outside_table(page_a, company_a):
    """期待 (一部): 一覧が切れない。

    見る所: 受注の新規登録の明細の部品の欄の一覧が body の直下に描かれ、候補の真ん中の点の一番上の要素がその候補で、
    マウスで押すと選ばれること。見ない所: 小窓の中の欄 (今の作品に無い)。
    """
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/orders/new/")
    options = _open_part_field(page_a)

    parent_is_body = page_a.locator(f"[id='id_{FIELD}-ts-dropdown']").evaluate(
        "content => content.parentElement.parentElement === document.body"
    )
    assert parent_is_body
    # 画面全体の絵は窓の大きさを一時変えるので、測る前に撮る。
    shot(page_a, "E-06", "a-part-candidates")

    option = options.filter(has_text=PART_NO).first
    option.wait_for(state="visible", timeout=SELECT_WAIT_MS)
    option.scroll_into_view_if_needed()
    page_a.evaluate(NEXT_FRAMES)
    box = option.bounding_box()
    assert box is not None
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    on_top = page_a.evaluate(
        """([x, y]) => {
            const element = document.elementFromPoint(x, y);
            const option = element && element.closest(".option");
            return option ? option.textContent : "";
        }""",
        [x, y],
    )
    assert PART_NO in on_top

    page_a.mouse.click(x, y)
    expect(page_a.locator(f"select[name='{FIELD}'] option:checked")).to_contain_text(PART_NO, timeout=SELECT_WAIT_MS)


def test_e06_enter_right_after_typing_picks_typed_part(page_a, company_a):
    """期待 (一部): 矢印と Enter で選べる。

    見る所: 1 字 0.1 秒で品番を打ってすぐ Enter を押すと、前の一覧の先頭でなく打った品番が選ばれ、Enter で保存へ進まないこと。
    見ない所: 矢印キーで選ぶ所 (今の動きのまま)。
    """
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/orders/new/")
    _open_part_field(page_a)

    _type_then_enter(page_a)

    expect(page_a.locator(f"select[name='{FIELD}'] option:checked")).to_contain_text(
        TYPED_PART_NO, timeout=SELECT_WAIT_MS
    )
    assert urlparse(page_a.url).path == "/orders/new/"


def test_e06_enter_on_prefilled_field_picks_new_list(page_a, company_a):
    """期待 (一部): 矢印と Enter で選べる。

    見る所: 受注の編集の画面で、最初から部品の入っている明細の欄に品番を打ってすぐ Enter を押すと、新しい一覧の品番が選ばれること。
    見ない所: 保存 (編集の保存は E-08 の範囲)。
    """
    login(page_a, company_a)
    new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", CUSTOMER_CODE),
        part_no=PART_NO,
        qty=1,
        unit_price=480,
        mark=unique_mark("e06"),
    )
    detail = urlparse(page_a.url).path
    assert re.fullmatch(r"/orders/\d+/", detail)
    page_a.goto(f"{company_a.base_url}{detail}edit/")
    expect(page_a.locator(f"select[name='{FIELD}'] option:checked")).to_contain_text(PART_NO)
    _open_part_field(page_a)

    _type_then_enter(page_a)

    expect(page_a.locator(f"select[name='{FIELD}'] option:checked")).to_contain_text(
        TYPED_PART_NO, timeout=SELECT_WAIT_MS
    )
