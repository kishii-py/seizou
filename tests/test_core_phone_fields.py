"""スマホの欄の字の大きさ (画面の直し 15 件 (11-07) の 9 = forms-03)。

iOS の Safari は、字が 16px より小さい欄に焦点が移ると画面を拡大する (実機は未確認)。scripts/css/input.css の層の外の決まりが、
幅 639px 以下で .input・.select・.textarea と、字を打って探す欄 (.ts-wrapper .ts-control・その中の input。部品の CSS の 13px が app.css より後に
読まれるので包みを頭に付けて勝つ) の字を 16px にする。欄の高さは変えない。
元の字 (input.css) を見る試験と、作り直した app.css を見る試験 (名前に built が付く物) を分ける。
"""

import re
from pathlib import Path

import seizou.core

CORE = Path(seizou.core.__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
INPUT_CSS = REPO / "scripts" / "css" / "input.css"
APP_CSS = CORE / "static" / "css" / "app.css"
PHONE_SELECTOR = ".input,\n  .select,\n  .textarea,\n  .ts-wrapper .ts-control,\n  .ts-wrapper .ts-control input"


def test_input_css_makes_phone_fields_16px():
    css = INPUT_CSS.read_text(encoding="utf-8")
    start = css.index(PHONE_SELECTOR + " {")
    body = css[start : css.index("}", start)]
    assert "font-size: 16px;" in body
    assert "height" not in body and "min-height" not in body  # 欄の高さは変えない
    assert css[:start].rstrip().endswith("@media (max-width: 639px) {")


def test_built_css_has_phone_field_rule():
    built = APP_CSS.read_text(encoding="utf-8").replace(" ", "")
    assert re.search(r"@media\(max-width:639px\)\{\.input,\.select,\.textarea,\.ts-wrapper\.ts-control,\.ts-wrapper\.ts-controlinput\{font-size:16px\}", built)
