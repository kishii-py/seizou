#!/usr/bin/env python3
"""夜の仕事の記録を 1 行足す (Claude Code の Stop の hook)。

作り手の運用の hook。公開のプラグインには入れない。設定の書き方は scripts/hooks.md。
夜の印 (docs/nights/<日付>.allow。形は scripts/night_guard.py の頭のコメント) が無いときは何もしない。
あれば、レーンの記録のファイル docs/nights/<日付>/<番号>.md に 1 行足す (無ければ作る)。
  日付 = .allow のファイルの名前、番号 = .allow の 1 行目の # の次の語。
足す行: 日時・レーンの題 (.allow の 1 行目)・枝の名前と HEAD の短い hash・
        通ったテストの本数 (uv run pytest -q の集計の行から読む)・hook が書いた印。
まとめのファイル docs/nights/<日付>.md には書かない。
止めない・ループしない: いつも 0 で終わる。入力の stop_hook_active が true なら何もしない。
環境変数 SEIZOU_NIGHT_LOG_SKIP_PYTEST に 1 を入れると pytest を回さない (テスト用)。
標準の library だけで書く。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import night_guard as ng  # noqa: E402

SKIP_PYTEST_ENV = "SEIZOU_NIGHT_LOG_SKIP_PYTEST"
PYTEST_COMMAND = ("uv", "run", "pytest", "-q")
PYTEST_TIMEOUT_SECONDS = 840
HOOK_MARK = "[hook night_log]"
COUNT_RE = re.compile(r"(\d+) (passed|failed|errors?|skipped)")


def git_value(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "不明"
    return out or "不明"


def pytest_summary(root: Path) -> str:
    if os.environ.get(SKIP_PYTEST_ENV) == "1":
        return "pytest を飛ばした"
    try:
        result = subprocess.run(
            PYTEST_COMMAND,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=PYTEST_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"pytest を回せなかった ({PYTEST_TIMEOUT_SECONDS} 秒で打ち切り)"
    except OSError:
        return "pytest を回せなかった (uv が起動できない)"
    for line in reversed(result.stdout.splitlines()):
        counts = {
            ("error" if kind.startswith("error") else kind): int(n)
            for n, kind in COUNT_RE.findall(line)
        }
        if counts:
            passed = counts.pop("passed", 0)
            rest = "・".join(f"{kind} {n}" for kind, n in counts.items())
            return f"pytest で通った本数 {passed}" + (f" ({rest})" if rest else "")
    return "pytest を回せなかった (集計の行が無い)"


def build_line(allow: ng.AllowList, root: Path, now: datetime) -> str:
    branch = git_value(root, "branch", "--show-current")
    head = git_value(root, "rev-parse", "--short", "HEAD")
    return (
        f"- {now.strftime('%Y-%m-%d %H:%M')} / {allow.title_line} / "
        f"枝 {branch} @ {head} / {pytest_summary(root)} / {HOOK_MARK}"
    )


def append_line(record: Path, line: str) -> None:
    record.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if record.exists():
        current = record.read_text(encoding="utf-8")
        if current and not current.endswith("\n"):
            prefix = "\n"
    with record.open("a", encoding="utf-8") as fh:
        fh.write(prefix + line + "\n")


def run(data: dict, now: datetime | None = None) -> Path | None:
    if data.get("stop_hook_active") is True:
        return None
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else None
    root = ng.find_root(cwd)
    allow_files = ng.find_allow_files(root)
    if len(allow_files) != 1:
        if allow_files:
            print("夜の記録を書かなかった: 許しの一覧が 2 つ以上ある。", file=sys.stderr)
        return None
    allow = ng.parse_allow(allow_files[0])
    if not allow.lane:
        print("夜の記録を書かなかった: 許しの一覧の 1 行目に番号が無い。", file=sys.stderr)
        return None
    record = root / ng.ALLOW_DIR / allow.date / f"{allow.lane}.md"
    append_line(record, build_line(allow, root, now or datetime.now().astimezone()))
    return record


def main() -> int:
    try:
        run(ng.read_input(sys.stdin))
    except Exception as exc:  # noqa: BLE001 - 記録の hook は止めない
        print(f"夜の記録を書けなかった: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
