"""template の注記 `{# … #}` は 1 行に収める (請求書の一覧の表の上に注記の字が行の数だけ出た件。UI の会話の指示書 11-07 23:3x)。

Django の `{# #}` は同じ行の中でしか注記にならない。2 行に分けると 2 行とも字として画面に出て、
行ごとに include される部品 (billing/_row.html など) では行の数だけ並ぶ。複数行の注記は `{% comment %}…{% endcomment %}` に書く。
見る所: src/seizou の下の全部の .html で、`{#` があって同じ行のその後に `#}` が無い行と、`{#` が無いのに `#}` がある行が 0 件。
"""

from pathlib import Path

import seizou

SEIZOU_DIR = Path(seizou.__file__).parent


def _unclosed_comment_lines() -> list[str]:
    bad = []
    for path in sorted(SEIZOU_DIR.rglob("templates/**/*.html")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            start = line.find("{#")
            where = f"{path.relative_to(SEIZOU_DIR)}:{number}"
            if start >= 0 and "#}" not in line[start:]:
                bad.append(f"{where}: {{# が同じ行で閉じていない")
            elif start < 0 and "#}" in line:
                bad.append(f"{where}: #}} だけの行 (2 行に分けた注記の続き)")
    return bad


def test_template_comments_close_on_the_same_line():
    assert _unclosed_comment_lines() == []


def test_invoice_row_note_is_a_comment_block():
    text = (SEIZOU_DIR / "billing" / "templates" / "billing" / "_row.html").read_text(encoding="utf-8")

    assert "{% comment %}欄の描き方は core/_rows.html と同じ。" in text
    assert text.count("{% endcomment %}") == 1
    assert "残は数のまま)。 #}" not in text
