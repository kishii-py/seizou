"""R19 の検査: class 名を途中で組み立てない。古い版の class 名を使わない。

対象は .html の全部 (.js と .py は見ない)。テンプレートのコメントを外した字の中の、class 属性の値を見る
(値は改行をまたいでよい)。行は class= のある行。1 つの値につき違反は 1 件まで。同じ行は 1 件。
title= や data- の属性と、{% icon %} の引数は見ない。

(a) 途中で組み立てない。値を字の断片と tag ({{ }}・{% %}) に分けて、次のどれかなら違反。
- {{ }} がある (出す字が分からない。Tailwind も読めない)。
- tag の名前が if・elif・else・endif の外 (for・with・firstof など。出す字が分からない)。
- if と endif の対応が値の中で閉じない (elif・else が if の外にある形も)。
- {% if %} の tag が 8 を超える (入れ子も兄弟も合わせて数える。描き方が多すぎるので値を分ける)。
- 全部の描き方 (if の塊ごとに枝を 1 つ選ぶ。else が無ければ空も選ぶ) のどれかで、空白で分けた class 名が、
  値の字の断片 (全部の枝の中の物も) を空白で分けた名前の集まりに丸ごと無い (名前が tag の切れ目で継ぎ合わされた)。
固定の字に if の tag で丸ごとの名前を足す形 (whitespace-nowrap{% if x %} font-mono{% endif %}) は通る。
字の断片の中の {{ と {% の外の字は見ない (script の中の class="' + x + '" は tag が無いので通る)。

(b) 古い版の class 名。字の断片の名前の 1 つ 1 つの、variant の接頭辞 (lg: など) と ! を外した最後の部分が
constants.OLD_CLASS_NAMES にあれば違反。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .constants import OLD_CLASS_NAMES
from .engine import strip_template_comments

CODE = "R19"
MESSAGE = "class 名を途中で組み立てない。古い版の class 名を使わない"
FIX = (
    "class 名は固定の字で書き、if の tag で足すのは丸ごとの class 名だけにし ({{ }} と古い版の名前を class に入れない)、"
    "値で変わる見た目は data- の属性と固定の variant の class (data-[tone=primary]:bg-primary/15 の形) で切り替えてください。"
)

#: 1 つの値の中の {% if %} の tag の数の上限
MAX_IF_TAGS = 8

_CLASS = re.compile(r'\bclass\s*=\s*(["\'])(.*?)\1', re.S)
# tag は 1 行の中で閉じる物だけ (Django のテンプレートと同じ読み方)。
_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}")
_TAG_NAME = re.compile(r"\{%\s*(\w*)")
_CONDITIONAL = frozenset({"if", "elif", "else", "endif"})
_OLD = frozenset(OLD_CLASS_NAMES)


@dataclass
class _IfBlock:
    """{% if %}〜{% endif %} の塊。branches は枝ごとの中身 (字の断片と入れ子の塊の並び)。"""

    branches: list[list] = field(default_factory=lambda: [[]])
    has_else: bool = False


def applies(path: Path) -> bool:
    return path.suffix == ".html"


def find(tree: ast.Module) -> Iterator[int]:
    # .py は見ない (applies が偽)。
    return iter(())


def find_text(path: Path, text: str) -> Iterator[int]:
    text = strip_template_comments(text)
    lines = {
        text.count("\n", 0, match.start()) + 1
        for match in _CLASS.finditer(text)
        if _is_violation(match.group(2))
    }
    yield from sorted(lines)


def _is_violation(value: str) -> bool:
    """class の値 1 つが (a) か (b) に当たるか。"""
    pieces: list[str] = []
    tags: list[str] = []
    tokens: list[tuple[str, str]] = []
    position = 0
    for match in _TAG.finditer(value):
        pieces.append(value[position : match.start()])
        tokens.append(("text", value[position : match.start()]))
        tag = match.group(0)
        if tag.startswith("{{"):
            return True
        name = _TAG_NAME.match(tag).group(1)
        if name not in _CONDITIONAL:
            return True
        tags.append(name)
        tokens.append(("tag", name))
        position = match.end()
    pieces.append(value[position:])
    tokens.append(("text", value[position:]))

    names = {name for piece in pieces for name in piece.split()}
    if any(name.rsplit(":", 1)[-1].strip("!") in _OLD for name in names):
        return True
    if not tags:
        return False
    if tags.count("if") > MAX_IF_TAGS:
        return True
    items = _parse(tokens)
    if items is None:
        return True
    return any(name not in names for rendering in _renderings(items) for name in rendering.split())


def _parse(tokens: list[tuple[str, str]]) -> list | None:
    """字の断片と if の塊の並びにする。if と endif の対応が閉じなければ None。"""
    root: list = []
    current = root
    stack: list[tuple[_IfBlock, list]] = []
    for kind, value in tokens:
        if kind == "text":
            if value:
                current.append(value)
        elif value == "if":
            block = _IfBlock()
            current.append(block)
            stack.append((block, current))
            current = block.branches[-1]
        elif value in ("elif", "else"):
            if not stack or stack[-1][0].has_else:
                return None
            block = stack[-1][0]
            block.has_else = value == "else"
            block.branches.append([])
            current = block.branches[-1]
        else:  # endif
            if not stack:
                return None
            _, current = stack.pop()
    if stack:
        return None
    return root


def _renderings(items: list) -> set[str]:
    """並びの全部の描き方 (同じ字は 1 つ)。"""
    results = {""}
    for item in items:
        if isinstance(item, str):
            options = {item}
        else:
            options = set()
            for branch in item.branches:
                options |= _renderings(branch)
            if not item.has_else:
                options.add("")
        results = {before + after for before in results for after in options}
    return results
