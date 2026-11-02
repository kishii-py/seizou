"""R13 の検査: 数と金額を変える処理は部品の services を通す。画面から表に直接書かない。

対象は services.py の外の、画面と form とテンプレートタグと管理コマンド (05 の 4 節)。
ファイルの名前が views.py か forms.py、すぐ上のフォルダが views か templatetags、
またはパスの中に management/commands がある .py。services.py・models.py・migrations の中は見ない。
seizou.core の中も見る (core の画面と管理コマンドも表に直接書かない)。

見る形 (言い切れる物だけ。誤検知 0 を拾う数より先にする):
- objects か _default_manager を含む鎖の上の、表を書く呼び出し
  (例 Item.objects.create(...)・Order.objects.filter(...).update(...))。
- 位置の引数の無い save と delete の呼び出し (例 form.save()・obj.delete())。
  位置の引数のある物 (cache.delete(key)・storage.save(name, content)) と、
  commit=False の save (表に書かない) は出さない。
鎖に objects の無い update (context.update(...) などの辞書) は出さない。
行は属性の名前がある行。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

CODE = "R13"
MESSAGE = "数と金額を変える処理は、部品の services の関数を通す。画面から表に直接書かない"
FIX = "表を書く処理を部品の services.py の関数に移し、画面からはその関数を呼んでください。"

_MANAGERS = frozenset({"objects", "_default_manager"})
_WRITES = frozenset(
    {"create", "bulk_create", "bulk_update", "update", "delete", "get_or_create", "update_or_create"}
)
_SAVE_DELETE = frozenset({"save", "delete"})
_FILE_NAMES = frozenset({"views.py", "forms.py"})
_PARENT_DIRS = frozenset({"views", "templatetags"})
_OUT_OF_SCOPE = frozenset({"services.py", "models.py"})


def applies(path: Path) -> bool:
    if path.name in _OUT_OF_SCOPE or "migrations" in path.parts:
        return False
    if path.name in _FILE_NAMES or path.parent.name in _PARENT_DIRS:
        return True
    parts = path.parts
    return any(parts[i] == "management" and parts[i + 1] == "commands" for i in range(len(parts) - 1))


def find(tree: ast.Module) -> Iterator[int]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        func = node.func
        if func.attr in _WRITES and _has_manager(func.value):
            # 鎖が複数の行にまたがるときは、属性の名前がある行を指す。
            yield func.end_lineno
        elif func.attr in _SAVE_DELETE and not node.args and not _commit_false(node):
            yield func.end_lineno


def _has_manager(expr: ast.expr) -> bool:
    """呼び出しの受け手の鎖に objects か _default_manager の属性があるか。"""
    while True:
        if isinstance(expr, ast.Attribute):
            if expr.attr in _MANAGERS:
                return True
            expr = expr.value
        elif isinstance(expr, ast.Call):
            expr = expr.func
        elif isinstance(expr, ast.Subscript):
            expr = expr.value
        else:
            return False


def _commit_false(node: ast.Call) -> bool:
    """save(commit=False) の形か (ModelForm の save で、表に書かない)。"""
    return any(
        keyword.arg == "commit" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False
        for keyword in node.keywords
    )
