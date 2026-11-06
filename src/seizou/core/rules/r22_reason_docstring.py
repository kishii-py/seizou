"""R22 の検査: 決まりを実装した関数は、docstring に「訳:」の行を持つ (何のためか・出所・変えてよい条件)。

業務の決まりは、なぜそうするかが分からないと、後の人が変えてよいか判断できず、改善が止まる (決め 11-07 00:0x)。
訳は「訳: <何のためか>。出所 = <どこから来たか>。変えてよい条件 = <1 文>。」の形。出所が分からなければ「未確認」と書く。

対象のファイルは services.py (部品の src/seizou/<app>/services.py と、会社が足した app の services.py)・
checks.py (会社のポカよけ)・seizou/core/checks/builtin.py (同梱のポカよけ)。
tests と migrations の下は歩かない (engine の SKIP_DIRS)。

対象の関数と class (ファイルの中の全部。入れ子の関数も 1 つずつ見る):
(a) 本文に raise RuleViolation(…) がある関数 (中心の決まりで断る)。
(b) 断りの文を返す関数 (名前が REFUSAL_RETURN_NAMES のどれか。今は cancel_refusal)。
(c) 本文で .quantize( を呼ぶ関数 (端数の丸め)。
(d) Check を継承した class (基底が Check か <名前>.Check の形。seizou.core.checks の Check)。
本文は入れ子の関数と class の中を除く (入れ子の物は、その物が対象になる)。
(a) は、今は中心の決まり (RuleViolation) に絞る。番号の無い断り (ValueError・PermissionError) は見ない
(対象が 80 を超えたので、依頼書 N226 の守ることのとおり絞った。RULES.md の R22 の末尾の 1 句)。

違反は、docstring が無い・「訳:」で始まる行が無い・その行に「出所 =」が無い の 3 つ。行は def (class) の行。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

CODE = "R22"
MESSAGE = (
    "決まりを実装した関数 (断る処理・端数の丸め・ポカよけの検査) は、docstring に「訳:」の行を持つ。"
    "訳は、何のためか・出所 (法令・商慣習・作者の決め・AI の案・未確認のどれか)・変えてよい条件を書く。"
    "出所が分からない物は「未確認」と書く (推測で埋めない)。"
    "断る処理は、今は中心の決まりで断る関数 (RuleViolation) と断りの文を返す関数 (cancel_refusal) だけを見る"
)
FIX = (
    "関数 (class) の docstring の末尾に「訳: <何のためか 1 文>。出所 = <RULES.md の番号・本人の決め・AI の案・"
    "会社の設定・仕様 01 の表・未確認のどれか>。変えてよい条件 = <1 文>。」の行を足してください。"
    "出所が分からなければ「未確認」と書いてください (推測で埋めない)。"
)

#: 断りの文を作って返す関数の名前 ((b)。raise は呼び手が変数で渡す)
REFUSAL_RETURN_NAMES = frozenset({"cancel_refusal"})
#: 中心の決まりで断る例外の名前 ((a))
REFUSAL_ERRORS = frozenset({"RuleViolation"})
#: 訳の行の頭と、その行に要る字
REASON_HEAD = "訳:"
ORIGIN_MARK = "出所 ="

_FILE_NAMES = frozenset({"services.py", "checks.py"})
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def applies(path: Path) -> bool:
    if path.name in _FILE_NAMES:
        return True
    # 同梱のポカよけ (seizou/core/checks/builtin.py)
    return path.name == "builtin.py" and path.parent.name == "checks" and path.parent.parent.name == "core"


def find(tree: ast.Module) -> Iterator[int]:
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, _FUNCS):
            target = node.name in REFUSAL_RETURN_NAMES or any(_is_rule(sub) for sub in _own_nodes(node))
        elif isinstance(node, ast.ClassDef):
            target = any(_is_check(base) for base in node.bases)
        else:
            continue
        if target and not _has_reason(node):
            lines.add(node.lineno)
    yield from sorted(lines)


def _own_nodes(func: ast.AST) -> Iterator[ast.AST]:
    """関数の本文の節。入れ子の関数と class の中には入らない (lambda の中には入る)。"""
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, _SCOPES):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _is_rule(node: ast.AST) -> bool:
    """(a) raise RuleViolation(…) か、(c) ….quantize(…) の節か。"""
    if isinstance(node, ast.Raise):
        exc = node.exc
        return isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) and exc.func.id in REFUSAL_ERRORS
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "quantize"


def _is_check(base: ast.expr) -> bool:
    """(d) 基底が Check か <名前>.Check か。"""
    if isinstance(base, ast.Name):
        return base.id == "Check"
    return isinstance(base, ast.Attribute) and base.attr == "Check"


def _has_reason(node: ast.AST) -> bool:
    """docstring に「訳:」で始まり「出所 =」を含む行があるか。"""
    doc = ast.get_docstring(node)
    if not doc:
        return False
    return any(line.strip().startswith(REASON_HEAD) and ORIGIN_MARK in line for line in doc.splitlines())
