"""注文書の見張り。フォルダに置いた注文書 (PDF・PNG・JPEG) を古い順に読み取り、受信した注文に入口「読み取り」の行で並べる。

命令 seizou_intake_watch は引数を読んで結果を書くだけで、処理はこの module に置く (seizou_practice と core/practice.py の分け方)。

    manage.py seizou_intake_watch [<フォルダ>] [--interval 秒] [--once] [--user ログイン ID]
    manage.py seizou_intake_watch --status
    manage.py seizou_intake_watch --stop

ファイルの動かし方: 見るのはフォルダの直下のファイルだけ (done/・failed/ とほかのフォルダは見ない)。拡張子が EXTENSIONS の物を、
最後に書いた時刻から SETTLE_SECONDS たった物だけ、古い順に 1 つずつ intake.services.extract_to_inbox で読む。
読んだ物 (同じ中身で読まなかった物も) は <フォルダ>/done/ へ、読めなかった物は <フォルダ>/failed/ へ動かし、
理由を failed/<動かした名前>.txt に 1 行で書く。同じ名前があれば <名前の幹>-2<拡張子>・-3… の空いている名前にする。
ファイルは with を抜けてから動かす (Windows は開いたままのファイルを動かせない)。外の library (watchdog) を使わず、
見回りの間 (interval) ごとに os.scandir で見る。1 日の上限 (設定 ai_limits の watch_per_day) を超えた物は読まずに置いたまま (動かさない)。次の日に読む。

見張りの記録: 置き場 (settings の BASE_DIR) に intake_watch.json を 1 本だけ置く。中身は pid・フォルダ・間・始めた日時・最後の見回り。
動いているかは最後の見回りの時刻で見る (max(3 × 間, STALE_SECONDS) 秒より新しい)。process の生き死には見ない
(Windows の os.kill は合図の 0 でも process を止めるため)。止めるのは合図のファイル intake_watch.stop で、次の見回りで止まる。
--once は記録を書かず、1 周だけ見て終わる (試験と cron 用)。

表を書くのは intake.services (と、その中から呼ぶ connect.services) だけ。鍵の値・注文書の中身・読み取りの JSON を出さない。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import close_old_connections
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import services

#: 読む拡張子 (今の読み取りが受ける種類。小文字にして見る。中身の形は extract_order の _block_for が先頭のバイトで見る)
EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg")
DONE_DIR = "done"
FAILED_DIR = "failed"
#: 書きかけのファイルを読まない待ち (最後に書いた時刻からの秒)。複合機や共有フォルダへの写しが終わるのを待つ。試験は負の値にする (ファイルの時刻と時計の差に左右されないため)
SETTLE_SECONDS = 2
#: 見回りの間の既定 (秒)
DEFAULT_INTERVAL = 10
#: 見張りの記録と止める合図のファイルの名前 (置き場の BASE_DIR の下)
RECORD = "intake_watch.json"
STOP = "intake_watch.stop"
#: 最後の見回りがこの秒 (か間の 3 倍の長い方) より古い記録は、止まっている見張りの物として置き直す
STALE_SECONDS = 30
MESSAGE_OPEN = "ファイルを開けませんでした。置き直してください。"

#: 見回りの間の待ち。試験が差し替える
_sleep = time.sleep


class WatchError(Exception):
    """見張りを始められない・記録の置き場が無い (命令が CommandError に替える日本語の文)。"""


def default_folder() -> str:
    """settings の SEIZOU の intake_watch_folder (空でない字なら前後の空白を落とした字)。無ければ ""。"""
    seizou = getattr(settings, "SEIZOU", {}) or {}
    value = seizou.get("intake_watch_folder") if isinstance(seizou, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def pending(folder) -> list[Path]:
    """フォルダの直下の、読む拡張子で書き終わったファイルを (最後に書いた時刻, 名前) の古い順に並べる。"""
    settled = time.time() - SETTLE_SECONDS
    found = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if not entry.is_file() or os.path.splitext(entry.name)[1].lower() not in EXTENSIONS:
                continue
            written = entry.stat().st_mtime
            if written <= settled:
                found.append((written, entry.name, Path(entry.path)))
    found.sort(key=lambda row: (row[0], row[1]))
    return [path for _written, _name, path in found]


def handle(path, user) -> dict:
    """ファイル 1 つを読み取って受信した注文に入れ、done/ か failed/ へ動かす。返す物は結果の辞書。

    result は inbox (新しく入れた)・same (同じ中身が入っている)・failed (読めなかった)・held (1 日の上限で読まずに置いた)。読み取りの断り
    (鍵が無い・形式・大きさ・API の失敗・返事の形・connect が無い) と開けない・読めないは failed/ へ動かし、
    理由を failed/<動かした名前>.txt に書く。動かすのにも失敗したら動かさずに理由だけを返す。1 日の上限 (services.WatchLimitReached) は
    ファイルを動かさず、理由だけを返す (次の日の見回りで読む)。ほかの例外は上へ出す。
    """
    path = Path(path)
    row = {"result": "failed", "name": path.name, "moved": None, "inbox": None, "reason": ""}
    try:
        with open(path, "rb") as stream:
            inbox, created = services.extract_to_inbox(File(stream, name=path.name), user)
    except services.WatchLimitReached as exc:
        # 上限で読まなかった物は動かさない (読んでいないので done/ でも failed/ でもない)。
        row["result"] = "held"
        row["reason"] = str(exc)
        return row
    except ValueError as exc:
        # IntakeUnavailable は ValueError の子。文に鍵の値と読み取りの中身は入っていない。
        return _fail(path, row, str(exc))
    except OSError:
        return _fail(path, row, MESSAGE_OPEN)
    row["result"] = "inbox" if created else "same"
    row["inbox"] = inbox
    try:
        row["moved"] = _move(path, DONE_DIR)
    except OSError:
        # 動かせなければ残す。次の見回りは同じ中身なので API を呼ばずに、もう一度 done/ へ動かす。
        pass
    return row


def _fail(path, row, reason) -> dict:
    row["reason"] = reason
    try:
        moved = _move(path, FAILED_DIR)
    except OSError:
        return row
    row["moved"] = moved
    moved.with_name(f"{moved.name}.txt").write_text(f"{reason}\n", encoding="utf-8")
    return row


def _move(path, sub) -> Path:
    """<フォルダ>/<sub>/ を作り、同じ名前があれば -2・-3… の空いている名前にして動かす。動かした先を返す。"""
    path = Path(path)
    target_dir = path.parent / sub
    target_dir.mkdir(exist_ok=True)
    target = target_dir / path.name
    number = 2
    while target.exists():
        target = target_dir / f"{path.stem}-{number}{path.suffix}"
        number += 1
    os.replace(path, target)
    return target


def run_once(folder, user) -> list[dict]:
    """1 周見る。pending の古い順に handle を 1 つずつ呼んだ結果の並び。"""
    return [handle(path, user) for path in pending(folder)]


def _base() -> Path:
    base = getattr(settings, "BASE_DIR", None)
    if not base:
        raise WatchError("settings に BASE_DIR が無いので、見張りの記録の置き場を決められません。")
    return Path(base)


def record_path() -> Path:
    """見張りの記録のファイル (置き場の intake_watch.json)。"""
    return _base() / RECORD


def stop_path() -> Path:
    """止める合図のファイル (置き場の intake_watch.stop)。"""
    return _base() / STOP


def read_record() -> dict | None:
    """見張りの記録。無い・JSON でない・辞書でない・pid が正の整数でない → None。"""
    try:
        data = json.loads(record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    return data


def _fresh(record) -> bool:
    """最後の見回り (seen_at) が max(3 × 間, STALE_SECONDS) 秒より新しいか。読めなければ偽。"""
    interval = record.get("interval")
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
        interval = DEFAULT_INTERVAL
    seen = record.get("seen_at")
    try:
        seen_at = datetime.fromisoformat(seen) if isinstance(seen, str) else None
    except ValueError:
        return False
    if seen_at is None or timezone.is_naive(seen_at):
        return False
    return (timezone.now() - seen_at).total_seconds() < max(3 * interval, STALE_SECONDS)


def status() -> dict | None:
    """記録に running (最後の見回りが新しい) を足した物。記録が無ければ None。"""
    record = read_record()
    if record is None:
        return None
    return {**record, "running": _fresh(record)}


def status_time(value) -> str:
    """--status に出す日時の字。記録の日時 (ISO の字) を、settings の TIME_ZONE の時刻の「2026-11-04 09:40:17」の形にする。

    記録のファイルは ISO の字のまま (読み書きの形を変えない)。読めない字・timezone の無い字・字でない値は、今までと同じ字のまま返す。
    """
    try:
        parsed = parse_datetime(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or timezone.is_naive(parsed):
        return f"{value}"
    return timezone.localtime(parsed).strftime("%Y-%m-%d %H:%M:%S")


def request_stop() -> bool:
    """止める合図のファイルを置く。記録が無ければ置かずに偽。"""
    if read_record() is None:
        return False
    stop_path().write_text(timezone.now().isoformat(), encoding="utf-8")
    return True


def ensure_idle() -> None:
    """動いている見張りがあれば WatchError (2 つが同じファイルを読まないように)。"""
    current = status()
    if current is not None and current["running"]:
        raise WatchError(
            f"既に見張っています (pid {current['pid']}・フォルダ {current.get('folder', '')})。"
            "止めるときは manage.py seizou_intake_watch --stop。"
        )


def once(folder, user) -> list[dict]:
    """--once。動いている見張りが無ければ 1 周だけ見る (記録を書かない。DB の接続を閉じない)。"""
    ensure_idle()
    return run_once(folder, user)


def _write_record(record) -> None:
    path = record_path()
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _signature(path) -> tuple | None:
    """ファイルが置き直されたかを見る印 (最後に書いた時刻, 大きさ)。stat できなければ None。"""
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return (stat.st_mtime, stat.st_size)


def run(folder, user, interval, write) -> None:
    """常駐。合図のファイルが置かれるまで、interval 秒ごとに 1 周見て、1 つごとに write(結果の辞書) を呼ぶ。

    動いている見張りがあれば WatchError。古い記録と残った合図のファイルは置き直す。終わるとき (Ctrl+C の
    KeyboardInterrupt でも) 記録と合図のファイルを消す。周の頭で close_old_connections を呼ぶ (長く立てておくため)。
    読めなかった (failed) のに failed/ へ動かせなかったファイルは、道と印 (最後に書いた時刻・大きさ) を process の中で覚え、
    次の見回りからは読まない (置き直されて印が変われば読む。run_once と --once は今までどおり毎回読む)。
    訳: failed/ へ動かせない読めないファイルを 10 秒ごとに読み直し、そのたびに API を呼ぶのを止めるため。出所 = エンタープライズ化の調べ 第一弾の候補 18 の確かめ直しの直し方の直し (f) (11-07)。変えてよい条件 = 読めなかった物の置き場を変えるとき。
    """
    ensure_idle()
    stop = stop_path()
    stop.unlink(missing_ok=True)
    started = timezone.now().isoformat()
    record = {"pid": os.getpid(), "folder": str(folder), "interval": interval, "started_at": started, "seen_at": started}
    _write_record(record)
    # failed/ へ動かせなかった読めないファイル (道 → 印)。同じ印のままの物は読み直さない。
    unmovable = {}
    try:
        while not stop.exists():
            close_old_connections()
            # 1 つ読むごとに書き、見回りの時刻も書き直す (読み取りの待ちが長くても、動いている見張りに見える)。
            for path in pending(folder):
                signature = _signature(path)
                if signature is not None and unmovable.get(path) == signature:
                    continue
                row = handle(path, user)
                if row["result"] == "failed" and row["moved"] is None:
                    unmovable[path] = signature
                else:
                    unmovable.pop(path, None)
                write(row)
                record["seen_at"] = timezone.now().isoformat()
                _write_record(record)
            record["seen_at"] = timezone.now().isoformat()
            _write_record(record)
            _sleep(interval)
    finally:
        record_path().unlink(missing_ok=True)
        stop.unlink(missing_ok=True)
