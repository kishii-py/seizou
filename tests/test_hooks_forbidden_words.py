"""禁止語の検査 (scripts/forbidden_words.py) と pre-push の hook (.githooks/pre-push) のテスト。

一覧はテストが一時のフォルダに作る作り物の語だけを使う。実物の一覧は読まない。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "forbidden_words.py"
HOOK = REPO / ".githooks" / "pre-push"

ENV_FILE = "SEIZOU_FORBIDDEN_WORDS_FILE"
ENV_WORDS = "SEIZOU_FORBIDDEN_WORDS"

LONG_WORD = "plumbrix"
SHORT_WORD = "zqx"
WORD_LIST = f"# 作り物の一覧\n\n{LONG_WORD}\n{SHORT_WORD}\n"


def clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop(ENV_FILE, None)
    env.pop(ENV_WORDS, None)
    return env


@pytest.fixture
def list_env(tmp_path: Path) -> dict[str, str]:
    list_file = tmp_path / "list" / "words.txt"
    list_file.parent.mkdir()
    list_file.write_text(WORD_LIST, encoding="utf-8")
    env = clean_env()
    env[ENV_FILE] = str(list_file)
    return env


def run_cli(args: list[str], env: dict[str, str], cwd: Path, stdin: str | None = None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_hit_file_exits_nonzero_and_shows_location(tmp_path, list_env):
    write(tmp_path / "work" / "a.txt", f"1 行目\nこれは {LONG_WORD} の話\n")
    result = run_cli(["a.txt"], list_env, tmp_path / "work")
    assert result.returncode == 1
    assert "a.txt:2" in result.stdout
    assert LONG_WORD in result.stdout


def test_clean_file_exits_zero_and_reports_counts(tmp_path, list_env):
    write(tmp_path / "work" / "a.txt", "作り物の一覧\nきれいな文だけ\n")
    result = run_cli(["a.txt"], list_env, tmp_path / "work")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "一覧の語 2 個" in result.stdout
    assert "見たファイル 1 本" in result.stdout
    assert LONG_WORD not in result.stdout
    assert SHORT_WORD not in result.stdout


def test_case_insensitive(tmp_path, list_env):
    write(tmp_path / "work" / "a.txt", "PlumBRIX\n")
    result = run_cli(["a.txt"], list_env, tmp_path / "work")
    assert result.returncode == 1
    assert "a.txt:1" in result.stdout


def test_short_word_not_hit_inside_alnum_run(tmp_path, list_env):
    write(tmp_path / "work" / "a.txt", "abzqxcd\nzqx1 1zqx\nvar a=Zqxb;\n")
    result = run_cli(["a.txt"], list_env, tmp_path / "work")
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("line", ["foo zqx bar", "(ZQX)", "あzqxい", "x-zqx"])
def test_short_word_hits_as_word(tmp_path, list_env, line):
    write(tmp_path / "work" / "a.txt", line + "\n")
    result = run_cli(["a.txt"], list_env, tmp_path / "work")
    assert result.returncode == 1
    assert "a.txt:1" in result.stdout


def test_long_word_hits_as_substring(tmp_path, list_env):
    write(tmp_path / "work" / "a.txt", f"xx{LONG_WORD}yy\n")
    result = run_cli(["a.txt"], list_env, tmp_path / "work")
    assert result.returncode == 1


def test_binary_files_are_skipped(tmp_path, list_env):
    work = tmp_path / "work"
    write(work / "clean.txt", "きれいな文\n")
    write(work / "image.png", f"{LONG_WORD}\n")
    (work / "data.bin").write_bytes(b"\x00\x01" + LONG_WORD.encode())
    result = run_cli(["."], list_env, work)
    assert result.returncode == 0, result.stdout
    assert "見たファイル 1 本" in result.stdout
    assert "外したファイル 2 本" in result.stdout


def test_vendor_and_built_css_are_skipped(tmp_path, list_env):
    work = tmp_path / "work"
    write(work / "clean.txt", "きれいな文\n")
    write(work / "src" / "pkg" / "static" / "vendor" / "lib.js", f"var {LONG_WORD}=1;\n")
    write(work / "site" / "static" / "css" / "app.css", f".{LONG_WORD}{{}}\n")
    result = run_cli(["."], list_env, work)
    assert result.returncode == 0, result.stdout
    assert "外したファイル 2 本" in result.stdout


def test_no_env_fails_with_reason(tmp_path):
    write(tmp_path / "a.txt", "きれいな文\n")
    result = run_cli(["a.txt"], clean_env(), tmp_path)
    assert result.returncode == 2
    assert ENV_FILE in result.stderr
    assert ENV_WORDS in result.stderr


def test_words_env_only_is_enough(tmp_path):
    write(tmp_path / "a.txt", f"{LONG_WORD}\n")
    env = clean_env()
    env[ENV_WORDS] = WORD_LIST
    result = run_cli(["a.txt"], env, tmp_path)
    assert result.returncode == 1
    assert "a.txt:1" in result.stdout


def test_list_without_words_fails(tmp_path):
    list_file = write(tmp_path / "words.txt", "# コメントだけ\n\n")
    write(tmp_path / "a.txt", "きれいな文\n")
    env = clean_env()
    env[ENV_FILE] = str(list_file)
    result = run_cli(["a.txt"], env, tmp_path)
    assert result.returncode == 2


def test_missing_path_fails(tmp_path, list_env):
    result = run_cli(["no_such_file.txt"], list_env, tmp_path)
    assert result.returncode == 2


def test_stdin_text(tmp_path, list_env):
    result = run_cli([], list_env, tmp_path, stdin=f"1 行目\n{LONG_WORD}\n")
    assert result.returncode == 1
    assert ":2:" in result.stdout


# --- pre-push の通し (一時の git のリポジトリと bare の相手) ---

WINDOWS_SKIP_REASON = "git の hook は POSIX の shell と env で起こす前提。Windows の確かめは会期後"


def git(args: list[str], cwd: Path, env: dict[str, str], check: bool = True):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=check
    )


@pytest.fixture
def push_repo(tmp_path, list_env):
    empty_config = tmp_path / "empty.gitconfig"
    empty_config.write_text("", encoding="utf-8")
    env = dict(list_env)
    env.update(
        {
            "GIT_CONFIG_GLOBAL": str(empty_config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "作り物の作者",
            "GIT_AUTHOR_EMAIL": "author@example.invalid",
            "GIT_COMMITTER_NAME": "作り物の作者",
            "GIT_COMMITTER_EMAIL": "author@example.invalid",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    git(["init", "-q", "--bare", str(bare)], tmp_path, env)
    git(["init", "-q", "-b", "work", str(work)], tmp_path, env)
    (work / ".githooks").mkdir()
    (work / "scripts").mkdir()
    shutil.copyfile(HOOK, work / ".githooks" / "pre-push")
    (work / ".githooks" / "pre-push").chmod(0o755)
    shutil.copyfile(SCRIPT, work / "scripts" / "forbidden_words.py")
    write(work / "README.md", "きれいな文\n")
    git(["config", "core.hooksPath", ".githooks"], work, env)
    git(["remote", "add", "origin", str(bare)], work, env)
    git(["add", ".githooks/pre-push", "scripts/forbidden_words.py", "README.md"], work, env)
    git(["commit", "-q", "-m", "最初"], work, env)
    return work, bare, env


def remote_head(bare: Path, env: dict[str, str]) -> str:
    return git(["--git-dir", str(bare), "rev-parse", "work"], bare, env).stdout.strip()


def local_head(work: Path, env: dict[str, str]) -> str:
    return git(["rev-parse", "HEAD"], work, env).stdout.strip()


@pytest.mark.skipif(sys.platform == "win32", reason=WINDOWS_SKIP_REASON)
def test_pre_push_blocks_word_and_passes_after_fix(push_repo):
    work, bare, env = push_repo
    first = git(["push", "-q", "origin", "work"], work, env, check=False)
    assert first.returncode == 0, first.stderr
    assert "見たファイル 3 本" in first.stderr

    write(work / "notes.txt", f"1 行目\nここに {LONG_WORD} がある\n")
    git(["add", "notes.txt"], work, env)
    git(["commit", "-q", "-m", "メモを足す"], work, env)
    blocked = git(["push", "-q", "origin", "work"], work, env, check=False)
    assert blocked.returncode != 0
    assert "notes.txt:2" in blocked.stderr
    assert "push を止めた" in blocked.stderr
    assert remote_head(bare, env) != local_head(work, env)

    write(work / "notes.txt", "1 行目\nきれいな文\n")
    git(["add", "notes.txt"], work, env)
    git(["commit", "-q", "-m", "メモを直す"], work, env)
    passed = git(["push", "-q", "origin", "work"], work, env, check=False)
    assert passed.returncode == 0, passed.stderr
    assert remote_head(bare, env) == local_head(work, env)


@pytest.mark.skipif(sys.platform == "win32", reason=WINDOWS_SKIP_REASON)
def test_pre_push_blocks_word_in_commit_message(push_repo):
    work, bare, env = push_repo
    assert git(["push", "-q", "origin", "work"], work, env, check=False).returncode == 0
    write(work / "notes.txt", "きれいな文\n")
    git(["add", "notes.txt"], work, env)
    git(["commit", "-q", "-m", f"{LONG_WORD} のメモ"], work, env)
    blocked = git(["push", "-q", "origin", "work"], work, env, check=False)
    assert blocked.returncode != 0
    assert "の文" in blocked.stderr


@pytest.mark.skipif(sys.platform == "win32", reason=WINDOWS_SKIP_REASON)
def test_pre_push_blocks_when_list_is_missing(push_repo):
    work, bare, env = push_repo
    env = dict(env)
    env.pop(ENV_FILE, None)
    env.pop(ENV_WORDS, None)
    blocked = git(["push", "-q", "origin", "work"], work, env, check=False)
    assert blocked.returncode != 0
    assert "一覧が読めない" in blocked.stderr
    assert ENV_FILE in blocked.stderr
