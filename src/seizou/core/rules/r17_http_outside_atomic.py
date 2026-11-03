"""R17 の検査: 相手の会社への HTTP は transaction.atomic の外で送る。

対象は全部の .py (services.py を含む)。

atomic の中 = 次の本体の中。入れ子の def・async def・lambda・class の本体には入らない (呼ばれるかが言い切れない)。
- with (と async with) の項の式が、transaction.atomic(...)・django.db.transaction.atomic(...)、
  または同じファイルで from django.db.transaction import atomic した名前の呼び出し atomic(...)。
  変数に入れた物 (with block:) と、呼び出しでない with transaction.atomic: は見ない。
- デコレータが transaction.atomic・transaction.atomic(...)・(上の import がある) atomic・atomic(...) の関数の本体の全部。

見る形 (直に呼ぶ物。言い切れる物だけ):
- requests の get・post・put・patch・delete・head・options・request、httpx の同じ 8 つと stream、
  urllib.request.urlopen。名前は同じファイルのどこかの import (関数の中の import も) で付いた物だけを見る。
  requests.Session().post(...)・session.post(...)・httpx.Client()・SDK の呼び出しは見ない。
- 1 段だけ追う: atomic の中で素の名前で呼んだ関数が同じファイルの最上位の def で、その本体
  (入れ子の def には入らない) に上の直の呼び出しがあれば違反。2 段先と self.x()・module.f() は追わない。
行は、直の呼び出しは属性の名前がある行、1 段の追いは atomic の中の呼び出しの行。
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path

CODE = "R17"
MESSAGE = "相手の会社への HTTP は transaction.atomic の外で送る"
FIX = (
    "相手の会社へ送る HTTP の呼び出し (requests・httpx・urlopen) を transaction.atomic のブロックの外に出し、"
    "送る前に書く物と送った後に書く物を別々の atomic にしてください。"
)

_VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "request"})
_SENDERS = {
    "requests": _VERBS,
    "httpx": _VERBS | {"stream"},
}
_URLOPEN = ["urllib", "request", "urlopen"]
_ATOMIC_CHAINS = (["transaction", "atomic"], ["django", "db", "transaction", "atomic"])
_ATOMIC_IMPORTED = "django.db.transaction.atomic"

# 中に入らない入れ子 (呼ばれるかが言い切れない)
_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def applies(path: Path) -> bool:
    return path.suffix == ".py"


def find(tree: ast.Module) -> Iterator[int]:
    imported = _imported_names(tree)
    senders = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(_is_http_call(call, imported) for call in _calls_in(node.body))
    }

    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            inside = any(_is_atomic_call(item.context_expr, imported) for item in node.items)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inside = any(_is_atomic_decorator(d, imported) for d in node.decorator_list)
        else:
            continue
        if not inside:
            continue
        for call in _calls_in(node.body):
            if _is_http_call(call, imported):
                # 鎖が複数の行にまたがるときは、属性の名前がある行を指す。
                lines.add(call.func.end_lineno)
            elif isinstance(call.func, ast.Name) and call.func.id in senders:
                lines.add(call.lineno)
    yield from sorted(lines)


def _calls_in(body: Iterable[ast.stmt]) -> Iterator[ast.Call]:
    """文の並びの中の呼び出し。入れ子の def・lambda・class の中には入らない。"""
    stack = [node for node in body if not isinstance(node, _NESTED)]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Call):
            yield node
        stack.extend(child for child in ast.iter_child_nodes(node) if not isinstance(child, _NESTED))


def _is_atomic_ref(expr: ast.expr, imported: dict[str, str]) -> bool:
    """式が transaction.atomic (と import した atomic) を指すか。"""
    parts = _dotted(expr)
    if parts is None:
        return False
    if parts in _ATOMIC_CHAINS:
        return True
    return parts == ["atomic"] and imported.get("atomic") == _ATOMIC_IMPORTED


def _is_atomic_call(expr: ast.expr, imported: dict[str, str]) -> bool:
    return isinstance(expr, ast.Call) and _is_atomic_ref(expr.func, imported)


def _is_atomic_decorator(expr: ast.expr, imported: dict[str, str]) -> bool:
    return _is_atomic_ref(expr, imported) or _is_atomic_call(expr, imported)


def _is_http_call(call: ast.Call, imported: dict[str, str]) -> bool:
    """requests・httpx の決まった verb か urlopen を直に呼ぶか (名前は import で付いた物だけ)。"""
    parts = _dotted(call.func)
    if parts is None or parts[0] not in imported:
        return False
    full = imported[parts[0]].split(".") + parts[1:]
    if full == _URLOPEN:
        return True
    return len(full) == 2 and full[1] in _SENDERS.get(full[0], ())


def _imported_names(tree: ast.Module) -> dict[str, str]:
    """import で付いた名前と、その名前が指す物の完全な名前 (R16 と同じ読み方)。

    関数の中の import も拾う。同じ名前が別々の物を指すときは、言い切れないので外す。
    """
    bound: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bound.setdefault(alias.asname, set()).add(alias.name)
                else:
                    head = alias.name.split(".")[0]
                    bound.setdefault(head, set()).add(head)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound.setdefault(alias.asname or alias.name, set()).add(f"{node.module}.{alias.name}")
    return {name: next(iter(targets)) for name, targets in bound.items() if len(targets) == 1}


def _dotted(expr: ast.expr) -> list[str] | None:
    """a.b.c の形の名前の並び。ほかの形は None。"""
    names: list[str] = []
    while isinstance(expr, ast.Attribute):
        names.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return None
    names.append(expr.id)
    return names[::-1]
