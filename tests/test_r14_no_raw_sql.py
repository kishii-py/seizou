"""R14: SQL を直接書かない (raw・cursor を使わない)。

決まりの検査 (seizou.core.rules.check) を見本のファイルに直に当てる。Django の設定に頼らない。
"""

from pathlib import Path

from seizou.core.rules import check

FIXTURES = Path(__file__).parent / "fixtures" / "rules" / "r14"
RULES_MD = Path(__file__).parent.parent / "RULES.md"


def _line_of(path: Path, text: str) -> int:
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if text in line:
            return number
    raise AssertionError(f"{path} に {text} が無い")


def _rules_md_text(code: str) -> str:
    for line in RULES_MD.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {code} |"):
            return line.split("|")[2].strip().replace("`", "")
    raise AssertionError(f"RULES.md に {code} の行が無い")


def test_bad_examples_report_each_form_with_file_and_line():
    bad = FIXTURES / "bad"
    result = check([bad])

    expected = {
        (str(bad / "services.py"), _line_of(bad / "services.py", "objects.raw(")),
        (str(bad / "reports.py"), _line_of(bad / "reports.py", "connection.cursor()")),
        (str(bad / "queries.py"), _line_of(bad / "queries.py", ".extra(")),
        (str(bad / "expressions.py"), _line_of(bad / "expressions.py", "import RawSQL")),
        (str(bad / "expressions.py"), _line_of(bad / "expressions.py", "RawSQL(")),
    }
    assert {(v.path, v.line) for v in result.violations} == expected
    assert {v.code for v in result.violations} == {"R14"}
    assert result.scanned == 4
    assert result.silenced == 0


def test_good_example_has_no_violation():
    result = check([FIXTURES / "good"])

    assert result.violations == ()
    assert result.scanned == 1


def test_message_is_the_rules_md_text_and_fix_points_to_orm():
    result = check([FIXTURES / "bad" / "services.py"])

    (violation,) = result.violations
    assert violation.message == _rules_md_text("R14")
    assert "ORM" in violation.fix


def test_raw_spread_over_lines_points_to_the_raw_line(tmp_path):
    source = tmp_path / "selectors.py"
    source.write_text(
        "def heavy(Item):\n"
        "    return (\n"
        "        Item.objects.filter(active=True)\n"
        "        .raw('SELECT 1')\n"
        "    )\n",
        encoding="utf-8",
    )

    result = check([source])

    assert [(v.code, v.line) for v in result.violations] == [("R14", 4)]
