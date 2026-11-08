"""プラグインの MCP サーバー seizou の起こし役 (同じプラグインの .mcp.json が uv で呼ぶ)。

製品の script。公開のプラグインに入る。標準の library だけで書く (uv run --no-project で動き、seizou を import できない)。

会社の置き場には pyproject.toml が無く、置き場の python は seizou を入れた場所の python。
起こし役は、置き場の一覧 (~/.seizou/sites.json。環境変数 SEIZOU_HOME があればその下) に登録した置き場だけで、
一覧の python で置き場の manage.py seizou_mcp を打つ。一覧は seizou new と seizou ai-docs が書く
(書く側は src/seizou/site_registry.py。道の決め方と JSON の形 = 置き場の resolve した絶対の道 → python の絶対の道 を、ここに写して持つ)。

訳: 開いたフォルダの README.md から python の道を読まない。読む形では、攻撃者が用意したフォルダ (manage.py・answers.yaml・
README.md の起動の 1 行) を Claude Code で開くだけで、その中の実行ファイルと manage.py が利用者の権限で動いたため。
一覧は利用者のホームの下にあり、開いたフォルダの中身では足せない。
出所: 本人の決め 11-08 01:0x (今夜作る) と、Claude Security の走査の指摘。
変えてよい条件: 本人が決め直したとき、または Claude Code がプラグインの MCP サーバーを開いたフォルダごとに許す仕組みを持ったとき。

- 置き場: 環境変数 CLAUDE_PROJECT_DIR があればそのフォルダ、無ければ今のフォルダ。resolve した絶対の道で一覧と比べる。
  一覧に無ければ、標準エラーに 1 行を書いて番号 1 で終わる (manage.py と answers.yaml があれば登録の仕方、無ければ置き場で開く案内)。
  一覧にあっても manage.py と answers.yaml が無ければ (置き場を消した後など)、置き場で開く案内を書いて番号 1 で終わる。
- 標準入力と標準出力はそのまま子に渡す (MCP の通信)。起こし役は標準出力に 1 字も書かず、標準入力を読まない。
  知らせは標準エラーに 1 行だけ書く。
- 利用者は渡さない (子の命令が置き場の .env の SEIZOU_MCP_USER を読む)。起こし役が受けた引数は、子にそのまま渡す。
- 子の終わりの番号で終わる。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

#: 会社の置き場の印 (両方があるフォルダ)
COMPANY_MARKERS = ("manage.py", "answers.yaml")

#: 置き場の一覧の置き場を変える環境変数と、一覧のファイルの名前 (src/seizou/site_registry.py と同じ)
HOME_ENV = "SEIZOU_HOME"
SITES_FILE = "sites.json"

OUTSIDE_SITE_MESSAGE = "会社の置き場 (manage.py と answers.yaml のあるフォルダ) で Claude Code を開くと、seizou の道具が使えます。"
NOT_REGISTERED_MESSAGE = "この置き場は登録されていません。`seizou ai-docs <置き場>` で登録してください。"

# Windows の標準エラー (cp1252) で日本語の文が落ちないように utf-8 で書く。
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def site_dir() -> Path:
    project = os.environ.get("CLAUDE_PROJECT_DIR", "")
    return (Path(project) if project else Path.cwd()).expanduser().resolve()


def is_site(folder: Path) -> bool:
    return all((folder / name).is_file() for name in COMPANY_MARKERS)


def sites_path() -> Path:
    """置き場の一覧のファイルの道 (src/seizou/site_registry.sites_path と同じ)。"""
    home = os.environ.get(HOME_ENV, "")
    return (Path(home).expanduser() if home else Path.home() / ".seizou") / SITES_FILE


def registered_python(folder: Path) -> str | None:
    """一覧にある置き場 folder (resolve した絶対の道) の python。一覧に無いか、一覧が読めなければ None。"""
    try:
        sites = json.loads(sites_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    python = sites.get(str(folder)) if isinstance(sites, dict) else None
    return python if isinstance(python, str) and python else None


def tell(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def main(argv: list[str]) -> int:
    folder = site_dir()
    python = registered_python(folder)
    if python is None:
        tell(NOT_REGISTERED_MESSAGE if is_site(folder) else OUTSIDE_SITE_MESSAGE)
        return 1
    if not is_site(folder):
        tell(OUTSIDE_SITE_MESSAGE)
        return 1
    try:
        completed = subprocess.run([python, "manage.py", "seizou_mcp", *argv], cwd=folder, check=False)
    except OSError:
        tell(f"置き場の一覧の python ({python}) を起こせません。seizou を入れ直してから、seizou ai-docs {folder} で登録し直してください。")
        return 1
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
