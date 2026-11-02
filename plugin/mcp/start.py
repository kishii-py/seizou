"""プラグインの MCP サーバー seizou の起こし役 (同じプラグインの .mcp.json が uv で呼ぶ)。

製品の script。公開のプラグインに入る。標準の library だけで書く。

会社の置き場には pyproject.toml が無く、置き場の python は seizou を入れた場所の python。
そこで起こし役は、置き場の README.md の起動の 1 行 (seizou new が書く cd <置き場> && <python> manage.py runserver <port>)
から <python> を読み、その python で置き場の manage.py seizou_mcp を打つ。

- 置き場: 環境変数 CLAUDE_PROJECT_DIR があればそのフォルダ、無ければ今のフォルダ。
  manage.py と answers.yaml の両方が無ければ、標準エラーに 1 行を書いて番号 1 で終わる。
- 標準入力と標準出力はそのまま子に渡す (MCP の通信)。起こし役は標準出力に 1 字も書かず、標準入力を読まない。
  知らせは標準エラーに 1 行だけ書く。
- 利用者は渡さない (子の命令が置き場の .env の SEIZOU_MCP_USER を読む)。起こし役が受けた引数は、子にそのまま渡す。
- 子の終わりの番号で終わる。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: 会社の置き場の印 (両方があるフォルダ)
COMPANY_MARKERS = ("manage.py", "answers.yaml")

#: 起動の 1 行の、python の後に続く字
START_TAIL = " manage.py runserver"

OUTSIDE_SITE_MESSAGE = "会社の置き場 (manage.py と answers.yaml のあるフォルダ) で Claude Code を開くと、seizou の道具が使えます。"
NO_START_LINE_MESSAGE = (
    "会社の置き場の README.md に、起動の 1 行 (cd <置き場> && <python> manage.py runserver <port>) が読めません。"
    "seizou new で置き場を作り直してください。"
)

# Windows の標準エラー (cp1252) で日本語の文が落ちないように utf-8 で書く。
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def site_dir() -> Path:
    project = os.environ.get("CLAUDE_PROJECT_DIR", "")
    return Path(project) if project else Path.cwd()


def is_site(folder: Path) -> bool:
    return all((folder / name).is_file() for name in COMPANY_MARKERS)


def site_python(folder: Path) -> str | None:
    """置き場の README.md の起動の 1 行の python。読めなければ None。"""
    try:
        text = (folder / "README.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        line = line.strip()
        if "&&" not in line or START_TAIL not in line:
            continue
        python = line.split("&&", 1)[1].split(START_TAIL, 1)[0].strip()
        if len(python) >= 2 and python.startswith('"') and python.endswith('"'):
            python = python[1:-1]
        if python:
            return python
    return None


def tell(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def main(argv: list[str]) -> int:
    folder = site_dir()
    if not is_site(folder):
        tell(OUTSIDE_SITE_MESSAGE)
        return 1
    python = site_python(folder)
    if python is None:
        tell(NO_START_LINE_MESSAGE)
        return 1
    try:
        completed = subprocess.run([python, "manage.py", "seizou_mcp", *argv], cwd=folder, check=False)
    except OSError:
        tell(f"起動の 1 行の python ({python}) を起こせません。seizou を入れ直すか、seizou new で置き場を作り直してください。")
        return 1
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
