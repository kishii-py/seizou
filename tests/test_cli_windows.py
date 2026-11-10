"""命令 seizou (src/seizou/cli.py) を Windows で通すための試験 (精査 Q6 の 1・4・6・7 と精査 Q1 の 10。開発の記録 docs/nights/20261111/N392.md)。

Windows の実機は無いので、macOS の上で Windows の形を模す。OS の見分けは cli._on_windows を差し替える
(os.name そのものを nt にすると、その間に作る Path が WindowsPath になるため)。Windows の CI でも同じ試験が回る。
道は作り物 (作り物 の名前の利用者と置き場)。
"""

from __future__ import annotations

import sys
from pathlib import Path

from seizou import cli
from tests.test_cli_new import FIXTURE, run_new

POSIX_PYTHON = "/opt/作り物/.venv/bin/python"
WINDOWS_PYTHON = r"C:\Users\作り物\seizou\.venv\Scripts\python.exe"
WINDOWS_SITE = r"C:\Users\作り物\seizou-sites\a_machining"


# 起動の 1 行と、置き場で打つ manage.py の 1 行 (精査 Q6 の 1・精査 Q1 の 10)


def test_start_line_on_posix_keeps_the_current_shape(monkeypatch):
    # macOS と Linux では今の形 (cd <置き場> && <python> manage.py runserver <port>) を 1 字も変えない
    monkeypatch.setattr(cli, "_on_windows", lambda: False)
    monkeypatch.setattr(sys, "executable", POSIX_PYTHON)
    site = Path("/tmp/sites/a_machining")
    assert cli.start_line(site, 8001) == f"cd {site} && {POSIX_PYTHON} manage.py runserver 8001"
    spaced = Path("/tmp/a b/a_machining")
    assert cli.start_line(spaced, 8001) == f'cd "{spaced}" && {POSIX_PYTHON} manage.py runserver 8001'


def test_start_line_on_windows_is_powershell_shape(monkeypatch):
    # Windows PowerShell 5.1 は && を区切りと読まない。; で 2 つの命令に分け、引用符で始まる python の前に & を置く
    monkeypatch.setattr(cli, "_on_windows", lambda: True)
    monkeypatch.setattr(sys, "executable", WINDOWS_PYTHON)
    line = cli.start_line(Path(WINDOWS_SITE), 8001)
    assert line == rf'cd "{WINDOWS_SITE}"; & "{WINDOWS_PYTHON}" manage.py runserver 8001'
    assert "&&" not in line


def test_manage_line_follows_the_os(monkeypatch):
    monkeypatch.setattr(cli, "_on_windows", lambda: True)
    monkeypatch.setattr(sys, "executable", WINDOWS_PYTHON)
    assert cli.manage_line("seizou_practice start") == rf'& "{WINDOWS_PYTHON}" manage.py seizou_practice start'
    monkeypatch.setattr(cli, "_on_windows", lambda: False)
    monkeypatch.setattr(sys, "executable", POSIX_PYTHON)
    assert cli.manage_line("seizou_practice start") == f"{POSIX_PYTHON} manage.py seizou_practice start"


def test_on_windows_reads_os_name(monkeypatch):
    # os.name を差し替えた間は Path を作らない (nt の間の Path は WindowsPath になる)。答えは区切りの外で比べる
    with monkeypatch.context() as patch:
        patch.setattr(cli.os, "name", "nt")
        on_nt = cli._on_windows()
        patch.setattr(cli.os, "name", "posix")
        on_posix = cli._on_windows()
    assert on_nt is True
    assert on_posix is False


def test_new_report_practice_line_has_python_on_windows(tmp_path, monkeypatch):
    # 練習の案内も起動の 1 行と同じ python の道を持つ (前は「置き場で manage.py seizou_practice start」で、そのまま打つと見つからない)
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path / "sites"))
    monkeypatch.setattr(cli, "_on_windows", lambda: True)
    code, out, err = run_new(["new", str(FIXTURE)])
    assert code == 0, err
    practice = [line for line in out.splitlines() if line.startswith("練習するときは:")]
    assert len(practice) == 1, out
    assert '& "' in practice[0]
    assert f'& "{sys.executable}" manage.py seizou_practice start' in practice[0]
