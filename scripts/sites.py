#!/usr/bin/env python3
"""手元の会社の置き場 (seizou new が作った物) の開発のサーバーを、まとめて立てる・止める・立て直す・見る。

    uv run python scripts/sites.py status            立っているか (置き場ごとに port と pid)
    uv run python scripts/sites.py start [id ...]    立てる (立っている物は飛ばす)
    uv run python scripts/sites.py stop [id ...]     止める
    uv run python scripts/sites.py restart [id ...]  止めて立てる (取り込みの後に打つ。runserver は --noreload で立てるため)

置き場の親は環境変数 SEIZOU_SITES_DIR (無ければ ~/seizou-sites)。id を書かなければ、親の下の answers.yaml を持つ置き場の全部。
port は置き場の answers.yaml の port。python はこの repo の .venv の物 (README の起動の 1 行と同じ)。
runserver は --noreload で立て、出力は <親>/<id>.runserver.log に足す。止めるのは、その port で待ち受けている process だけ
(lsof -ti :<port> で探す。pkill のように名前では殺さない)。
作り手の運用の道具で、公開のプラグインには入れない。標準の library と、この repo の依存 (yaml) だけで書く。
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
START_WAIT_SECONDS = 20
STOP_WAIT_SECONDS = 5


@dataclass(frozen=True)
class Site:
    id: str
    path: Path
    port: int

    @property
    def log(self) -> Path:
        return self.path.parent / f"{self.id}.runserver.log"


def sites_dir() -> Path:
    return Path(os.environ.get("SEIZOU_SITES_DIR") or "~/seizou-sites").expanduser()


def venv_python() -> Path:
    """この repo の .venv の python (会社の置き場の README の起動の 1 行と同じ物)。"""
    python = REPO / ".venv" / "bin" / "python3"
    if not python.is_file():
        python = Path(sys.executable)
    return python


def read_port(answers: Path) -> int:
    data = yaml.safe_load(answers.read_text(encoding="utf-8")) or {}
    port = data.get("port")
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise ValueError(f"{answers} の port が読めません (1〜65535 の数が要る): {port!r}")
    return port


def find_sites(root: Path, ids: list[str]) -> list[Site]:
    """親の下の置き場。id を渡せばその物だけ (無ければ ValueError)。"""
    found: list[Site] = []
    folders = [root / site_id for site_id in ids] if ids else sorted(p for p in root.iterdir() if p.is_dir())
    for folder in folders:
        answers = folder / "answers.yaml"
        if not answers.is_file():
            if ids:
                raise ValueError(f"置き場が無いか answers.yaml が無い: {folder}")
            continue
        found.append(Site(id=folder.name, path=folder, port=read_port(answers)))
    return found


def port_is_open(port: int, host: str = HOST, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def listening_pids(port: int) -> list[int]:
    """その port で待ち受けている process の pid (lsof)。lsof が無ければ空。"""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return []
    return [int(line) for line in result.stdout.split() if line.strip().isdigit()]


def start_command(site: Site, python: Path) -> list[str]:
    return [str(python), "manage.py", "runserver", str(site.port), "--noreload"]


def start(site: Site, python: Path) -> str:
    if port_is_open(site.port):
        return f"{site.id}: port {site.port} は既に立っている (飛ばした)"
    with site.log.open("ab") as log:
        subprocess.Popen(
            start_command(site, python), cwd=site.path, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
    deadline = time.monotonic() + START_WAIT_SECONDS
    while time.monotonic() < deadline:
        if port_is_open(site.port):
            return f"{site.id}: port {site.port} で立った (記録 {site.log})"
        time.sleep(0.5)
    return f"{site.id}: port {site.port} が {START_WAIT_SECONDS} 秒たっても答えない (記録 {site.log} を見る)"


def stop(site: Site) -> str:
    pids = listening_pids(site.port)
    if not pids:
        return f"{site.id}: port {site.port} に process は無い"
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while time.monotonic() < deadline and listening_pids(site.port):
        time.sleep(0.2)
    left = listening_pids(site.port)
    if left:
        return f"{site.id}: port {site.port} の process {left} が止まらない"
    return f"{site.id}: port {site.port} を止めた (pid {', '.join(map(str, pids))})"


def status(site: Site) -> str:
    pids = listening_pids(site.port)
    if port_is_open(site.port):
        who = f"pid {', '.join(map(str, pids))}" if pids else "pid は読めない"
        return f"{site.id}: port {site.port} 立っている ({who})"
    return f"{site.id}: port {site.port} 止まっている"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="手元の会社の置き場の開発のサーバーを、まとめて立てる・止める・立て直す。")
    parser.add_argument("action", choices=("status", "start", "stop", "restart"))
    parser.add_argument("ids", nargs="*", help="置き場の id (書かなければ全部)")
    args = parser.parse_args(argv)
    root = sites_dir()
    if not root.is_dir():
        print(f"置き場の親が無い: {root} (環境変数 SEIZOU_SITES_DIR で変えられる)", file=sys.stderr)
        return 2
    try:
        sites = find_sites(root, args.ids)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not sites:
        print(f"置き場が無い: {root} の下に answers.yaml を持つフォルダが無い", file=sys.stderr)
        return 2
    python = venv_python()
    code = 0
    for site in sites:
        if args.action == "status":
            print(status(site))
        elif args.action == "start":
            print(start(site, python))
        elif args.action == "stop":
            print(stop(site))
        else:
            print(stop(site))
            line = start(site, python)
            print(line)
            if "答えない" in line:
                code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
