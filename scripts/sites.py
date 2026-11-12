#!/usr/bin/env python3
"""手元の会社の置き場 (seizou new が作った物) の開発のサーバーを、まとめて立てる・止める・立て直す・見る。

    uv run python scripts/sites.py status            立っているか (置き場ごとに port と pid)
    uv run python scripts/sites.py start [id ...]    立てる (立っている物は飛ばす)
    uv run python scripts/sites.py stop [id ...]     止める
    uv run python scripts/sites.py restart [id ...]  止めて立てる (取り込みの後に打つ。runserver は --noreload で立てるため)

置き場の親は環境変数 SEIZOU_SITES_DIR (無ければ ~/seizou-sites)。id を書かなければ、親の下の answers.yaml を持つ置き場の全部。
port は置き場の answers.yaml の port。python はこの repo の .venv の物 (README の起動の 1 行と同じ)。
runserver は --noreload で立て、出力は <親>/<id>.runserver.log に足す。止めるのは、その port で待ち受けている process だけ
(lsof -ti :<port> で探す。pkill のように名前では殺さない)。lsof が無い PC (Windows) では、この道具が立てたときに
<親>/<id>.runserver.pid に書いた pid の記録で止める (lsof の命令が無く、port が開いていて、記録が在るときだけ)。
作り手の運用の道具で、公開のプラグインには入れない。標準の library と、この repo の依存 (yaml) だけで書く。
"""

from __future__ import annotations

import argparse
import os
import shutil
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

    @property
    def pid_file(self) -> Path:
        return self.path.parent / f"{self.id}.runserver.pid"


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
        child = subprocess.Popen(
            start_command(site, python), cwd=site.path, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
    pid = getattr(child, "pid", None)  # Popen を差し替えた試験の偽は pid を持たない物を返す
    if isinstance(pid, int):
        site.pid_file.write_text(f"{pid}\n", encoding="utf-8")
    deadline = time.monotonic() + START_WAIT_SECONDS
    while time.monotonic() < deadline:
        if port_is_open(site.port):
            return f"{site.id}: port {site.port} で立った (記録 {site.log})"
        time.sleep(0.5)
    return f"{site.id}: port {site.port} が {START_WAIT_SECONDS} 秒たっても答えない (記録 {site.log} を見る)"


def stop(site: Site) -> str:
    pids = listening_pids(site.port)
    if not pids:
        return stop_recorded(site)
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
    site.pid_file.unlink(missing_ok=True)
    return f"{site.id}: port {site.port} を止めた (pid {', '.join(map(str, pids))})"


def recorded_pid(site: Site) -> int | None:
    """この道具が立てたときに書いた pid (記録が無いか、数でなければ None)。"""
    try:
        text = site.pid_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def lsof_missing() -> bool:
    """lsof の命令そのものが無い PC か (Windows)。在るのに待ち受けの process を答えない時とは分ける。"""
    return shutil.which("lsof") is None


def stop_recorded(site: Site) -> str:
    """lsof が pid を答えないとき。lsof の命令が無い PC で、port が開いていて、この道具が立てた記録の pid があれば、その pid にだけ送る。

    訳: lsof の無い PC (Windows) で restart が古いサーバーを止めずに成功の形の文を出したため (精査 Q6 の 8、本体の決め
    11-11 14:2x)。記録の pid は後で別の process が使うことがあるので、lsof の在る PC では使わない。lsof が在るのに
    待ち受けの process を答えないのは、ほかの利用者の process などで、記録の pid の物とは限らないため (本体の直し 11-12)。
    """
    if not port_is_open(site.port):
        return f"{site.id}: port {site.port} に process は無い"
    if not lsof_missing():
        return (
            f"{site.id}: port {site.port} は開いているが、止める process が分からない"
            " (lsof が待ち受けの process を答えない。記録の pid は使わない)"
        )
    pid = recorded_pid(site)
    if pid is None:
        return (
            f"{site.id}: port {site.port} は開いているが、止める process が分からない"
            " (lsof が無く、この道具が立てた記録も無い)"
        )
    try:
        os.kill(pid, signal.SIGTERM)  # Windows の Python では TerminateProcess になる
    except OSError:  # ProcessLookupError を含む。Windows は無い pid に OSError を出す
        pass
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while time.monotonic() < deadline and port_is_open(site.port):
        time.sleep(0.2)
    if port_is_open(site.port):
        return f"{site.id}: port {site.port} の process (pid {pid}。立てた時の記録) が止まらない"
    site.pid_file.unlink(missing_ok=True)
    return f"{site.id}: port {site.port} を止めた (pid {pid}。立てた時の記録)"


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
            line = stop(site)
            print(line)
            if "分からない" in line or "止まらない" in line:  # 止められないまま立て直したと出さない
                code = 1
                continue
            line = start(site, python)
            print(line)
            if "答えない" in line:
                code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
