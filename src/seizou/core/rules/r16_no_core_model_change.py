"""R16 の検査: 部品の表を変えない。会社ごとの項目は拡張の表に足す。

見る形 (決まりの検査の設計の決め。言い切れる物だけ):
- models.py (と models/ の中の .py) の class で、親が部品の app の models の class である物。
  親の名前は同じファイルの import で決める (例 from seizou.masters.models import Item の後の
  class MyItem(Item)、import seizou.masters.models as masters_models の後の masters_models.Item)。
  行は class の行。proxy = True の class (表を作らず、列も足せない) は出さない。
- migrations のフォルダのうち、すぐ上のフォルダの名前が部品の app の名前で、seizou の package の外にある物
  (会社の側に部品の app 名の migration がある)。フォルダには行が無いので、行の番号は 0。
  中身は読まない (枠が場所だけを渡す)。

部品の app の名前は constants.PART_APPS (部品どうしの呼ぶ向きの設計の決めの表) から読む。
OneToOneField や ForeignKey で部品の表を指す (拡張の表) のは継承でないので出さない。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from .constants import PART_APPS

CODE = "R16"
MESSAGE = "部品の表を変えない。会社ごとの項目は拡張の表に足す"
FIX = "部品の表を継承して列を足さず、会社ごとの項目は fields の拡張の表 (<表>Ext) に足してください。"

# seizou の package のフォルダ (この module の rules → core → seizou)
_SEIZOU_DIR = Path(__file__).resolve().parents[2]

_PART_APPS = frozenset(PART_APPS)


def applies(path: Path) -> bool:
    return path.name == "models.py" or path.parent.name == "models"


def find(tree: ast.Module) -> Iterator[int]:
    imported = _imported_names(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if any(_is_part_model(base, imported) for base in node.bases) and not _is_proxy(node):
            yield node.lineno


def find_in_dir(path: Path) -> Iterator[int]:
    if path.name != "migrations" or path.parent.name not in _PART_APPS:
        return
    if path.resolve().is_relative_to(_SEIZOU_DIR):
        # 部品自身の migrations。
        return
    yield 0


def _imported_names(tree: ast.Module) -> dict[str, str]:
    """import で付いた名前と、その名前が指す物の完全な名前。

    同じ名前が別々の物を指すときは、言い切れないので外す。
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


def _is_part_model(expr: ast.expr, imported: dict[str, str]) -> bool:
    """親の式が、部品の app の models の class (seizou.<app>.models.<名前>) を指すか。"""
    parts = _dotted(expr)
    if not parts or parts[0] not in imported:
        return False
    full = imported[parts[0]].split(".") + parts[1:]
    return len(full) == 4 and full[0] == "seizou" and full[1] in _PART_APPS and full[2] == "models"


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


def _is_proxy(node: ast.ClassDef) -> bool:
    """class の中の class Meta に proxy = True があるか。"""
    for stmt in node.body:
        if isinstance(stmt, ast.ClassDef) and stmt.name == "Meta":
            for item in stmt.body:
                if (
                    isinstance(item, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "proxy" for t in item.targets)
                    and isinstance(item.value, ast.Constant)
                    and item.value.value is True
                ):
                    return True
    return False
