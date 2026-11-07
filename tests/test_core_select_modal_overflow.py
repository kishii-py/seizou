"""小窓の中の「字を打って探す欄」の候補の一覧が、小窓の箱の下で切れない (本人の報告 11-07 17:0x)。

小窓 (dialog) の中の欄は候補の一覧を包みの中に描く (body の直下だと最上層の dialog の裏に隠れる)。daisyUI の modal-box は
overflow-y: auto なので、一覧が箱の下で切られて 1 件目しか見えなかった。seizou-select.js は一覧を開いている間だけ箱に
overflow-visible を付け、閉じたら外す。class は Tailwind が読む base.html の注に書き、app.css に入れる。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "src" / "seizou" / "core" / "static" / "js" / "seizou-select.js"
BASE = ROOT / "src" / "seizou" / "core" / "templates" / "core" / "base.html"
CSS = ROOT / "src" / "seizou" / "core" / "static" / "css" / "app.css"


def test_select_in_modal_lets_the_box_overflow_while_open():
    js = JS.read_text(encoding="utf-8")
    assert 'select.closest("#seizou-modal-box")' in js
    opened = js.index('ts.on("dropdown_open"')
    closed = js.index('ts.on("dropdown_close"')
    assert 'modalBox.classList.add("overflow-visible")' in js[opened:closed]
    assert 'modalBox.classList.remove("overflow-visible")' in js[closed:]


def test_overflow_visible_is_built_into_the_css():
    assert "seizou-select.js の overflow-visible" in BASE.read_text(encoding="utf-8")
    assert ".overflow-visible{overflow:visible}" in CSS.read_text(encoding="utf-8")
