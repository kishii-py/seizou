"""入れる所から画面が開くまでを通して確かめる (CI の install の job と、手元の確かめの両方で使う)。

する事: 一時の置き場に、作り物の 2 社を seizou new --demo で立てる → 置き場ごとに manage.py runserver を起こす →
/health/ が 200・最初の画面が開く・受注の一覧に作り物の受注が並ぶ・点検 (seizou_doctor) が止まらずに終わる、までを見る。
どれかが外れたら 1 で終わる。通ったかどうかは、終わりの番号とファイルと HTTP の答えで決め、出力の日本語の字では決めない
(英語の Windows の端末は日本語を「?」に置き換えるため。字が読めたかどうかは、知らせとして 1 行出すだけにする)。
訳: Linux と Windows は試験 (pytest) だけで、入れる所から起動までを通した確かめが無かった (精査 Q6 の提案 11-11)。
    実機の代わりに、GitHub Actions の ubuntu・windows・macos でこの 1 本を回す。環境変数 PYTHONUTF8 は置かない
    (使う人の PC と同じ既定の文字コードで通すため)。
使い方: uv sync --locked の後に uv run python scripts/install_smoke.py。標準の library だけで書く。
"""

from __future__ import annotations

import http.cookiejar
import locale
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: 会社の id と、確かめに使うポート (例の答えの 8001・8002 は、手元で動いている置き場とぶつからないように使わない)
COMPANIES = (("a_machining", 18001), ("b_plating", 18002))
READY_SECONDS = 120
SEEDED = "作り物のデータを登録しました"
DOCTOR_RESULT = "点検の結果"
#: 作り物の受注の番号の頭 (受注の一覧の HTML にこの字があれば、種が入っている。ASCII なので文字コードに左右されない)
ORDER_PREFIX = b"SO-0"
#: 出力の行の終わりの、パスワードに見える字の並び (seizou new は管理者のパスワードを 1 度だけ出す。記録には写さない)
SECRET_AT_END = re.compile(r"(?<=: )[A-Za-z0-9_-]{12,}\s*$")


def say(text: str) -> None:
    """標準出力の文字コードで出せない字は「?」にして出す (Windows の cp1252 の端末でも止まらないように)。"""
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"), flush=True)


def run(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)


def tail(text: str, lines: int = 25) -> str:
    """出力の終わりの行。パスワードに見える字の並びは伏せる。"""
    return "\n".join(SECRET_AT_END.sub("(伏せた)", line) for line in text.splitlines()[-lines:])


def fetch(url: str, opener: urllib.request.OpenerDirector) -> tuple[int, bytes]:
    """url を 1 回開いて、HTTP の状態と本文を返す (開けなければ 0 と空)。"""
    try:
        with opener.open(url, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""
    except (urllib.error.URLError, OSError):
        return 0, b""


def wait_for(url: str, opener: urllib.request.OpenerDirector, seconds: int) -> int:
    """url が答えるまで待って、HTTP の状態を返す (答えなければ 0)。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return 0


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="seizou-smoke-", ignore_cleanup_errors=True) as work:
        sites = Path(work) / "sites"
        sites.mkdir()
        # 置き場の一覧 (~/.seizou/sites.json) にも一時の置き場を書かないように、SEIZOU_HOME も一時のフォルダへ向ける
        env = dict(os.environ, SEIZOU_SITES_DIR=str(sites), SEIZOU_HOME=str(Path(work) / "home"))
        servers: list[tuple[str, subprocess.Popen, Path]] = []
        try:
            for company, port in COMPANIES:
                started = time.monotonic()
                answers = ROOT / "examples" / "companies" / f"{company}.yaml"
                new = run(
                    [sys.executable, "-c", "from seizou.cli import main; raise SystemExit(main())", "new", str(answers), "--demo"],
                    ROOT,
                    env,
                )
                site = sites / company
                if new.returncode != 0 or not (site / "manage.py").is_file() or not (site / "db.sqlite3").is_file():
                    failures.append(f"{company}: seizou new が通らなかった (終わりの番号 {new.returncode})")
                    say(f"--- {company} seizou new の出力の終わり\n{tail(new.stdout)}\n{tail(new.stderr)}")
                    continue
                say(f"{company}: seizou new --demo が通った ({time.monotonic() - started:.0f} 秒)")
                if SEEDED not in new.stdout:
                    say(f"{company}: 知らせ = seizou new の出力の日本語が読めなかった (端末の文字コード {locale.getpreferredencoding(False)})")

                log = (Path(work) / f"{company}-server.log").open("w", encoding="utf-8")
                server = subprocess.Popen(
                    [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
                    cwd=site,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                servers.append((company, server, Path(log.name)))
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                started = time.monotonic()
                health = wait_for(f"http://127.0.0.1:{port}/health/", opener, READY_SECONDS)
                if health != 200:
                    failures.append(f"{company}: /health/ が {health} (200 のはず)")
                    continue
                say(f"{company}: 起動して /health/ が 200 ({time.monotonic() - started:.0f} 秒)")
                home = wait_for(f"http://127.0.0.1:{port}/", opener, 30)
                if home != 200:
                    failures.append(f"{company}: 最初の画面が {home} (200 のはず)")
                else:
                    say(f"{company}: 最初の画面が開いた")
                status, body = fetch(f"http://127.0.0.1:{port}/orders/", opener)
                if status != 200 or ORDER_PREFIX not in body:
                    failures.append(f"{company}: 受注の一覧に作り物の受注が無い (状態 {status})")
                else:
                    say(f"{company}: 受注の一覧に作り物の受注が並んだ")

                doctor = run([sys.executable, "manage.py", "seizou_doctor"], site, env)
                if doctor.returncode not in (0, 1) or "Traceback" in doctor.stdout + doctor.stderr or not doctor.stdout.strip():
                    failures.append(f"{company}: 点検が止まった (終わりの番号 {doctor.returncode})")
                    say(f"--- {company} seizou_doctor の出力の終わり\n{tail(doctor.stdout)}\n{tail(doctor.stderr)}")
                else:
                    results = [line for line in doctor.stdout.splitlines() if DOCTOR_RESULT in line]
                    say(f"{company}: 点検が終わった ({results[-1].strip() if results else '結果の行の日本語は読めなかった'})")
        finally:
            for company, server, log_path in servers:
                server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=15)
                if failures:
                    say(f"--- {company} のサーバーの記録の終わり\n{tail(log_path.read_text(encoding='utf-8', errors='replace'))}")
    if failures:
        say("通らなかった物:")
        for failure in failures:
            say(f"  {failure}")
        return 1
    say("入れる所から画面が開くまで、2 社とも通った")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
