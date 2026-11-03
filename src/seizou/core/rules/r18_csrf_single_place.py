"""R18 の検査: CSRF の見出しは土台のテンプレートの 1 か所にだけ書く。

対象は .html の全部と、道に vendor の無い .js (外の library は見ない)。.py は見ない。
許す場所は、道の末尾 3 つが templates/core/base.html のファイル (土台のテンプレート。
会社の置き場が同じ道で上書きするときも同じ 1 か所)。このファイルは見ない。

見る形: 見出しの名前 x-csrftoken の字 (大小を区別しない)。.html はテンプレートのコメントを外してから見る。
.js はコメントを外さない (// は URL の中にもあり言い切れない。JS のコメントに書くなら印で黙らせる)。
{% csrf_token %} は見ない (見出しでなく form の hidden の欄。include される部分のテンプレートでは、
form の中かどうかが 1 ファイルで分からない)。行は字のある行で、同じ行は 1 件。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

from .engine import strip_template_comments

CODE = "R18"
MESSAGE = "CSRF の見出しは土台のテンプレートの 1 か所にだけ書く"
FIX = (
    "CSRF の見出し (X-CSRFToken) は core/templates/core/base.html の body の hx-headers の 1 か所にだけ書き、"
    "ほかのテンプレートと JS からは消してください (form には csrf_token の tag を使う)。"
)

_HEADER = re.compile(r"x-csrftoken", re.I)
_BASE_TEMPLATE = ("templates", "core", "base.html")


def applies(path: Path) -> bool:
    if path.suffix == ".html":
        return path.resolve().parts[-3:] != _BASE_TEMPLATE
    if path.suffix == ".js":
        return "vendor" not in path.parts
    return False


def find(tree: ast.Module) -> Iterator[int]:
    # .py は見ない (applies が偽)。
    return iter(())


def find_text(path: Path, text: str) -> Iterator[int]:
    if path.suffix == ".html":
        text = strip_template_comments(text)
    lines = {text.count("\n", 0, match.start()) + 1 for match in _HEADER.finditer(text)}
    yield from sorted(lines)
