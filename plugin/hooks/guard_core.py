"""AI が部品 (seizou) の中身を書き換えないように、Write・Edit・NotebookEdit を止める (R20。Claude Code の PreToolUse の hook)。

製品の hook。公開のプラグインに入る。登録は同じフォルダの hooks.json。

会社の置き場 (manage.py と answers.yaml の両方があるフォルダ) から開いた会話だけを見る。
環境変数 CLAUDE_PROJECT_DIR (Claude Code を開いたフォルダ) を先に見て、無いか置き場でなければ会話の今のフォルダ (cwd) を見る。
どちらかか、その上のフォルダのどれかが会社の置き場なら、書く先が部品の中身のときに止める
(cd で置き場の外へ移った後の書き込みも止めるため。決め 11-08 00:5x)。
書く先は Write と Edit の file_path、NotebookEdit の notebook_path。相対の道は cwd から数える。
部品の中身とは次の 2 つ。書く先のファイルやフォルダがまだ無くても、道の名前で決める。
道の名前は小文字にそろえて比べる (macOS と Windows は大文字と小文字を区別せずに同じファイルを開くので、SEIZOU と書いた道で抜けさせない)。
  (a) 道に site-packages の直下の seizou のフォルダが入っている (入れた部品)。
  (b) 道の上のフォルダに、名前が seizou で中に parts.py を持つフォルダがある
      (編集できる形で入れた部品のソースと、会社の置き場の中の部品の写し)。
ほかの全部は通す。会社の置き場の fields/・templates/・checks.py・settings.py・manual/ と、
会社が足した app は、会社の違いと会社が足す画面を書く所なので通す。
会社の置き場でない所 (部品のソースのリポジトリなど) では何もしない。
Read は見ない (置き場の .claude/settings.json の deny で止める。Read のたびに uv が起きる遅さを避ける。決め 11-08 00:5x)。
Bash の道具での書き込みも見ない (限りは SECURITY.md)。

止めるときは終わりの番号 2 で終わり、理由を標準エラーに日本語で出す (Claude Code が Claude に渡す)。
通すときは 0 で何も出さない。標準の library だけで書く。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

EXIT_ALLOW = 0
EXIT_BLOCK = 2

#: 見る道具 (Write・Edit・NotebookEdit)
WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})

#: 書く先の道を持つ鍵 (Write と Edit は file_path、NotebookEdit は notebook_path)
PATH_KEYS = ("file_path", "notebook_path")

#: 会社の置き場の印 (両方があるフォルダ)
COMPANY_MARKERS = ("manage.py", "answers.yaml")

#: 部品の package の名前と、部品のソースの置き場の印
PARTS_PACKAGE = "seizou"
PARTS_MARKER = "parts.py"


# Windows の標準出力 (cp1252) で日本語の文が落ちないように utf-8 で書く。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def read_input(stream) -> dict:
    try:
        data = json.loads(stream.read() or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def find_company_root(start: Path) -> Path | None:
    """start か、その上のフォルダのうち、会社の置き場の印が両方あるフォルダ。無ければ None。"""
    for folder in (start, *start.parents):
        if all((folder / name).is_file() for name in COMPANY_MARKERS):
            return folder
    return None


def company_root(cwd: Path) -> Path | None:
    """会社の置き場。CLAUDE_PROJECT_DIR (Claude Code を開いたフォルダ) を先に見て、見つからなければ cwd から探す。無ければ None。

    訳: 会話の中で cd して cwd が置き場の外 (部品のソースなど) に移っても、開いた置き場の会話なら守りを外さないため。
    出所: 本人の決め 11-08 00:5x と、仕組みの調べの候補 7 (hook の抜け道)。変えてよい条件: Claude Code が開いたフォルダを別の形で渡すようになったとき。
    """
    project = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if project:
        root = find_company_root(Path(project).expanduser().resolve())
        if root is not None:
            return root
    return find_company_root(cwd)


def resolve_target(file_path: str, cwd: Path) -> Path:
    target = Path(file_path).expanduser()
    if not target.is_absolute():
        target = cwd / target
    return target.resolve()


def is_parts(target: Path) -> bool:
    """書く先が部品の中身か。道の区切りは Path.parts で見る (Windows でも同じに動く)。

    訳: 名前は小文字にそろえて比べる。大文字と小文字を区別しないファイルの置き場 (macOS・Windows) で、
    SITE-PACKAGES/SEIZOU のように書いた道が同じ部品を指したまま == の比べを抜けるのを止めるため。
    出所: 仕組みの調べの候補 7 (hook の抜け道)。変えてよい条件: 部品の package の名前に大文字を使うようになったとき。
    """
    names = [name.lower() for name in target.parts]
    for index, name in enumerate(names[:-1]):
        if name == "site-packages" and names[index + 1] == PARTS_PACKAGE:
            return True
    return any(
        folder.name.lower() == PARTS_PACKAGE and (folder / PARTS_MARKER).is_file() for folder in target.parents
    )


def block_message(target: Path) -> str:
    return (
        f"書き込みを止めた (R20): {target} は部品 (seizou) の中身です。"
        "AI は部品の中身を書き換えません。"
        "会社ごとの違いは、会社の置き場の fields/・templates/・checks.py・settings.py で出してください。"
        "部品に足りない物は、担当者に伝えてください。"
    )


def decide(data: dict) -> tuple[int, str]:
    if data.get("tool_name") not in WRITE_TOOLS:
        return EXIT_ALLOW, ""
    tool_input = data.get("tool_input")
    file_path = None
    if isinstance(tool_input, dict):
        for key in PATH_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                file_path = value
                break
    if file_path is None:
        return EXIT_ALLOW, ""

    cwd_value = data.get("cwd")
    cwd = Path(cwd_value) if isinstance(cwd_value, str) and cwd_value else Path.cwd()
    cwd = cwd.resolve()
    if company_root(cwd) is None:
        return EXIT_ALLOW, ""

    target = resolve_target(file_path, cwd)
    if is_parts(target):
        return EXIT_BLOCK, block_message(target)
    return EXIT_ALLOW, ""


def main() -> int:
    code, message = decide(read_input(sys.stdin))
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
