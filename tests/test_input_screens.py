"""入力の画面の直し (初見テスト R3 の所見)。Django の test client で HTML を見て、JS と CSS は字で約束を見る。

ブラウザでの確かめ (候補の一覧の位置・Enter・⋮ の位置・保存の位置・金額・残り・小窓) は tests/e2e の
E-06・E-08・E-38・E-12・E-05。
"""

import re
from pathlib import Path

import seizou.core

CORE = Path(seizou.core.__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
SELECT_JS = CORE / "static" / "js" / "seizou-select.js"
INPUT_CSS = REPO / "scripts" / "css" / "input.css"
APP_CSS = CORE / "static" / "css" / "app.css"


# --- 1. 候補の一覧を body の直下に描き、取りに行っている間の Enter を止める -------------------------


def test_select_js_draws_list_under_body_and_waits_for_load_on_enter():
    text = SELECT_JS.read_text(encoding="utf-8")
    for word in (
        "dropdownParent",
        '"body"',
        'closest("dialog")',
        "ts.loading > 0",
        "control_input.value.trim()",
        'ts.on("load"',
        "positionDropdown",
        'document.addEventListener("scroll"',
    ):
        assert word in text, word
    # 取った字を HTML として差し込まない。innerHTML の字は小窓を開く htmx の swap の名前の 1 か所 (今のまま) だけ。
    assert ".innerHTML" not in text and "insertAdjacentHTML" not in text
    assert text.count("innerHTML") == 1 and 'swap: "innerHTML"' in text
    # class は今の 2 行 (知らせと「詳細を見る」の link) だけ。新しい行で class を足さない。
    assert text.count("className =") == 2


def test_input_css_styles_list_under_body():
    source = INPUT_CSS.read_text(encoding="utf-8")
    assert "body > .ts-dropdown {" in source
    assert "body > .ts-dropdown .active {" in source
    built = APP_CSS.read_text(encoding="utf-8")
    assert len(re.findall(r"body ?> ?\.ts-dropdown", built)) >= 2
