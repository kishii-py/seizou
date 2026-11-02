"""R16: 部品の表を変えない。会社ごとの項目は拡張の表に足す。

決まりの検査 (seizou.core.rules.check) を見本のファイルとフォルダに直に当てる。Django の設定に頼らない。
"""

from pathlib import Path

import seizou
from seizou.core.rules import check

FIXTURES = Path(__file__).parent / "fixtures" / "rules" / "r16"
RULES_MD = Path(__file__).parent.parent / "RULES.md"
SEIZOU_DIR = Path(seizou.__file__).parent


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


def test_inheriting_part_model_is_reported():
    models = FIXTURES / "bad" / "models.py"
    result = check([models])

    assert [(v.code, v.path, v.line) for v in result.violations] == [
        ("R16", str(models), _line_of(models, "class MaterialItem(")),
        ("R16", str(models), _line_of(models, "class RegionPartner(")),
    ]


def test_part_app_migrations_on_company_side_are_reported():
    bad = FIXTURES / "bad"
    result = check([bad])

    assert [(v.code, v.path, v.line) for v in result.violations if v.line == 0] == [
        ("R16", str(bad / "masters" / "migrations"), 0),
    ]
    assert len(result.violations) == 3
    # migrations の中身は読まない (走査は models.py の 1 本だけ)。
    assert result.scanned == 1


def test_extension_table_is_allowed():
    result = check([FIXTURES / "good"])

    assert result.violations == ()
    assert result.scanned == 1


def test_part_migrations_inside_package_are_not_reported():
    assert (SEIZOU_DIR / "masters" / "migrations").is_dir()

    result = check([SEIZOU_DIR])

    assert [v for v in result.violations if v.code == "R16"] == []


def test_proxy_model_is_not_reported(tmp_path):
    models = tmp_path / "models.py"
    models.write_text(
        "from seizou.masters.models import Item\n"
        "class ActiveItem(Item):\n"
        "    class Meta:\n"
        "        proxy = True\n",
        encoding="utf-8",
    )

    assert check([models]).violations == ()


def test_message_is_the_rules_md_text():
    result = check([FIXTURES / "bad"])

    assert {v.message for v in result.violations} == {_rules_md_text("R16")}
    assert all("Ext" in v.fix for v in result.violations)
