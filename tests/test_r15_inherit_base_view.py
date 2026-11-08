"""R15: 画面は core の土台の画面 (seizou.core.views の Base*View) を継承する。

決まりの検査 (seizou.core.rules.check) を見本のファイルに直に当てる。Django の設定に頼らない。
"""

import ast
from pathlib import Path

import seizou.core
from seizou.core.rules import check
from seizou.core.rules.constants import BASE_VIEWS, OVERRIDABLE_ATTRS
from tests.rules_helpers import line_of as _line_of, rules_md_text as _rules_md_text

FIXTURES = Path(__file__).parent / "fixtures" / "rules" / "r15"
CORE_VIEWS = Path(seizou.core.__file__).parent / "views"


def _core_views_files() -> list[Path]:
    """core の画面の土台の package (views/) の中の .py を全部。"""
    return sorted(CORE_VIEWS.glob("*.py"))


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
    files = _core_views_files()
    result = check([CORE_VIEWS])

    assert len(files) >= 1
    assert result.scanned == len(files)
    assert result.violations == ()


def test_message_is_the_rules_md_text():
    result = check([FIXTURES / "bad"])

    assert {v.message for v in result.violations} == {_rules_md_text("R15")}
    assert all("Base" in v.fix for v in result.violations)


def test_base_view_names_match_core_views():
    files = _core_views_files()
    names = {
        node.name
        for path in files
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name.startswith("Base") and node.name.endswith("View")
    }

    assert len(files) >= 1
    assert names == BASE_VIEWS


# 2 文目: 上書きしてよいのは 13 の属性だけ


def test_override_outside_eleven_is_reported():
    views = FIXTURES / "bad_override" / "views.py"
    result = check([FIXTURES / "bad_override"])

    assert [(v.code, v.path, v.line) for v in result.violations] == [
        ("R15", str(views), _line_of(views, "paginate_by = 20")),
        ("R15", str(views), _line_of(views, "def get_context_data(")),
        ("R15", str(views), _line_of(views, 'success_url = "/orders/"')),
    ]
    assert result.scanned == 1


def test_eleven_overrides_are_allowed():
    result = check([FIXTURES / "good_override"])

    assert result.violations == ()
    assert result.scanned == 1


def test_non_inheriting_class_is_reported_once(tmp_path):
    views = tmp_path / "views.py"
    views.write_text(
        "class OrderListView(ListView):\n"
        "    paginate_by = 20\n"
        "    def get_context_data(self, **kwargs):\n"
        "        return {}\n",
        encoding="utf-8",
    )

    assert [(v.code, v.line) for v in check([views]).violations] == [("R15", 1)]


def test_nested_class_in_view_is_reported(tmp_path):
    views = tmp_path / "views.py"
    views.write_text(
        "class ItemListView(BaseListView):\n"
        "    model = Item\n"
        "    class Meta:\n"
        "        verbose_name = '品目'\n",
        encoding="utf-8",
    )

    assert [(v.code, v.line) for v in check([views]).violations] == [("R15", 3)]


def test_overridable_constants_match_core_views():
    files = _core_views_files()
    assert len(files) >= 1
    (value,) = [
        node.value
        for path in files
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "OVERRIDABLE" for target in node.targets)
    ]

    assert ast.literal_eval(value) == OVERRIDABLE_ATTRS
    assert len(OVERRIDABLE_ATTRS) == 13
