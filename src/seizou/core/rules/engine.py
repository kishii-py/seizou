"""決まりの検査の枠 (05 の 4 節)。

パスの一覧 (ファイルかフォルダ) を歩き、.py を標準の ast で 1 回だけ読み、
決まりごとの module に同じ木を渡す。どの決まりも 1 ファイルの中で答えを出す。
字のファイル (.html・.js) は 1 回だけ UTF-8 の字で読み、find_text を持つ決まりに渡す。
Django の設定を読まない。

決まりごとの module は次の物を持つ。
- CODE: 番号 (例 "R14")
- MESSAGE: RULES.md の番号の文 (平文)
- FIX: 直し方の 1 文 (平文)
- applies(path): このファイルを見るか
- find(tree): 違反の行の番号を返す
- find_in_dir(path) (無くてよい): 中身を読まないフォルダ (migrations) の場所を見て、違反の行の番号を返す
- find_text(path, text) (無くてよい): 字のファイル (.html・.js) を読んで、違反の行の番号を返す
重さは全部 error (会期中の決まりは全部止める)。
"""

from __future__ import annotations

import ast
import io
import os
import re
import tokenize
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

#: 歩くときに中身を読まないフォルダの名前。直に渡したファイルとフォルダには当てない。
SKIP_DIRS = frozenset({"migrations", "tests", ".venv", "site-packages", "node_modules", ".git", "vendor"})

#: 字で読むファイルの末尾 (R18・R19 が見る)。
TEXT_SUFFIXES = frozenset({".html", ".js"})

#: 中身を読まないが、場所を決まりに渡すフォルダの名前 (R16 が部品の app 名の migration を見る)
LOCATION_DIRS = frozenset({"migrations"})

# 黙らせる印。番号は必須 (番号の無い印は合わない)。
_IGNORE = re.compile(r"seizou:\s*ignore\[(R\d+)\]")

# 字のファイルで、印だけのコメントの行の始まり (この行の印は直下の行にも合う)。
_TEXT_COMMENT_STARTS = ("{#", "<!--", "//", "/*", "{% comment")

# テンプレートのコメント 3 種。左から順に外すので、{% comment %} の中の {# #} は外側ごと外れる。
_TEMPLATE_COMMENTS = re.compile(
    r"\{%\s*comment\b.*?%\}.*?\{%\s*endcomment\s*%\}|\{#.*?#\}|<!--.*?-->",
    re.S,
)

# 行に中身があるかを決めるときに数えない字句の種類
_NOT_CODE = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)


@dataclass(frozen=True)
class Violation:
    """違反 1 つ。"""

    code: str
    message: str
    path: str
    line: int
    fix: str


@dataclass(frozen=True)
class Result:
    """検査の結果。黙らせた違反は一覧に入れず、数だけを持つ。"""

    violations: tuple[Violation, ...]
    silenced: int
    scanned: int


def run(paths: Iterable[str | os.PathLike], rules: Sequence) -> Result:
    """パスの一覧を、決まりの module の並び rules で検査する。"""
    files, location_dirs = _walk(paths)
    violations: list[Violation] = []
    silenced = 0
    scanned = 0

    for path in files:
        if path.suffix in TEXT_SUFFIXES:
            # 字のファイルは下の輪で読む。
            continue
        try:
            data = path.read_bytes()
            tree = ast.parse(data, filename=str(path))
        except (OSError, SyntaxError, ValueError):
            # 読めないファイルと構文の誤りは違反にせず飛ばし、走査の本数に数えない
            # (構文の誤りは ruff と pytest が別に捕まえる)。
            continue
        scanned += 1

        found = [(rule, line) for rule in rules if rule.applies(path) for line in rule.find(tree)]
        if not found:
            continue
        marks = _ignore_marks(data) if b"seizou:" in data else {}
        for rule, line in found:
            if rule.CODE in marks.get(line, ()):
                silenced += 1
            else:
                violations.append(Violation(rule.CODE, rule.MESSAGE, str(path), line, rule.FIX))

    for path in files:
        if path.suffix not in TEXT_SUFFIXES:
            continue
        try:
            data = path.read_bytes()
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            # 読めないファイルは違反にせず飛ばし、走査の本数に数えない (.py の構文の誤りと同じ)。
            continue
        scanned += 1

        found = [
            (rule, line)
            for rule in rules
            if hasattr(rule, "find_text") and rule.applies(path)
            for line in rule.find_text(path, text)
        ]
        if not found:
            continue
        marks = _text_ignore_marks(text) if b"seizou:" in data else {}
        for rule, line in found:
            if rule.CODE in marks.get(line, ()):
                silenced += 1
            else:
                violations.append(Violation(rule.CODE, rule.MESSAGE, str(path), line, rule.FIX))

    for folder in location_dirs:
        for rule in rules:
            find_in_dir = getattr(rule, "find_in_dir", None)
            if find_in_dir is None:
                continue
            for line in find_in_dir(folder):
                violations.append(Violation(rule.CODE, rule.MESSAGE, str(folder), line, rule.FIX))

    violations.sort(key=lambda v: (v.path, v.line, v.code))
    return Result(tuple(violations), silenced, scanned)


def _walk(paths: Iterable[str | os.PathLike]) -> tuple[list[Path], list[Path]]:
    """読む .py の一覧と、中身を読まずに場所だけを渡すフォルダの一覧を返す。"""
    # 字のファイル (TEXT_SUFFIXES) も同じ files に入れる (seen を共通にし、同じファイルを 2 度数えない)。
    # run が末尾で .py と字のファイルに分けて読む。
    files: list[Path] = []
    location_dirs: list[Path] = []
    seen: set[str] = set()

    def add(target: list[Path], path: Path) -> None:
        key = os.path.realpath(path)
        if key not in seen:
            seen.add(key)
            target.append(path)

    for given in paths:
        start = Path(given)
        if start.is_file():
            # 直に渡されたファイルは、置き場の名前にかかわらず読む。
            if start.suffix == ".py":
                add(files, start)
            elif start.suffix in TEXT_SUFFIXES:
                add(files, start)
            continue
        if not start.is_dir():
            continue
        for root, dirnames, filenames in os.walk(start):
            kept = []
            for name in sorted(dirnames):
                if name not in SKIP_DIRS:
                    kept.append(name)
                elif name in LOCATION_DIRS:
                    add(location_dirs, Path(root, name))
            dirnames[:] = kept
            for name in sorted(filenames):
                if name.endswith(".py"):
                    add(files, Path(root, name))
                elif Path(name).suffix in TEXT_SUFFIXES:
                    add(files, Path(root, name))
    return files, location_dirs


def _ignore_marks(data: bytes) -> dict[int, set[str]]:
    """黙らせる印のある行と番号。行の末尾の印はその行に、印だけの行は直下の行に合う。

    コメントは ast に乗らないので、元の文字列を tokenize で読む。
    """
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(data).readline))
    except (tokenize.TokenError, SyntaxError):
        return {}

    code_lines: set[int] = set()
    comments: list[tokenize.TokenInfo] = []
    for token in tokens:
        if token.type == tokenize.COMMENT:
            comments.append(token)
        elif token.type not in _NOT_CODE:
            code_lines.update(range(token.start[0], token.end[0] + 1))

    marks: dict[int, set[str]] = {}
    for token in comments:
        codes = set(_IGNORE.findall(token.string))
        if not codes:
            continue
        line = token.start[0]
        marks.setdefault(line, set()).update(codes)
        if line not in code_lines:
            marks.setdefault(line + 1, set()).update(codes)
    return marks


def _text_ignore_marks(text: str) -> dict[int, set[str]]:
    """字のファイルの、黙らせる印のある行と番号。

    印はその行に合う。行が印だけのコメントの行 ({#・<!--・//・/*・{% comment で始まる) なら、直下の行にも合う。
    行は改行 (\\n) で数える (決まりの行の番号と同じ数え方)。
    """
    marks: dict[int, set[str]] = {}
    for number, line in enumerate(text.split("\n"), start=1):
        codes = set(_IGNORE.findall(line))
        if not codes:
            continue
        marks.setdefault(number, set()).update(codes)
        if line.strip().startswith(_TEXT_COMMENT_STARTS):
            marks.setdefault(number + 1, set()).update(codes)
    return marks


def strip_template_comments(text: str) -> str:
    """テンプレートのコメント ({# #}・{% comment %}〜{% endcomment %}・<!-- -->) を外す。

    外した所には中の改行の数だけ改行を残し、行の番号を保つ。JS のコメントは外さない
    (// は URL の中にもあり、言い切れない)。
    """
    return _TEMPLATE_COMMENTS.sub(lambda match: "\n" * match.group(0).count("\n"), text)
