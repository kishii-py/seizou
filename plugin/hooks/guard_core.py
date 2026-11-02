"""AI が部品 (seizou) の中身を書き換えないように、Write と Edit を止める (R20。Claude Code の PreToolUse の hook)。

製品の hook。公開のプラグインに入る。登録は同じフォルダの hooks.json。

会社の置き場 (manage.py と answers.yaml の両方があるフォルダ) から開いた会話だけを見る。
会話の今のフォルダ (cwd) か、その上のフォルダのどれかが会社の置き場なら、書く先が部品の中身のときに止める。
部品の中身とは次の 2 つ。書く先のファイルやフォルダがまだ無くても、道の名前で決める。
  (a) 道に site-packages の直下の seizou のフォルダが入っている (入れた部品)。
  (b) 道の上のフォルダに、名前が seizou で中に parts.py を持つフォルダがある
      (編集できる形で入れた部品のソースと、会社の置き場の中の部品の写し)。
ほかの全部は通す。会社の置き場の fields/・templates/・checks.py・settings.py・manual/ と、
会社が足した app は、会社の違いと会社が足す画面を書く所なので通す。
会社の置き場でない所 (部品のソースのリポジトリなど) では何もしない。

止めるときは終わりの番号 2 で終わり、理由を標準エラーに日本語で出す (Claude Code が Claude に渡す)。
通すときは 0 で何も出さない。標準の library だけで書く。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXIT_ALLOW = 0
EXIT_BLOCK = 2

#: 見る道具 (Write と Edit だけ)
WRITE_TOOLS = frozenset({"Write", "Edit"})

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


def resolve_target(file_path: str, cwd: Path) -> Path:
    target = Path(file_path).expanduser()
    if not target.is_absolute():
        target = cwd / target
    return target.resolve()


def is_parts(target: Path) -> bool:
    """書く先が部品の中身か。道の区切りは Path.parts で見る (Windows でも同じに動く)。"""
    names = target.parts
    for index, name in enumerate(names[:-1]):
        if name == "site-packages" and names[index + 1] == PARTS_PACKAGE:
            return True
    return any(
        folder.name == PARTS_PACKAGE and (folder / PARTS_MARKER).is_file() for folder in target.parents
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
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path:
        return EXIT_ALLOW, ""

    cwd_value = data.get("cwd")
    cwd = Path(cwd_value) if isinstance(cwd_value, str) and cwd_value else Path.cwd()
    cwd = cwd.resolve()
    if find_company_root(cwd) is None:
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
