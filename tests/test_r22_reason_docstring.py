"""R22: 決まりを実装した関数は、docstring に「訳:」の行を持つ (何のためか・出所・変えてよい条件)。

決まりの検査 (seizou.core.rules.check) を一時の置き場 (tmp_path) の見本に直に当てる。Django の設定に頼らない。
見本の関数と class の名前と文は作り物。
"""

from pathlib import Path

import pytest

import seizou
from seizou.core.rules import check, r22_reason_docstring
from tests.rules_helpers import rules_md_text as _rules_md_text

SEIZOU_DIR = Path(seizou.__file__).parent

GOOD_DOC = (
    '    """作り物の関数。\n'
    "\n"
    "    訳: 作り物の見本のため。出所 = 未確認。変えてよい条件 = Issue「決まりの提案」でこの訳に対して議論してから。\n"
    '    """\n'
)
NO_REASON_DOC = '    """作り物の関数。"""\n'
NO_ORIGIN_DOC = '    """作り物の関数。\n\n    訳: 作り物の見本のため。変えてよい条件 = 議論してから。\n    """\n'

#: 対象の 4 つの形 → (ファイルの名前, def か class の行, docstring の後の本文)。def か class の行は 1 行目。
KINDS = {
    "a_rule_violation": (
        "services.py",
        "def cancel_sample(order):\n",
        '    if order.status == "cancelled":\n'
        '        raise RuleViolation("R05", "保存できません。作り物の理由です。")\n'
        "    return order\n",
    ),
    "b_refusal_return": (
        "services.py",
        "def cancel_refusal(invoice):\n",
        '    return "取り消せません。作り物の理由です。"\n',
    ),
    "c_quantize": (
        "services.py",
        "def round_sample(amount):\n",
        "    return amount.quantize(Decimal(1))\n",
    ),
    "d_check": (
        "checks.py",
        "class NoRemainingSample(Check):\n",
        '    name = "no_remaining_sample"\n',
    ),
}


def _source(kind: str, doc: str) -> tuple[str, str]:
    name, head, body = KINDS[kind]
    return name, head + doc + body


def _write(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def _r22(result):
    return [(v.path, v.line) for v in result.violations if v.code == "R22"]


@pytest.mark.parametrize("kind", KINDS)
def test_target_without_reason_is_reported(tmp_path, kind):
    name, text = _source(kind, NO_REASON_DOC)
    path = _write(tmp_path, name, text)

    result = check([path])

    assert _r22(result) == [(str(path), 1)]
    assert result.silenced == 0


@pytest.mark.parametrize("kind", KINDS)
def test_target_without_docstring_is_reported(tmp_path, kind):
    name, text = _source(kind, "")
    path = _write(tmp_path, name, text)

    assert _r22(check([path])) == [(str(path), 1)]


@pytest.mark.parametrize("kind", KINDS)
def test_reason_without_origin_is_reported(tmp_path, kind):
    name, text = _source(kind, NO_ORIGIN_DOC)
    path = _write(tmp_path, name, text)

    assert _r22(check([path])) == [(str(path), 1)]


@pytest.mark.parametrize("kind", KINDS)
def test_target_with_reason_and_origin_is_clean(tmp_path, kind):
    name, text = _source(kind, GOOD_DOC)
    path = _write(tmp_path, name, text)

    result = check([path])

    assert result.violations == ()
    assert result.scanned == 1


@pytest.mark.parametrize("kind", KINDS)
def test_ignore_mark_silences_r22(tmp_path, kind):
    name, text = _source(kind, NO_REASON_DOC)
    line_above = _write(tmp_path / "above", name, "# seizou: ignore[R22] 作り物の見本\n" + text)
    head, rest = text.split("\n", 1)
    end_of_line = _write(tmp_path / "end", name, head + "  # seizou: ignore[R22] 作り物の見本\n" + rest)
    no_number = _write(tmp_path / "none", name, "# seizou: ignore 作り物の見本\n" + text)

    for path in (line_above, end_of_line):
        result = check([path])
        assert result.violations == (), path
        assert result.silenced == 1, path

    result = check([no_number])

    assert _r22(result) == [(str(no_number), 2)]
    assert result.silenced == 0


def test_builtin_checks_file_is_a_target(tmp_path):
    _name, text = _source("d_check", NO_REASON_DOC)
    builtin = _write(tmp_path / "core" / "checks", "builtin.py", text)
    other = _write(tmp_path / "other" / "checks", "builtin.py", text)

    assert _r22(check([builtin])) == [(str(builtin), 1)]
    assert _r22(check([other])) == []


def test_attribute_base_check_is_a_target(tmp_path):
    path = _write(tmp_path, "checks.py", 'class NoRemainingSample(checks.Check):\n    name = "no_remaining_sample"\n')

    assert _r22(check([path])) == [(str(path), 1)]


def test_other_files_and_functions_are_not_targets(tmp_path):
    _name, text = _source("a_rule_violation", NO_REASON_DOC)
    for name in ("views.py", "forms.py", "models.py", "selectors.py"):
        _write(tmp_path / "out", name, text)
    services = _write(
        tmp_path / "in",
        "services.py",
        "def create_sample(data):\n"
        '    """作り物の関数 (番号の無い断りは今は見ない)。"""\n'
        "    if not data:\n"
        '        raise ValueError("保存できません。作り物の理由です。")\n'
        "    return round(data, 0)\n"
        "\n"
        "\n"
        "class SampleForm(Form):\n"
        "    pass\n",
    )

    result = check([tmp_path / "out", services])

    assert _r22(result) == []
    assert result.scanned == 5


def test_nested_function_is_its_own_target(tmp_path):
    path = _write(
        tmp_path,
        "services.py",
        "def outer(items):\n"
        '    """作り物の外の関数 (自分では断らない)。"""\n'
        "\n"
        "    def inner(item):\n"
        '        """作り物の中の関数。"""\n'
        '        raise RuleViolation("R02", "保存できません。作り物の理由です。")\n'
        "\n"
        "    return [inner(item) for item in items]\n",
    )

    assert _r22(check([path])) == [(str(path), 4)]


def test_current_package_has_reasons():
    result = check([SEIZOU_DIR])

    assert _r22(result) == []


def test_message_is_the_rules_md_text():
    assert r22_reason_docstring.MESSAGE == _rules_md_text("R22")
    assert "訳:" in r22_reason_docstring.FIX
    assert "未確認" in r22_reason_docstring.FIX
