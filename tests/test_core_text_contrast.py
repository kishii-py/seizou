"""薄い字と薄い印を濃くする決まり (初見テスト R5 の候補 C49)。

C49 の 1 = 選んでいない tab の字を地の字の 60% にする (scripts/css/input.css の末尾)。
C49 の 2 = マニュアルの手順の check の箱を、読むだけの印 (disabled) のまま縁が見える濃さにする (manuals.markdown の CLASSES)。
C49 の 6 = 淡い札と淡い知らせの字を、daisyUI が同じ色に組にしている濃い字の色にする (決め C49-1 = a。11-06)。
C49 の 7 = 黄の知らせの頭の三角の印だけを黄のまま残す (決め C49-2 = a。11-06)。
元の字 (input.css と CLASSES) を見る試験と、作り直した app.css を見る試験 (名前に built が付く物) を分ける。
app.css を作り直さずに input.css を直すと、built の試験だけが落ちる。
"""

import re
from pathlib import Path

import seizou.core
from seizou.manuals import markdown as manual_markdown

CORE = Path(seizou.core.__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
INPUT_CSS = REPO / "scripts" / "css" / "input.css"
APP_CSS = CORE / "static" / "css" / "app.css"

# daisyUI の選んでいない tab の選び方 (app.css の daisyUI の規則と同じ字)。選んだ tab と指を乗せた tab を除く。
TAB_SELECTOR = (
    ".tab:is(.tabs>.tab):not(:checked,label:has(:checked),:hover,.tab-active,"
    "[aria-selected=true],[aria-current=true],[aria-current=page])"
)
SOFT_BADGE_HEAD = ".badge-soft:is("
SOFT_BADGE_COLORS = (".badge-warning", ".badge-success", ".badge-error", ".badge-info")
# (選び方, 中に有る字)。6 の淡い知らせ 3 色と黄の線の札、7 の黄の知らせの印。
SOFT_RULES = (
    (".alert-soft.alert-warning", "color: var(--color-warning-content);"),
    (".alert-soft.alert-error", "color: var(--color-error-content);"),
    (".alert-soft.alert-info", "color: var(--color-info-content);"),
    (".badge-outline.badge-warning", "border-color: var(--badge-color);"),
    (".alert-soft.alert-warning > svg", "color: var(--color-warning);"),
)
CHECK_BOX_ADDED = ("disabled:opacity-100", "border-base-content/50")
RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _block(css: str, selector: str) -> str:
    """input.css で「選び方 + 空白 + {」の後から次の } までの中身。"""
    start = css.index(selector + " {") + len(selector) + 2
    return css[start : css.index("}", start)]


def _built_rules() -> list[tuple[str, str]]:
    """作り直した app.css の、中に規則を持たない一番内側の (選び方, 中身) の組。空白は除く (作り直しで空白が詰まるため)。"""
    text = APP_CSS.read_text(encoding="utf-8")
    return [(sel.replace(" ", ""), body.replace(" ", "")) for sel, body in RULE_RE.findall(text)]


def _has_built_rule(rules: list[tuple[str, str]], head: str, needle: str) -> bool:
    head = head.replace(" ", "")
    needle = needle.replace(" ", "").rstrip(";")  # 作り直すと最後の ; が落ちる
    return any(head in sel and needle in body for sel, body in rules)


# --- 元の字 (input.css と CLASSES) ------------------------------------------------------------------------


def test_input_css_has_unselected_tab_rule():
    body = _block(INPUT_CSS.read_text(encoding="utf-8"), TAB_SELECTOR)
    assert "color: color-mix(in oklab, var(--color-base-content) 60%, transparent);" in body


def test_manual_check_box_is_visible_while_disabled():
    classes = manual_markdown.CLASSES["check_box"].split()
    assert classes[:4] == ["checkbox", "checkbox-sm", "mr-2", "align-middle"]
    for name in CHECK_BOX_ADDED:
        assert name in classes, name


def test_input_css_has_soft_badge_and_alert_rules():
    css = INPUT_CSS.read_text(encoding="utf-8")
    start = css.index(SOFT_BADGE_HEAD)
    selector = css[start : css.index("{", start)]
    for name in SOFT_BADGE_COLORS:
        assert name in selector, name
    for name in ("badge-primary", "badge-ghost"):  # 主色の濃い字の色は白なので入れない
        assert name not in selector, name
    assert "color: var(--badge-fg);" in css[css.index("{", start) : css.index("}", start)]
    for selector, needle in SOFT_RULES:
        assert needle in _block(css, selector), selector
    assert "color: var(--badge-fg);" in _block(css, ".badge-outline.badge-warning")


# --- 作り直した app.css ------------------------------------------------------------------------------------


def test_built_css_has_unselected_tab_rule_and_check_box_classes():
    rules = _built_rules()
    tab_bodies = [body for sel, body in rules if ".tab:is(.tabs>.tab):not(" in sel and ":hover" in sel and ".tab-active" in sel]
    assert any("var(--color-base-content)60%" in body for body in tab_bodies)  # daisyUI の 50% の規則も残る
    text = APP_CSS.read_text(encoding="utf-8")
    for name in (".disabled\\:opacity-100", ".border-base-content\\/50"):
        assert name in text, name


def test_built_css_has_soft_badge_and_alert_rules():
    rules = _built_rules()
    assert _has_built_rule(rules, SOFT_BADGE_HEAD, "color:var(--badge-fg)")
    for selector, needle in SOFT_RULES:
        assert _has_built_rule(rules, selector, needle), selector
    assert _has_built_rule(rules, ".badge-outline.badge-warning", "color:var(--badge-fg)")
