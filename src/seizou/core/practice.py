"""練習モード。命令 seizou_practice の中身。

置き場の DB (SQLite) と上げたファイルを、控えと同じ backup.take_backup で practice/<日時>/ に写し、
写しの道を環境変数で渡した runserver を別のポートで立てる。立てる前に、置き場の settings.py が写しを読むかを
子の process で確かめる (読まなければ立てない = 帯が本番に残らないと言いながら本番に書く形を作らない)。
止めるときは記録の pid だけを止め、practice/ の下の写しだけを消す。
本番の process の中で DB を切り替える形は作らない。PostgreSQL の置き場では使えない。
表に書く処理は無い。部品を import しない。子の process に渡す環境変数の辞書は、文にも記録にも書かない (鍵が入っている)。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings

from seizou.core import backup

#: 置き場の下の写しの置き場の名前
PRACTICE_DIR = "practice"
#: 写しのフォルダの中の記録 (pid・ポート・URL・立てた日時) と runserver の出力
RECORD = "practice.json"
LOG = "runserver.log"
#: 子の process に渡す環境変数の名前 (雛形の settings.py の塊と同じ字)
ENV_FLAG = "SEIZOU_PRACTICE"
ENV_DB = "SEIZOU_PRACTICE_DB"
ENV_MEDIA = "SEIZOU_PRACTICE_MEDIA"
#: 練習のサーバーの host と、既定のポート (会社のポート + 100)
HOST = "127.0.0.1"
PORT_OFFSET = 100
#: 立つのと止まるのを待つ秒 (scripts/sites.py と同じ) と、失敗の文に添える出力の末尾の字数
START_WAIT_SECONDS = 20
STOP_WAIT_SECONDS = 5
_TAIL = 300

POSTGRES_GUIDE = "練習モードは SQLite の置き場だけ。PostgreSQL は控えを別の DB に戻して立ててください。"
SETTINGS_GUIDE = (
    "この置き場の settings.py は、練習の写しを読む塊をまだ持っていないので、練習のサーバーを立てませんでした (写しは消しました)。"
    "docs/operations.md の「練習モード」の塊を、settings.py の MEDIA_ROOT の行の後に足してから、もう 1 度打ってください。"
)
#: 立てる前の確かめの子の process に打たせる Python の 1 行 (DB の名前と MEDIA_ROOT を 1 行ずつ出す)
PROBE = 'from django.conf import settings; print(settings.DATABASES["default"]["NAME"]); print(settings.MEDIA_ROOT)'


class PracticeError(Exception):
    """練習モードを止める理由 (日本語の文)。命令が CommandError にする。"""


def is_practice() -> bool:
    """練習のサーバー (環境変数 SEIZOU_PRACTICE が 1) で動いているか。context processor が全部の画面で呼ぶ。"""
    return os.environ.get(ENV_FLAG) == "1"


def practice_root() -> Path:
    """練習の写しの置き場 (置き場の practice/)。"""
    base = getattr(settings, "BASE_DIR", None)
    if not base:
        raise PracticeError("settings に BASE_DIR が無いので、練習の写しの置き場を決められません。")
    return Path(base) / PRACTICE_DIR


def default_port() -> int:
    """既定のポート (settings の SEIZOU の port + PORT_OFFSET)。会社のポートが無ければ PracticeError。"""
    seizou = getattr(settings, "SEIZOU", {}) or {}
    port = seizou.get("port") if isinstance(seizou, dict) else None
    if isinstance(port, int):
        return port + PORT_OFFSET
    raise PracticeError("会社のポートが settings の SEIZOU に無いので、--port で練習のサーバーのポートを指定してください。")


def port_is_open(port: int) -> bool:
    """そのポートで待ち受けている process があるか (scripts/sites.py と同じ形)。試験が差し替える。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((HOST, port)) == 0


def practice_env(folder: Path) -> dict:
    """子の process に渡す環境変数 (今の環境変数に、練習の印と写しの DB と写しの上げたファイルの道を足した物)。"""
    return {
        **os.environ,
        ENV_FLAG: "1",
        ENV_DB: str(folder / backup.DB_FILES["sqlite"]),
        ENV_MEDIA: str(folder / backup.MEDIA_DIR),
    }


def verify_settings(base: Path, env: dict, db_path: Path, media_path: Path) -> bool:
    """置き場の settings.py が、写しの環境変数で写しの DB と写しの上げたファイルを読むか。試験が差し替える。

    置き場の manage.py で shell を 1 回打ち、DB の名前と MEDIA_ROOT を出させて比べる。
    子の process が失敗したら PracticeError。読まない (塊が無い) なら偽。
    """
    result = subprocess.run(
        [sys.executable, str(base / "manage.py"), "shell", "--no-imports", "-c", PROBE],
        cwd=base,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        tail = (result.stderr or "").strip()[-_TAIL:]
        raise PracticeError(f"練習の settings の確かめが失敗しました (終わりの番号 {result.returncode})。{tail}")
    lines = (result.stdout or "").strip().splitlines()
    if len(lines) < 2:
        return False
    return Path(lines[-2]).resolve() == db_path.resolve() and Path(lines[-1]).resolve() == media_path.resolve()


def launch(args: list[str], *, cwd: Path, env: dict, log: Path) -> subprocess.Popen:
    """runserver を別の process として立てる (出力は log に足す。命令を打った端末を閉じても残る)。試験が差し替える。"""
    with log.open("ab") as out:
        return subprocess.Popen(args, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)


def wait_ready(proc, port: int) -> bool:
    """START_WAIT_SECONDS の間、ポートが開くのを待つ。子が終わったらすぐ偽。時間切れも偽。試験が差し替える。"""
    deadline = time.monotonic() + START_WAIT_SECONDS
    while time.monotonic() < deadline:
        if port_is_open(port):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def terminate(pid: int) -> None:
    """記録の pid に SIGTERM を送る (既に無いか、送れない process は飛ばす)。試験が差し替える。"""
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def _log_tail(log: Path) -> str:
    try:
        text = log.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return text.strip()[-_TAIL:]


def start(port: int | None = None) -> dict:
    """写しを取り、練習のサーバーを立てる。返す物 = 記録 (pid・port・url・started_at) と folder・log・ready。

    ready が偽 = 時間切れ (子と写しと記録は残す。stop で止められる)。子が終わったときは写しを消して PracticeError。
    """
    kind = backup.database_kind()
    if kind == "postgresql":
        raise PracticeError(POSTGRES_GUIDE)
    if kind != "sqlite":
        raise PracticeError(f"練習モードは SQLite の置き場だけ。今の DB ({kind}) では使えません。")
    root = practice_root()
    base = root.parent
    manage = base / "manage.py"
    if not manage.is_file():
        raise PracticeError(f"置き場の manage.py が見つかりません: {manage} (練習モードは seizou new が作った置き場で打ちます)。")
    port = port or default_port()
    if port_is_open(port):
        raise PracticeError(
            f"ポート {port} は既に使われています。練習のサーバーが立っていれば seizou_practice status で見られます。"
            "別のポートで立てるなら --port で指定してください。"
        )

    try:
        copy = backup.take_backup(root)
    except backup.BackupError as exc:
        raise PracticeError(str(exc)) from exc
    folder = copy["folder"]

    # ここから立てるまでの失敗は、作ったばかりの写し (take_backup が返したフォルダ) を消してから上げ直す
    try:
        env = practice_env(folder)
        db_path = Path(env[ENV_DB])
        media_path = Path(env[ENV_MEDIA])
        if not verify_settings(base, env, db_path, media_path):
            raise PracticeError(SETTINGS_GUIDE)
        proc = launch(
            [sys.executable, str(manage), "runserver", str(port), "--noreload", "--insecure"],
            cwd=base,
            env=env,
            log=folder / LOG,
        )
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise

    record = {
        "pid": proc.pid,
        "port": port,
        "url": f"http://{HOST}:{port}/",
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    (folder / RECORD).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ready = wait_ready(proc, port)
    if not ready:
        code = proc.poll()
        if code is not None:
            tail = _log_tail(folder / LOG)
            shutil.rmtree(folder, ignore_errors=True)
            raise PracticeError(f"練習のサーバーが立ちませんでした (終わりの番号 {code})。{tail}")
    return {**record, "folder": folder, "log": folder / LOG, "ready": ready}


def _positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def read_record(folder: Path) -> dict | None:
    """写しのフォルダの practice.json。無い・JSON でない・辞書でない・pid と port が正の int でない → None。"""
    try:
        data = json.loads((Path(folder) / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not _positive_int(data.get("pid")) or not _positive_int(data.get("port")):
        return None
    return data


def copies() -> list[Path]:
    """practice/ の下の、名前が日時の形のフォルダ (名前の順)。practice/ が無ければ []。"""
    root = practice_root()
    if not root.is_dir():
        return []
    found = [path for path in root.iterdir() if path.is_dir() and backup.NAME_PATTERN.match(path.name)]
    return sorted(found, key=lambda path: path.name)


def status() -> list[dict]:
    """記録のある写しごとに、記録と folder と running (記録のポートが開いているか)。"""
    rows = []
    for folder in copies():
        record = read_record(folder)
        if record is None:
            continue
        rows.append({**record, "folder": folder, "running": port_is_open(record["port"])})
    return rows


def _wait_closed(port: int) -> bool:
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while port_is_open(port):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)
    return True


def stop() -> list[dict]:
    """練習のサーバーを止めて写しを消す。写しごとに 1 行の辞書を返す。

    止めるのは記録の pid だけで、記録のポートが開いているときだけ送る。止まらなければ写しを消さない (left)。
    記録の無いフォルダは消さない (skipped)。practice/ のフォルダそのものは残す。
    """
    root = practice_root().resolve()
    rows = []
    for folder in copies():
        if folder.resolve().parent != root:
            continue
        record = read_record(folder)
        if record is None:
            rows.append({"folder": folder, "skipped": True})
            continue
        running = port_is_open(record["port"])
        if running:
            terminate(record["pid"])
            if not _wait_closed(record["port"]):
                rows.append({"folder": folder, "pid": record["pid"], "port": record["port"], "left": True})
                continue
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            raise PracticeError(
                f"写しを消せません: {folder} ({exc.strerror})。"
                "サーバーがまだ動いていれば、少し待ってからもう 1 度 seizou_practice stop を打ってください。"
            ) from exc
        rows.append({"folder": folder, "pid": record["pid"], "port": record["port"], "stopped": running})
    return rows
