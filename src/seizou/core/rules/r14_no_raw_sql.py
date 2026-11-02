"""R14 の検査: SQL を直接書かない。

対象は全部の .py (services.py を含む)。見る形は raw・cursor・extra の呼び出し (属性の名前) と、
RawSQL の呼び出しと import。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

CODE = "R14"
MESSAGE = "SQL を直接書かない (raw・cursor を使わない)"
FIX = "SQL の文を書かず、Django の ORM の問い合わせ (filter・annotate・aggregate など) に置き換えてください。"

_METHODS = frozenset({"raw", "cursor", "extra"})
_RAW_SQL = "RawSQL"


def applies(path: Path) -> bool:
    return True


def find(tree: ast.Module) -> Iterator[int]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and (func.attr in _METHODS or func.attr == _RAW_SQL):
                # 鎖が複数の行にまたがるときは、属性の名前がある行を指す。
                yield func.end_lineno
            elif isinstance(func, ast.Name) and func.id == _RAW_SQL:
                yield node.lineno
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == _RAW_SQL for alias in node.names):
                yield node.lineno
