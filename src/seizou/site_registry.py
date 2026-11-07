"""プラグインの MCP が起こしてよい会社の置き場の一覧 (~/.seizou/sites.json) を読み書きする。標準の library だけで書く。

一覧は JSON の 1 つの辞書で、鍵は置き場の絶対の道 (resolve した物)、値はその置き場を動かす python の絶対の道
(seizou new が置き場の README.md の起動の 1 行に書く sys.executable と同じ物)。
一覧の置き場は、環境変数 SEIZOU_HOME があればその下、無ければ ~/.seizou の下の sites.json
(試験は SEIZOU_HOME を一時のフォルダにし、本物の ~/.seizou に書かない)。
seizou new と seizou ai-docs が登録し (cli.register_site_for_plugin)、プラグインの起こし役 plugin/mcp/start.py が読む。
start.py は uv run --no-project で動き、この module を import できないので、道の決め方と JSON の形を写して持つ。
片方を変えるときは、もう片方も変える (tests/test_core_mcp_plugin.py が、この module で登録した置き場を start.py で起こす)。

訳: プラグインの MCP を、利用者が自分の手で作るか登録した置き場だけで起こすため。開いたフォルダの README.md から
python の道を読む形では、攻撃者が用意したフォルダ (manage.py・answers.yaml・起動の 1 行) を Claude Code で開くだけで、
その中の実行ファイルと manage.py が利用者の権限で動いた。一覧は利用者のホームの下にあり、開いたフォルダの中身では足せない。
出所: 本人の決め 11-08 01:0x (今夜作る) と、Claude Security の走査の候補 13 (plugin/mcp/start.py)。
変えてよい条件: 本人が決め直したとき、または Claude Code がプラグインの MCP サーバーを開いたフォルダごとに許す仕組みを持ったとき。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

#: 一覧の置き場を変える環境変数 (無ければ ~/.seizou)
HOME_ENV = "SEIZOU_HOME"

#: 一覧のファイルの名前
SITES_FILE = "sites.json"


def sites_path() -> Path:
    """一覧のファイルの道。SEIZOU_HOME があればその下、無ければ ~/.seizou の下。"""
    home = os.environ.get(HOME_ENV, "")
    return (Path(home).expanduser() if home else Path.home() / ".seizou") / SITES_FILE


def site_key(site: Path) -> str:
    """一覧の鍵 (置き場の resolve した絶対の道)。start.py も開いたフォルダを同じ形にして比べる。"""
    return str(Path(site).expanduser().resolve())


def load_sites(path: Path | None = None) -> dict[str, str]:
    """一覧を読む。ファイルが無ければ空の辞書。JSON が壊れているか形が違えば ValueError (黙って空にして上書きしない)。"""
    path = path or sites_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in data.items()):
        raise ValueError(f"置き場の一覧の形が違います: {path}")
    return data


def register_site(site: Path, python: str | None = None) -> Path:
    """置き場を一覧に登録する。同じ置き場は python を書き直す。書いた一覧の道を返す。

    python は既定でこの process の sys.executable (seizou new が起動の 1 行に書く物と同じ)。
    一時のファイルに書いてから置き換える (途中で止まっても一覧が半端にならない。持ち主だけが読み書きできる)。
    書けなければ OSError、今の一覧が壊れていれば ValueError (壊れた一覧を消さない)。
    """
    path = sites_path()
    sites = load_sites(path)
    sites[site_key(site)] = python or sys.executable
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".sites-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(sites, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return path
