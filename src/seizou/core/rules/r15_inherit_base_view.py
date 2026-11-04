"""R15 の検査: 画面は core の土台の画面を継承し、上書きするのは 13 の属性だけ。

対象は views.py と views/ の中の .py。seizou.core の中は土台そのものなので見ない。
同じファイルの中だけを辿る。名前が Mixin で終わる親は数えない。

1 文目 (継承): 名前が View で終わる親を持つ class のうち、土台の名前を親に持たず、
同じファイルの中で土台を継承した class も親に持たない物。行は class の行。
View で終わる親の無い class は画面と言い切れないので出さない。

2 文目 (上書き): 土台を (同じファイルの中で辿って) 継承した class の本体で、
代入 (名前の的と型の注釈つきの代入)・関数の定義・class の定義の名前が 13 の属性 (OVERRIDABLE_ATTRS) に無い物。
行はその文の行。docstring と、名前の無い文 (pass など) は数えない。
組の展開の代入 (a, b = ...) は出さない。1 文目の違反の class には 2 文目を当てない (同じ class を 2 回出さない)。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from .constants import BASE_VIEWS, OVERRIDABLE_ATTRS

CODE = "R15"
MESSAGE = (
    "画面は core の土台の画面 (seizou.core.views の Base*View) を継承する。"
    "上書きしてよいのは model・ordering・columns・search_fields・filters・presets・tabs・form_class・service・"
    "template_name・get_queryset・screen_name・screen_help の 13 だけ"
)
FIX = (
    "画面の class は seizou.core.views の土台の画面 (Base*View) のどれかを継承し、"
    "class の中で上書きするのは 13 の属性だけにしてください。"
)

# seizou.core のフォルダ (この module の 1 つ上の 1 つ上)
_CORE_DIR = Path(__file__).resolve().parent.parent

_ALLOWED = frozenset(OVERRIDABLE_ATTRS)


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
        if any(parent in BASE_VIEWS or inherits_base(parent, frozenset()) for parent in parents):
            # 2 文目: 土台を継承した画面が、13 の属性の外を上書きしていないか。
            yield from _overrides_outside_allowed(node)
        elif any(parent.endswith("View") for parent in parents):
            # 1 文目: 画面なのに土台を継承していない。
            yield node.lineno


def _overrides_outside_allowed(node: ast.ClassDef) -> Iterator[int]:
    """class の本体の文のうち、13 の属性の外の名前を定める文の行。"""
    for index, stmt in enumerate(node.body):
        if index == 0 and _is_docstring(stmt):
            continue
        for name in _defined_names(stmt):
            if name not in _ALLOWED:
                yield stmt.lineno
                break


def _defined_names(stmt: ast.stmt) -> list[str]:
    """文が class の中に定める名前。言い切れない形 (組の展開など) は空にする。"""
    if isinstance(stmt, ast.Assign):
        return [target.id for target in stmt.targets if isinstance(target, ast.Name)]
    if isinstance(stmt, ast.AnnAssign):
        return [stmt.target.id] if isinstance(stmt.target, ast.Name) else []
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [stmt.name]
    return []


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


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
