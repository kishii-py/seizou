"""R15 の検査: 画面は core の土台の画面を継承する。

対象は views.py と views/ の中の .py。seizou.core の中は土台そのものなので見ない。
見る形: 名前が View で終わる親を持つ class のうち、土台の名前を親に持たず、
同じファイルの中で土台を継承した class も親に持たない物。同じファイルの中だけを辿る。
名前が Mixin で終わる親は数えない。View で終わる親の無い class は画面と言い切れないので出さない。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from .constants import BASE_VIEWS

CODE = "R15"
MESSAGE = (
    "画面は core の土台の画面 (seizou.core.views の Base*View) を継承する。"
    "上書きしてよいのは columns・search_fields・tabs・form_class・service・template_name・"
    "get_queryset・screen_name・screen_help の 9 つだけ"
)
FIX = "画面の class は seizou.core.views の土台の画面 (Base*View) のどれかを継承してください。"

# seizou.core のフォルダ (この module の 1 つ上の 1 つ上)
_CORE_DIR = Path(__file__).resolve().parent.parent


def applies(path: Path) -> bool:
    if not (path.name == "views.py" or path.parent.name == "views"):
        return False
    return not path.resolve().is_relative_to(_CORE_DIR)


def find(tree: ast.Module) -> Iterator[int]:
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    by_name = {node.name: node for node in classes}
    memo: dict[str, bool] = {}

    def inherits_base(name: str, visiting: frozenset[str]) -> bool:
        """同じファイルの class name が、土台を (同じファイルの中で辿って) 継承しているか。"""
        if name in memo:
            return memo[name]
        node = by_name.get(name)
        if node is None or name in visiting:
            return False
        visiting = visiting | {name}
        answer = any(
            parent in BASE_VIEWS or inherits_base(parent, visiting) for parent in _parent_names(node)
        )
        memo[name] = answer
        return answer

    for node in classes:
        parents = _parent_names(node)
        if not any(parent.endswith("View") for parent in parents):
            continue
        if any(parent in BASE_VIEWS for parent in parents):
            continue
        if any(parent in by_name and inherits_base(parent, frozenset()) for parent in parents):
            continue
        yield node.lineno


def _parent_names(node: ast.ClassDef) -> list[str]:
    """親の名前 (generic.ListView なら ListView)。Mixin で終わる名前は外す。"""
    names = []
    for base in node.bases:
        name = _last_name(base)
        if name and not name.endswith("Mixin"):
            names.append(name)
    return names


def _last_name(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    if isinstance(expr, ast.Subscript):
        return _last_name(expr.value)
    return None
