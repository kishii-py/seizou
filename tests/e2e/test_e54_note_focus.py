"""E-54 受注の詳細のメモの「書く」の後に、本文の箱へ focus が入る (初見テスト R5 の U163。候補 C42-先 の 1 と 5)。

手順: A 社で受注を 1 件入れる (受注の詳細へ移る) → メモの枠の「書く」を押す → 箱を押さずに印を打つ → 「やめる」→
「書く」に focus を置いて Enter → 箱を押さずに別の印を打つ → 「やめる」。保存しない (メモを残さない)。
期待: 詳細を開いただけでは focus はメモの枠の外にある。「書く」を押すと、押した直後と 0.5 秒後に focus が箱 (Quill の .ql-editor)
かその中にあり、打った字が隠した欄 body_html に入る。キーボードの Enter でも、Tab を押さずに箱に字が入る。「やめる」で閲覧の形に戻る。

この試験が見る所: `#notes a[hx-get]` (「書く」) を押した後、`document.activeElement` が `#notes .ql-editor` かその中。
`page.keyboard.type` で打った印が `#notes [name=body_html]` の値で `<p>{印}</p>` になる (新しい受注にはメモが無いので、打った字だけが入る)。
`#notes form a[hx-get]` (「やめる」) の後に `[data-note-editor]` が 0 で、`[data-note-view]` (保存したメモの本文) も 0。
枠と「書く」は字で探さない (「メモ」の字は見出しのほかに空の時の説明と 1 文にも出る) し、位置でも探さない (受注の詳細の並びは変わることがある)。
見ない所 (理由): 前の字の後ろに書き足す形と、長いメモで打った所が画面に見えるか (メモのある記録が要る。本体が画面の手で見る)、
記入の形の画面を直に開いた時と保存で断られた時 (同じ setUp を通る)、指示書の詳細 (同じ部分のテンプレートと JS)、
phone の幅で画面の鍵盤が開くか (Playwright の外)、B 社 (同じテンプレート)。
受注を 1 件増やす (E-51 と同じく A 社の DB に残る)。メモは増やさない。
"""

from playwright.sync_api import expect

from .conftest import login, new_order, seed_name, shot, unique_mark

CUSTOMER_CODE = "C001"
PART_NO = "WK-1001"
# focus が箱かその中にあれば真 (R5 の U163 の測りと同じ)
ACTIVE_JS = """() => {
  const box = document.querySelector("#notes .ql-editor");
  const active = document.activeElement;
  return !!(box && active && (active === box || box.contains(active)));
}"""
OUTSIDE_NOTES_JS = """() => {
  const notes = document.querySelector("#notes");
  return !!notes && !notes.contains(document.activeElement);
}"""


def _type_into_box(page, mark: str) -> None:
    """箱が起きて focus が入るのを待ち、0.5 秒後も箱にあることを見てから、箱を押さずに mark を打つ。"""
    expect(page.locator("#notes [data-note-editor] .ql-editor")).to_be_visible()
    page.wait_for_function(ACTIVE_JS)
    page.wait_for_timeout(500)
    assert page.evaluate(ACTIVE_JS), "0.5 秒後に focus が箱の外へ移った"
    page.keyboard.type(mark)
    expect(page.locator("#notes [name=body_html]")).to_have_value(f"<p>{mark}</p>")


def _cancel(page) -> None:
    """「やめる」で閲覧の形に戻す (保存しない)。"""
    page.locator("#notes form a[hx-get]").click()
    expect(page.locator("#notes [data-note-editor]")).to_have_count(0)
    expect(page.locator("#notes a[hx-get]")).to_have_count(1)


def test_e54_note_box_gets_focus_after_write(page_a, company_a):
    login(page_a, company_a)
    mark = unique_mark("e54")
    new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", CUSTOMER_CODE),
        part_no=PART_NO,
        qty=10,
        unit_price=480,
        mark=mark,
    )
    write = page_a.locator("#notes a[hx-get]")
    expect(write).to_have_count(1)
    assert page_a.evaluate(OUTSIDE_NOTES_JS), "詳細を開いただけでメモの枠に focus が入った"

    # 押す
    write.click()
    _type_into_box(page_a, f"{mark}-a")
    shot(page_a, "E-54", "a-click")
    _cancel(page_a)

    # キーボード (「書く」に focus を置いて Enter。Tab を押さない)
    page_a.locator("#notes a[hx-get]").focus()
    page_a.keyboard.press("Enter")
    _type_into_box(page_a, f"{mark}-b")
    shot(page_a, "E-54", "a-enter")
    _cancel(page_a)

    expect(page_a.locator("#notes [data-note-view]")).to_have_count(0)
