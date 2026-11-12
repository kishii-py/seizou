"""プラグインの skill の本文 (connect と manual の SKILL.md) が、置き場の README.md の起動の 1 行を 2 つの形で書くかを確かめる。

起動の 1 行は src/seizou/cli.py の start_line が書く (N392)。macOS と Linux では cd <置き場> && <python> manage.py runserver <port>、
Windows では cd "<置き場>"; & "<python>" manage.py runserver <port> の形になる。connect と manual は、この 1 行から <python> を読む。

訳: SKILL.md が macOS と Linux の形だけを書くと、Windows の置き場で Claude が README の字から python の所を読み違えうる。
PowerShell では manage.py の命令も & "<python>" manage.py … の形でないと打てない (docs/getting-started.md と同じ事)。
期待の字は cli の start_line と manage_line から作るので、start_line の形が変われば、この試験が落ちて SKILL.md も直すことになる。
変えてよい条件: 起動の 1 行を書く関数が cli から移ったとき、字を作る元をその関数に替える (出所は依頼書 N429・本体の決め 11-11 14:2x)。

tests/test_plugin_skills.py は標準の library だけで動く約束なので、seizou.cli を読むこの試験は別のファイルに置く。seizou.cli は Django を読まない。
OS の見分けは tests/test_cli_windows.py と同じく cli._on_windows を差し替える。
"""

from __future__ import annotations

import sys
from pathlib import Path

from seizou import cli

SKILLS_DIR = Path(__file__).resolve().parents[1] / "plugin" / "skills"

# 起動の 1 行の形を書いている skill
SKILLS = ("connect", "manual")


def _lines(monkeypatch, on_windows: bool) -> tuple[str, str]:
    """OS の見分けと python の道を差し替えて、起動の 1 行と manage.py の 1 行を cli に作らせる。"""
    monkeypatch.setattr(cli, "_on_windows", lambda: on_windows)
    monkeypatch.setattr(sys, "executable", "<python>")
    return cli.start_line(Path("<置き場>"), "<port>"), cli.manage_line("…")


def test_skills_show_both_start_line_forms_of_cli(monkeypatch):
    windows_start, windows_manage = _lines(monkeypatch, on_windows=True)
    posix_start, _ = _lines(monkeypatch, on_windows=False)

    for name in SKILLS:
        text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        assert posix_start in text, f"{name} の SKILL.md に macOS と Linux の起動の 1 行 ({posix_start}) が無い"
        assert windows_start in text, f"{name} の SKILL.md に Windows の起動の 1 行 ({windows_start}) が無い"
        # 頭 (& "<python>" manage.py) は起動の 1 行にも在るので、案内の 1 文の字 (& "<python>" manage.py …) の全部で見る
        assert windows_manage in text, f"{name} の SKILL.md に Windows の manage.py の案内の形 ({windows_manage}) が無い"
