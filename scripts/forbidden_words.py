#!/usr/bin/env python3
"""禁止語の検査 (作り手の運用。公開のプラグインには入れない)。

一覧は環境変数で渡す。読む順は次の 2 つだけ。
  1. SEIZOU_FORBIDDEN_WORDS_FILE  一覧のファイルの道
  2. SEIZOU_FORBIDDEN_WORDS       一覧の中身そのもの (CI の secret 用)
どちらも無ければ、検査をせずに 0 以外で終わる (何も見ていないのに通さない)。

一覧の形: 1 行に 1 語。空行と # で始まる行は飛ばす。大文字と小文字を区別しない。
当て方: 4 文字以上の語は部分一致。3 文字以下の語は英数字の並びの途中では当てない。
外す物: 二進のファイル (画像・PDF・フォント・TIFF と、NUL を含む物)、
        道に static/vendor/ を含む物 (同梱の JS と CSS)、
        名前が static/css/app.css で終わる物 (作った CSS)。

使い方:
  python scripts/forbidden_words.py <道> ...   ファイルかフォルダ (フォルダは下の全部)
  python scripts/forbidden_words.py            標準入力の文を見る (- でも同じ)
終わりの番号: 0 = 当たり無し、1 = 当たりあり、2 = 一覧が読めない・道が無い・見た物が 0。

.githooks/pre-push と CI と取り込みの確かめが、この 1 本を使う。標準の library だけで書く。
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ENV_FILE = "SEIZOU_FORBIDDEN_WORDS_FILE"
ENV_WORDS = "SEIZOU_FORBIDDEN_WORDS"

EXIT_OK = 0
EXIT_HIT = 1
EXIT_ERROR = 2

SHORT_WORD_MAX = 3

BINARY_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
        ".tif", ".tiff",
        ".pdf",
        ".ttf", ".otf", ".woff", ".woff2", ".eot",
    }
)
VENDOR_PART = "static/vendor/"
BUILT_CSS_SUFFIX = "static/css/app.css"
SKIP_DIR_NAMES = frozenset({".git"})


class WordListError(Exception):
    """一覧が読めないとき。"""


@dataclass(frozen=True)
class Hit:
    label: str
    lineno: int
    word: str

    def render(self) -> str:
        return f"{self.label}:{self.lineno}: 禁止語「{self.word}」"


@dataclass
class Report:
    hits: list[Hit] = field(default_factory=list)
    examined: int = 0
    skipped: int = 0


def parse_words(text: str) -> list[str]:
    words: list[str] = []
    for raw in text.splitlines():
        word = raw.strip()
        if not word or word.startswith("#"):
            continue
        words.append(word)
    return words


def read_order_text() -> str:
    return (
        "一覧の読む順: "
        f"(1) 環境変数 {ENV_FILE} に一覧のファイルの道、"
        f"(2) 環境変数 {ENV_WORDS} に一覧の中身 (1 行に 1 語)。"
        "直し方: どちらかを渡してから打ち直す。"
    )


def load_words(environ: dict[str, str] | None = None) -> list[str]:
    env = os.environ if environ is None else environ
    path_value = env.get(ENV_FILE, "").strip()
    if path_value:
        try:
            text = Path(path_value).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            raise WordListError(
                f"環境変数 {ENV_FILE} の道の一覧が読めない ({exc.strerror})。"
            ) from exc
        source = ENV_FILE
    elif env.get(ENV_WORDS, "").strip():
        text = env[ENV_WORDS]
        source = ENV_WORDS
    else:
        raise WordListError(f"環境変数 {ENV_FILE} も {ENV_WORDS} も無い。")
    words = parse_words(text)
    if not words:
        raise WordListError(f"{source} から読んだ一覧に語が 1 つも無い。")
    return words


def compile_matchers(words: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    matchers: list[tuple[str, re.Pattern[str]]] = []
    for word in words:
        escaped = re.escape(word)
        if len(word) <= SHORT_WORD_MAX:
            pattern = rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
        else:
            pattern = escaped
        matchers.append((word, re.compile(pattern, re.IGNORECASE)))
    return matchers


def scan_line(
    line: str, label: str, lineno: int, matchers: list[tuple[str, re.Pattern[str]]]
) -> list[Hit]:
    return [Hit(label, lineno, word) for word, rx in matchers if rx.search(line)]


def scan_text(
    text: str, label: str, matchers: list[tuple[str, re.Pattern[str]]]
) -> list[Hit]:
    hits: list[Hit] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        hits.extend(scan_line(line, label, lineno, matchers))
    return hits


def is_skipped_path(path: str) -> bool:
    posix = path.replace("\\", "/")
    if VENDOR_PART in posix:
        return True
    if posix.endswith(BUILT_CSS_SUFFIX):
        return True
    return Path(posix).suffix.lower() in BINARY_SUFFIXES


def looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def iter_files(target: Path):
    if target.is_file():
        yield target
        return
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIR_NAMES)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def scan_paths(
    paths: list[str], matchers: list[tuple[str, re.Pattern[str]]]
) -> Report:
    report = Report()
    for raw in paths:
        target = Path(raw)
        if not target.exists():
            raise FileNotFoundError(raw)
        for file_path in iter_files(target):
            label = file_path.as_posix()
            if is_skipped_path(label):
                report.skipped += 1
                continue
            data = file_path.read_bytes()
            if looks_binary(data):
                report.skipped += 1
                continue
            report.examined += 1
            report.hits.extend(scan_text(decode(data), label, matchers))
    return report


def summary_line(word_count: int, report: Report, extra: str = "") -> str:
    return (
        f"禁止語の検査: 一覧の語 {word_count} 個 / 見たファイル {report.examined} 本 / "
        f"外したファイル {report.skipped} 本{extra} / 当たり {len(report.hits)} 件"
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        words = load_words()
    except WordListError as exc:
        print(f"禁止語の検査をしなかった: {exc}", file=sys.stderr)
        print(read_order_text(), file=sys.stderr)
        return EXIT_ERROR
    matchers = compile_matchers(words)

    if not args or args == ["-"]:
        text = sys.stdin.read()
        report = Report(hits=scan_text(text, "<標準入力>", matchers), examined=1)
        extra = " (標準入力)"
    else:
        try:
            report = scan_paths(args, matchers)
        except FileNotFoundError as exc:
            print(f"禁止語の検査をしなかった: 道が無い: {exc}", file=sys.stderr)
            return EXIT_ERROR
        extra = ""
        if report.examined == 0:
            print(summary_line(len(words), report), file=sys.stderr)
            print("見たファイルが 0 本なので、合格にしない。", file=sys.stderr)
            return EXIT_ERROR

    for hit in report.hits:
        print(hit.render())
    print(summary_line(len(words), report, extra))
    return EXIT_HIT if report.hits else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
