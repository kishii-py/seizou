"""薄い字と薄い印を濃くする決まり (初見テスト R5 の候補 C49)。

C49 の 1 = 選んでいない tab の字を地の字の 60% にする (scripts/css/input.css の末尾)。
C49 の 2 = マニュアルの手順の check の箱を、読むだけの印 (disabled) のまま縁が見える濃さにする (manuals.markdown の CLASSES)。
C49 の 6 = 淡い札と淡い知らせの字を、daisyUI が同じ色に組にしている濃い字の色にする (決め C49-1 = a。11-06)。
C49 の 7 = 黄の知らせの頭の三角の印だけを黄のまま残す (決め C49-2 = a。11-06)。
画面の直し 15 件 (11-07) の 1 = 字に使う意味の色 (.text-error・.text-warning・.text-success・.text-info と、前に修飾の付いた 2 つ) を、
同じ色に黒を混ぜた色にする (A11Y-03・V1)。比は WCAG の式で計算して見る (白い地と、1 行おきの行と指を乗せた行の灰の地)。
元の字 (input.css と CLASSES) を見る試験と、作り直した app.css を見る試験 (名前に built が付く物) を分ける。
app.css を作り直さずに input.css を直すと、built の試験だけが落ちる。
"""

import math
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


# --- 字に使う意味の色 (画面の直し 15 件の 1。A11Y-03・V1) ----------------------------------------------------------

#: 字の色の class と、Tailwind が作る選び方の字 (前に修飾の付いた物は app.css の字と同じ)。(input.css の選び方, 混ぜる元の色の変数)
TONE_TEXT_RULES = (
    (".text-error,\n.data-\\[over\\=true\\]\\:text-error[data-over=\"true\"]", "--color-error"),
    (".text-warning,\n.data-\\[tone\\=warning\\]\\:text-warning[data-tone=\"warning\"]", "--color-warning"),
    (".text-success", "--color-success"),
    (".text-info", "--color-info"),
)
TONE_MIX_RE = re.compile(r"color: color-mix\(in oklab, var\((--color-[a-z]+)\) (\d+)%, black\);")
THEME_OKLCH_RE = re.compile(r"(--color-[a-z0-9-]+):oklch\(([\d.]+)% ([\d.]+) ([\d.]+)\)")
WHITE = (1.0, 1.0, 1.0)


def _linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gamma(c: float) -> float:
    c = min(1.0, max(0.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _oklab_to_srgb(lab: tuple[float, float, float]) -> tuple[float, float, float]:
    big_l, a, b = lab
    l_ = (big_l + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (big_l - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (big_l - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        _gamma(4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_),
        _gamma(-1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_),
        _gamma(-0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_),
    )


def _srgb_to_oklab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = (_linear(c) for c in rgb)
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def _oklch_to_srgb(lightness: float, chroma: float, hue: float) -> tuple[float, float, float]:
    return _oklab_to_srgb((lightness, chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))))


def mix_with_black(rgb: tuple[float, float, float], percent: int) -> tuple[float, float, float]:
    """color-mix(in oklab, rgb percent%, black) と同じ計算 (黒は oklab で (0, 0, 0))。"""
    lab = _srgb_to_oklab(rgb)
    return _oklab_to_srgb(tuple(c * percent / 100 for c in lab))


def contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """WCAG 2 の比 (1〜21)。"""
    def lum(rgb):
        r, g, b = (_linear(round(c * 255) / 255) for c in rgb)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def theme_colors() -> dict[str, tuple[float, float, float]]:
    """app.css の daisyUI の light の theme の色 (oklch) を sRGB にした物。"""
    text = APP_CSS.read_text(encoding="utf-8")
    return {name: _oklch_to_srgb(float(lightness) / 100, float(chroma), float(hue)) for name, lightness, chroma, hue in THEME_OKLCH_RE.findall(text)}


def test_input_css_darkens_tone_text_only():
    """字の class だけを黒に混ぜた色にし、塗りの色の変数 (--color-error など) は変えない。前に修飾の付いた 2 つは同じ規則の中。"""
    css = INPUT_CSS.read_text(encoding="utf-8")
    percents = set()
    for selector, variable in TONE_TEXT_RULES:
        body = _block(css, selector)
        match = TONE_MIX_RE.search(body)
        assert match and match.group(1) == variable, selector
        percents.add(int(match.group(2)))
    assert len(percents) == 1  # 4 色で同じ割合
    for variable in ("--color-error", "--color-warning", "--color-success", "--color-info"):
        assert f"{variable}:" not in css, variable  # 塗りの色そのものは theme のまま


def test_tone_text_contrast_is_at_least_4_5():
    """混ぜた色と白い地 (base-100)・1 行おきの行 (base-200)・指を乗せた行 (base-300) との比が、4 色とも字の目安 4.5 以上。"""
    css = INPUT_CSS.read_text(encoding="utf-8")
    colors = theme_colors()
    grounds = {"white": WHITE, "base-200": colors["--color-base-200"], "base-300": colors["--color-base-300"]}
    for selector, variable in TONE_TEXT_RULES:
        percent = int(TONE_MIX_RE.search(_block(css, selector)).group(2))
        original = colors[variable]
        assert contrast(original, WHITE) < 4.5, variable  # 元の色は字には薄い (前提の確かめ)
        darkened = mix_with_black(original, percent)
        for name, ground in grounds.items():
            assert contrast(darkened, ground) >= 4.5, (variable, name, contrast(darkened, ground))


def test_built_css_has_tone_text_rules():
    rules = _built_rules()
    for head in (".text-error", ".text-warning", ".text-success", ".text-info"):
        variable = "--color-" + head[len(".text-") :]
        assert any(sel.startswith(head + ",") or sel == head for sel, body in rules if f"color-mix(inoklab,var({variable})" in body), head
    for head in (".data-\\[over\\=true\\]\\:text-error[data-over=true]", ".data-\\[tone\\=warning\\]\\:text-warning[data-tone=warning]"):
        assert any(head in sel and "color-mix(inoklab,var(--color-" in body for sel, body in rules), head
