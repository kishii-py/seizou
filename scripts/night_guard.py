#!/usr/bin/env python3
"""夜の無人の仕事で、許しの一覧に無い道への書き込みを止める (Claude Code の PreToolUse の hook)。

作り手の運用の hook。公開のプラグインには入れない。設定の書き方は scripts/hooks.md。
夜の印 (許しの一覧のファイル) が無いときは何もしない (昼の会話と取り込みの作業を止めない)。

許しの一覧 (.allow) の形:
  置き場と名前  作業場の docs/nights/<日付>.allow (例: docs/nights/20261102.allow)。
                1 つの作業場に 1 つ。commit しない。
  1 行目        # の後に依頼書の番号と題 (例: # D3 hooks)。番号は D と数字。
                夜の記録の hook は番号を記録のファイルの名前 docs/nights/<日付>/<番号>.md に使う。
  2 行目から    1 行に 1 つ、作業場の根からの相対の道。
                フォルダ (末尾 /) はその下の全部を許す。
                ファイルの名前の部分に * の glob を書ける (例: tests/test_hooks_*.py)。
                絶対の道を書けば作業場の外も許せる。空行と # の行は飛ばす。
  いつも許す物  docs/nights/ (書いていなくても許す。止まった記録を書くため)。
  許さない物    一覧に無い道の全部。src/seizou/core/・tests/test_r*・RULES.md・plugin/hooks/ も
                一覧に名指しが無ければ止まる。作業場の外の道も止まる。
  .allow が 2 つ以上あれば設定の誤りとして、docs/nights/ の下の他を全部止める。

止めるときは終わりの番号 2 で終わり、理由を標準エラーに日本語で出す (Claude Code が Claude に渡す)。
標準の library だけで書く。
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ALLOW_DIR = "docs/nights"
ALLOW_GLOB = "*.allow"
EXIT_ALLOW = 0
EXIT_BLOCK = 2


@dataclass(frozen=True)
class AllowList:
    path: Path
    date: str
    lane: str
    title_line: str
    entries: tuple[str, ...]


def read_input(stream) -> dict:
    try:
        data = json.loads(stream.read() or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def find_root(cwd: str | None) -> Path:
    base = Path(cwd) if cwd else Path.cwd()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=base,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if out:
            return Path(out).resolve()
    except (OSError, subprocess.CalledProcessError):
        pass
    return base.resolve()


def find_allow_files(root: Path) -> list[Path]:
    folder = root / ALLOW_DIR
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob(ALLOW_GLOB) if p.is_file())


def parse_allow(path: Path) -> AllowList:
    lines = path.read_text(encoding="utf-8").splitlines()
    first = lines[0].strip() if lines else ""
    title_line = first.lstrip("#").strip() if first.startswith("#") else ""
    lane = title_line.split()[0] if title_line else ""
    entries = []
    for raw in lines[1:]:
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        entries.append(entry)
    return AllowList(path, path.stem, lane, title_line, tuple(entries))


def resolve_target(file_path: str, cwd: str | None) -> Path:
    target = Path(file_path).expanduser()
    if not target.is_absolute():
        target = (Path(cwd) if cwd else Path.cwd()) / target
    return target.resolve()


def relative_to(target: Path, root: Path) -> str | None:
    try:
        return target.relative_to(root).as_posix()
    except ValueError:
        return None


def entry_matches(entry: str, target: Path, rel: str | None) -> bool:
    if Path(entry).is_absolute():
        candidate = Path(entry)
        if entry.endswith("/"):
            return relative_to(target, candidate.resolve()) is not None
        pattern = (candidate.parent.resolve() / candidate.name).as_posix()
        return _same_or_glob(pattern, target.as_posix())
    if rel is None:
        return False
    if entry.endswith("/"):
        prefix = entry.rstrip("/")
        return rel == prefix or rel.startswith(prefix + "/")
    return _same_or_glob(entry, rel)


def _same_or_glob(pattern: str, path: str) -> bool:
    pattern_dir, _, pattern_name = pattern.rpartition("/")
    path_dir, _, path_name = path.rpartition("/")
    if pattern_dir != path_dir:
        return False
    if "*" in pattern_name:
        return fnmatch.fnmatchcase(path_name, pattern_name)
    return pattern_name == path_name


def is_always_allowed(rel: str | None) -> bool:
    return rel is not None and (rel == ALLOW_DIR or rel.startswith(ALLOW_DIR + "/"))


def block_message(shown: str, allow_name: str, record_hint: str) -> str:
    return (
        f"書き込みを止めた: {shown} は、この夜の許しの一覧 ({allow_name}) に無い。"
        f"部品に足りない物は、docs/nights/ の下の自分の記録のファイル ({record_hint}) に"
        "要望として 1 行で書く。"
    )


def decide(data: dict) -> tuple[int, str]:
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else None
    root = find_root(cwd)
    allow_files = find_allow_files(root)
    if not allow_files:
        return EXIT_ALLOW, ""

    tool_input = data.get("tool_input")
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path:
        return EXIT_BLOCK, (
            "書き込みを止めた: hook の入力から書き込む道が読めず、"
            "この夜の許しの一覧と突き合わせられない。"
        )

    target = resolve_target(file_path, cwd)
    rel = relative_to(target, root)
    shown = rel if rel is not None else f"{target} (作業場の外)"
    if is_always_allowed(rel):
        return EXIT_ALLOW, ""

    if len(allow_files) > 1:
        names = "・".join(p.name for p in allow_files)
        return EXIT_BLOCK, (
            f"書き込みを止めた: 許しの一覧が {len(allow_files)} つある ({names})。"
            "設定の誤りなので、docs/nights/ の下の他は書けない。"
            "部品に足りない物は、docs/nights/ の下の自分の記録のファイルに要望として 1 行で書く。"
        )

    allow = parse_allow(allow_files[0])
    if any(entry_matches(entry, target, rel) for entry in allow.entries):
        return EXIT_ALLOW, ""
    record_hint = (
        f"docs/nights/{allow.date}/{allow.lane}.md" if allow.lane else "docs/nights/<日付>/<番号>.md"
    )
    return EXIT_BLOCK, block_message(shown, allow.path.name, record_hint)


def main() -> int:
    code, message = decide(read_input(sys.stdin))
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
