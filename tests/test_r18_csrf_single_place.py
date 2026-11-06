"""R18: CSRF の見出しは土台のテンプレートの 1 か所にだけ書く。

決まりの検査 (seizou.core.rules.check) を一時の置き場 (tmp_path) の見本に直に当てる。Django の設定に頼らない。
枠の字のファイルの口 (.html・.js を歩く・読めないファイル・vendor・字の黙らせ方) の試験もここに置く。
"""

from pathlib import Path

import seizou
from seizou.core.rules import check, r18_csrf_single_place
from tests.rules_helpers import rules_md_text as _rules_md_text

SEIZOU_DIR = Path(seizou.__file__).parent

HEADER_HTML = """<div hx-post="/x/" hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'>送る</div>\n"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _r18(result):
    return [(v.path, v.line) for v in result.violations if v.code == "R18"]


def test_header_outside_base_is_reported(tmp_path):
    rows = _write(tmp_path / "templates" / "orders" / "_rows.html", "<table>\n" + HEADER_HTML + "</table>\n")
    script = _write(
        tmp_path / "static" / "js" / "app.js",
        "const token = document.cookie;\nfetch('/x/', {method: 'POST', headers: {\"x-csrftoken\": token}});\n",
    )

    result = check([tmp_path])

    assert sorted(_r18(result)) == sorted([(str(rows), 2), (str(script), 2)])
    assert result.scanned == 2
    assert result.silenced == 0


def test_base_html_is_the_allowed_place(tmp_path):
    base = _write(tmp_path / "templates" / "core" / "base.html", HEADER_HTML)

    result = check([base])

    assert _r18(result) == []
    assert result.scanned == 1

    topbar = _write(tmp_path / "templates" / "core" / "_topbar.html", HEADER_HTML)

    result = check([tmp_path])

    assert _r18(result) == [(str(topbar), 1)]
    assert result.scanned == 2


def test_csrf_token_tag_and_comments_are_not_reported(tmp_path):
    page = _write(
        tmp_path / "templates" / "orders" / "form.html",
        "<form method=\"post\">{% csrf_token %}</form>\n"
        "{# X-CSRFToken はここに書かない #}\n"
        "<!-- X-CSRFToken -->\n"
        "{% comment %}X-CSRFToken{% endcomment %}\n"
        "{% comment \"説明\" %}\n{# 入れ子の X-CSRFToken #}\n{% endcomment %}\n",
    )

    result = check([page])

    assert result.violations == ()
    assert result.scanned == 1


def test_vendor_js_is_not_walked(tmp_path):
    library = _write(
        tmp_path / "static" / "vendor" / "htmx.min.js",
        "var h={headers:{\"X-CSRFToken\":t}};\n",
    )

    result = check([tmp_path])

    assert result.violations == ()
    assert result.scanned == 0

    result = check([library])

    assert result.violations == ()
    assert result.scanned == 1


def test_text_files_are_counted_and_unreadable_ones_are_skipped(tmp_path):
    _write(tmp_path / "templates" / "orders" / "list.html", "<p>一覧</p>\n")
    _write(tmp_path / "services.py", "def act():\n    return 1\n")

    result = check([tmp_path])

    assert result.scanned == 2
    assert result.violations == ()

    broken = tmp_path / "templates" / "orders" / "broken.html"
    broken.write_bytes(b"<p>\xff\xfe X-CSRFToken</p>\n")

    result = check([tmp_path, broken])

    assert result.scanned == 2
    assert result.violations == ()


def test_ignore_mark_silences_r18(tmp_path):
    end_of_line = _write(
        tmp_path / "a" / "templates" / "orders" / "_a.html",
        HEADER_HTML.rstrip("\n") + "{# seizou: ignore[R18] 作り物の見本 #}\n",
    )
    line_above = _write(
        tmp_path / "b" / "templates" / "orders" / "_b.html",
        "{# seizou: ignore[R18] 理由 #}\n" + HEADER_HTML,
    )
    script = _write(
        tmp_path / "c" / "static" / "js" / "app.js",
        "// seizou: ignore[R18] 理由\nheaders: {\"X-CSRFToken\": token},\n",
    )
    other_number = _write(
        tmp_path / "d" / "templates" / "orders" / "_d.html",
        "{# seizou: ignore[R19] 理由 #}\n" + HEADER_HTML,
    )

    for path in (end_of_line, line_above, script):
        result = check([path])
        assert result.violations == (), path
        assert result.silenced == 1, path

    result = check([other_number])

    assert _r18(result) == [(str(other_number), 2)]
    assert result.silenced == 0


def test_current_package_has_single_place():
    result = check([SEIZOU_DIR])

    assert _r18(result) == []
    base = SEIZOU_DIR / "core" / "templates" / "core" / "base.html"
    lines = [
        line
        for line in base.read_text(encoding="utf-8").splitlines()
        if "X-CSRFToken" in line and not line.strip().startswith("{#")
    ]
    assert len(lines) == 1


def test_message_is_the_rules_md_text():
    assert r18_csrf_single_place.MESSAGE == _rules_md_text("R18")
    assert "base.html" in r18_csrf_single_place.FIX
