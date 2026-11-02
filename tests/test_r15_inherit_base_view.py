"""R15: 画面は core の土台の画面 (seizou.core.views の Base*View) を継承する。

決まりの検査 (seizou.core.rules.check) を見本のファイルに直に当てる。Django の設定に頼らない。
"""

import ast
from pathlib import Path

import seizou.core
from seizou.core.rules import check
from seizou.core.rules.constants import BASE_VIEWS

FIXTURES = Path(__file__).parent / "fixtures" / "rules" / "r15"
RULES_MD = Path(__file__).parent.parent / "RULES.md"
CORE_VIEWS = Path(seizou.core.__file__).parent / "views.py"


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


def test_bad_example_reports_views_not_inheriting_base():
    views = FIXTURES / "bad" / "views.py"
    result = check([FIXTURES / "bad"])

    assert [(v.code, v.path, v.line) for v in result.violations] == [
        ("R15", str(views), _line_of(views, "class OrderListView(")),
        ("R15", str(views), _line_of(views, "class OrderDetailView(")),
    ]


def test_good_example_has_no_violation():
    result = check([FIXTURES / "good"])

    assert result.violations == ()
    assert result.scanned == 1


def test_same_file_inheritance_and_mixin_are_not_reported(tmp_path):
    views = tmp_path / "views.py"
    views.write_text(
        "class OrderListView(BaseListView):\n"
        "    pass\n"
        "class OrderMiddleView(OrderListView):\n"
        "    pass\n"
        "class OrderPrintView(PrintMixin, OrderMiddleView):\n"
        "    pass\n"
        "class OnlyMixinScreen(PrintMixin):\n"
        "    pass\n",
        encoding="utf-8",
    )

    assert check([views]).violations == ()


def test_parent_view_defined_in_another_file_is_reported(tmp_path):
    views = tmp_path / "views.py"
    views.write_text(
        "from .base import OrderBaseView\nclass OrderListView(OrderBaseView):\n    pass\n",
        encoding="utf-8",
    )

    assert [(v.code, v.line) for v in check([views]).violations] == [("R15", 2)]


def test_only_views_files_are_checked(tmp_path):
    (tmp_path / "forms.py").write_text("class OrderListView(ListView):\n    pass\n", encoding="utf-8")
    package = tmp_path / "views"
    package.mkdir()
    (package / "orders.py").write_text("class OrderListView(ListView):\n    pass\n", encoding="utf-8")

    result = check([tmp_path])

    assert [(Path(v.path).name, v.line) for v in result.violations] == [("orders.py", 1)]
    assert result.scanned == 2


def test_core_views_are_out_of_scope():
    result = check([CORE_VIEWS])

    assert result.scanned == 1
    assert result.violations == ()


def test_message_is_the_rules_md_text():
    result = check([FIXTURES / "bad"])

    assert {v.message for v in result.violations} == {_rules_md_text("R15")}
    assert all("Base" in v.fix for v in result.violations)


def test_base_view_names_match_core_views():
    tree = ast.parse(CORE_VIEWS.read_text(encoding="utf-8"))
    names = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name.startswith("Base") and node.name.endswith("View")
    }

    assert names == BASE_VIEWS
