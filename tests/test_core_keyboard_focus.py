"""キーボードの焦点 (画面の直し 15 件 (11-07) の 7 = A11Y-01・A11Y-05)。

- A11Y-01: 一覧の行の「詳細へ移る」link (class に link と utility の focus-visible:outline-2 を持つ) に Tab で着いても焦点の枠が出なかった。
  daisyUI の .link:focus が --tw-outline-style を none にし、utility の outline-style がその変数を読むため。scripts/css/input.css の
  層の外の規則 .link:focus-visible が solid を直に書く (utility の class を足す直し方は取らない = 同じ変数を読むので直らない)。
- A11Y-05: Shift+Tab で戻った欄が上の帯 (sticky・h-16) の下に隠れ、幅 640 未満では下の帯の分の余白 (96px) も足りなかった。
  html の scroll-padding-top と、下の帯がある画面の幅ごとの scroll-padding-bottom (帯の高さ + 8px) で直す (WCAG 2.2 の C43)。
元の字 (input.css) を見る試験と、作り直した app.css を見る試験 (名前に built が付く物) を分ける。ブラウザの動きは本体の手の確かめ。
"""

import re
from pathlib import Path

import seizou.core

CORE = Path(seizou.core.__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
INPUT_CSS = REPO / "scripts" / "css" / "input.css"
APP_CSS = CORE / "static" / "css" / "app.css"
TOPBAR = CORE / "templates" / "core" / "_topbar.html"
#: 下の帯の幅ごとの高さ (input.css の main:has(.seizou-save-row) の 3 つの数と同じ) と、その頭 (@media)
BOTTOM_STEPS = (("141px", None), ("113px", "@media (min-width: 640px) {"), ("65px", "@media (min-width: 64rem) {"))
ROW_LINK_CLASSES = "link link-primary block max-w-xs truncate focus-visible:outline-2 focus-visible:outline-primary"


def _rule(css: str, selector: str, start: int = 0) -> tuple[int, str]:
    """input.css の中の、selector で始まる決まりの (位置, { } の中身)。"""
    begin = css.index(selector + " {", start)
    return begin, css[begin : css.index("}", begin)]


def _built() -> str:
    return APP_CSS.read_text(encoding="utf-8").replace(" ", "")


# --- 元の字 (input.css) ----------------------------------------------------------------------------------------


def test_input_css_gives_links_a_visible_focus_ring():
    css = INPUT_CSS.read_text(encoding="utf-8")
    _, body = _rule(css, ".link:focus-visible")
    assert "outline: 2px solid var(--color-primary);" in body
    assert "outline-offset: 2px;" in body
    # 行の link の class の字は変えない (utility を足す直し方は取らない)
    rows = CORE / "templates" / "core" / "_rows.html"
    assert ROW_LINK_CLASSES in rows.read_text(encoding="utf-8")


def test_input_css_keeps_focused_fields_out_from_under_the_bars():
    css = INPUT_CSS.read_text(encoding="utf-8")
    _, html_rule = _rule(css, "html")
    assert "scroll-padding-top: calc(4rem + 8px);" in html_rule
    # 上の帯の高さの元の字 (h-16 = 4rem)
    assert 'class="seizou-header sticky top-0 z-30 flex h-16 ' in TOPBAR.read_text(encoding="utf-8")
    # 下の帯がある画面: 幅ごとの帯の高さ + 8px (main の下の余白と同じ 3 つの数。幅の頭も同じ)
    position = 0
    for height, head in BOTTOM_STEPS:
        position, body = _rule(css, "html:has(.seizou-save-row)", position)
        assert f"scroll-padding-bottom: calc({height} + 8px);" in body, height
        if head:
            assert css[:position].rstrip().endswith(head), height
        assert re.search(rf"padding-bottom: calc\({height} \+ 1(\.5)?rem \+ 56px\);", css), height  # main の余白の数と同じ
        position += 1
    assert css.count("html:has(.seizou-save-row) {") == 3
    assert "scroll-padding-bottom: 96px;" not in css


# --- 作り直した app.css ----------------------------------------------------------------------------------------


def test_built_css_has_link_focus_ring():
    built = _built()
    assert re.search(r"\.link:focus-visible\{[^}]*outline:2pxsolidvar\(--color-primary\)", built)
    assert re.search(r"\.link:focus-visible\{[^}]*outline-offset:2px", built)


def test_built_css_has_scroll_padding():
    built = _built()
    assert "html{scroll-padding-top:calc(4rem+8px)}" in built
    for height, _ in BOTTOM_STEPS:
        folded = f"{int(height[:-2]) + 8}px"  # 作り直しで同じ単位の calc は 1 つの数になる
        assert re.search(rf"html:has\(\.seizou-save-row\)\{{scroll-padding-bottom:(calc\({height}\+8px\)|{folded})\}}", built), height
