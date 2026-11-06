"""R19: class 名を途中で組み立てない。古い版の class 名を使わない。

決まりの検査 (seizou.core.rules.check) を一時の置き場 (tmp_path) の見本に直に当てる。Django の設定に頼らない。
"""

import re
from pathlib import Path

import pytest

import seizou
from seizou.core.rules import check, r19_no_dynamic_classnames
from seizou.core.rules.constants import OLD_CLASS_NAMES
from tests.rules_helpers import rules_md_text as _rules_md_text

SEIZOU_DIR = Path(seizou.__file__).parent


def _template(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "templates" / "orders" / "x.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _r19(result):
    return [(v.path, v.line) for v in result.violations if v.code == "R19"]


GLUED = [
    "btn-{% if x %}primary{% else %}ghost{% endif %}",
    "{% if a %}btn-{% endif %}{% if b %}primary{% endif %}",
    "a{% if x %}b{% else %} c{% endif %}",
    "{% if x %}b{% else %}c {% endif %}d",
    "badge {{ tone }}",
    "btn {% firstof a b %}",
    "a{% if x %}b",
]

WHOLE_TOKENS = [
    "whitespace-nowrap{% if cell.code %} font-mono{% endif %}",
    "mb-6 grid gap-4 sm:grid-cols-2 {% if stats|length > 4 %}xl:grid-cols-5"
    "{% elif stats|length == 4 %}xl:grid-cols-4{% else %}xl:grid-cols-3{% endif %}",
    "{% if x %}btn-primary{% else %}btn-ghost{% endif %}",
    "whitespace-nowrap{% if cell.code %} font-mono{% endif %}{% if cell.date %} font-mono tabular-nums{% endif %}",
    "a{% if c %} b{% if d %} e{% endif %}{% endif %}",
    "plan-bar rounded data-[tone=primary]:bg-primary/15 data-[state=late]:border-error",
]


@pytest.mark.parametrize("value", GLUED)
def test_glued_names_are_reported(tmp_path, value):
    path = _template(tmp_path, f"<p>見出し</p>\n<div\n  class=\"{value}\">中身</div>\n")

    result = check([path])

    assert _r19(result) == [(str(path), 3)]


@pytest.mark.parametrize("value", WHOLE_TOKENS)
def test_whole_token_conditionals_are_allowed(tmp_path, value):
    path = _template(tmp_path, f'<div class="{value}">中身</div>\n')

    result = check([path])

    assert result.violations == ()
    assert result.scanned == 1


def test_one_violation_per_attribute(tmp_path):
    path = _template(
        tmp_path,
        '<div class="form-control {{ x }}">欄</div>\n'
        '<a class="btn-{{ x }}" href="/"><span class="badge {{ y }}">印</span></a>\n',
    )

    result = check([path])

    assert _r19(result) == [(str(path), 1), (str(path), 2)]


def test_too_many_blocks_is_reported(tmp_path):
    nine = _template(tmp_path / "nine", '<div class="' + "{% if c %} a{% endif %}" * 9 + '">枠</div>\n')
    eight = _template(tmp_path / "eight", '<div class="' + "{% if c %} a{% endif %}" * 8 + '">枠</div>\n')
    nested = _template(
        tmp_path / "nested", '<div class="' + "{% if c %}" * 9 + " a" + "{% endif %}" * 9 + '">枠</div>\n'
    )

    assert _r19(check([nine])) == [(str(nine), 1)]
    assert _r19(check([eight])) == []
    assert _r19(check([nested])) == [(str(nested), 1)]


def test_multiline_attribute_points_to_class_line(tmp_path):
    path = _template(tmp_path, '<p>上</p>\n<div class="btn\n  {{ x }}">枠</div>\n')

    result = check([path])

    assert _r19(result) == [(str(path), 2)]


def test_single_quoted_class_is_checked(tmp_path):
    path = _template(tmp_path, "<div class='btn-{% if x %}a{% endif %}'>枠</div>\n")

    result = check([path])

    assert _r19(result) == [(str(path), 1)]


@pytest.mark.parametrize(
    ("name", "count"),
    [
        ("form-control", 1),
        ("btn-group", 1),
        ("input-bordered", 1),
        ("lg:btn-group", 1),
        ("!card-compact", 1),
        ("tab-lg", 1),
        ("tabs-lg", 0),
        ("label", 0),
        ("alert-info", 0),
        ("menu-title", 0),
    ],
)
def test_old_class_names_are_reported(tmp_path, name, count):
    path = _template(tmp_path, f'<div class="p-4 {name}">枠</div>\n')

    result = check([path])

    assert len(_r19(result)) == count


def test_old_names_absent_from_built_css():
    css = (SEIZOU_DIR / "core" / "static" / "css" / "app.css").read_text(encoding="utf-8")

    for name in OLD_CLASS_NAMES:
        assert re.search(rf"\.{re.escape(name)}(?![A-Za-z0-9_-])", css) is None, name


def test_names_still_in_daisyui_five_are_not_listed():
    for name in ("alert-info", "menu-title", "tabs-box", "join", "card-sm"):
        assert name not in OLD_CLASS_NAMES
    assert len(OLD_CLASS_NAMES) == len(set(OLD_CLASS_NAMES))
    assert all(re.fullmatch(r"[a-z0-9-]+", name) for name in OLD_CLASS_NAMES)


def test_comments_and_other_attributes_are_not_reported(tmp_path):
    path = _template(
        tmp_path,
        '{# <a class="btn-{{ x }}"> #}\n'
        '{% comment %}class="form-control"{% endcomment %}\n'
        '<!-- class="btn-{% if x %}a{% endif %}" -->\n'
        '<span title="{{ x }}" data-tone="{{ t }}">印</span>\n'
        "<button hx-vals='{\"class\": \"x\"}'>送る</button>\n",
    )

    result = check([path])

    assert result.violations == ()
    assert result.scanned == 1


def test_js_is_not_checked_by_r19(tmp_path):
    script = tmp_path / "static" / "js" / "x.js"
    script.parent.mkdir(parents=True)
    script.write_text('el.className = "btn-" + kind;\nel.innerHTML = \'<i class="btn-{{ x }}"></i>\';\n', encoding="utf-8")

    result = check([tmp_path])

    assert _r19(result) == []
    assert result.scanned == 1


def test_ignore_mark_silences_r19(tmp_path):
    end_of_line = _template(
        tmp_path / "a", '<div class="badge {{ tone }}">印</div>{# seizou: ignore[R19] 作り物の見本 #}\n'
    )
    line_above = _template(
        tmp_path / "b", '{# seizou: ignore[R19] 作り物の見本 #}\n<div class="badge {{ tone }}">印</div>\n'
    )
    no_number = _template(
        tmp_path / "c", '{# seizou: ignore 作り物の見本 #}\n<div class="badge {{ tone }}">印</div>\n'
    )

    for path in (end_of_line, line_above):
        result = check([path])
        assert result.violations == (), path
        assert result.silenced == 1, path

    result = check([no_number])

    assert _r19(result) == [(str(no_number), 2)]
    assert result.silenced == 0


def test_current_package_templates_are_clean():
    result = check([SEIZOU_DIR])

    assert _r19(result) == []
    rows = (SEIZOU_DIR / "core" / "templates" / "core" / "_rows.html").read_text(encoding="utf-8")
    assert "{% if col.numeric %} text-right{% endif %}" in rows


def test_message_is_the_rules_md_text():
    assert r19_no_dynamic_classnames.MESSAGE == _rules_md_text("R19")
    assert "data-" in r19_no_dynamic_classnames.FIX
