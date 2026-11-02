"""夜の記録の hook (scripts/night_log.py) のテスト。pytest を回す分は飛ばして回す。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "scripts" / "night_log.py"
HOOK_MARK = "[hook night_log]"
TITLE = "D9 試しの題"
BRANCH = "lane/D9"


def git_env(tmp_path: Path) -> dict[str, str]:
    empty_config = tmp_path / "empty.gitconfig"
    empty_config.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "GIT_CONFIG_GLOBAL": str(empty_config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "作り物の作者",
            "GIT_AUTHOR_EMAIL": "author@example.invalid",
            "GIT_COMMITTER_NAME": "作り物の作者",
            "GIT_COMMITTER_EMAIL": "author@example.invalid",
        }
    )
    return env


@pytest.fixture
def lane(tmp_path: Path):
    env = git_env(tmp_path)
    root = tmp_path / "lane"
    subprocess.run(["git", "init", "-q", "-b", BRANCH, str(root)], check=True, env=env)
    (root / "README.md").write_text("作り物\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "最初"], cwd=root, check=True, env=env)
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=root, check=True, env=env, capture_output=True, text=True,
    ).stdout.strip()
    env["SEIZOU_NIGHT_LOG_SKIP_PYTEST"] = "1"
    return root.resolve(), env, head


def write_allow(root: Path) -> None:
    path = root / "docs" / "nights" / "20261102.allow"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {TITLE}\ntests/\n", encoding="utf-8")


def run_log(root: Path, env: dict[str, str], stop_hook_active: bool = False):
    payload = {
        "session_id": "test",
        "transcript_path": "",
        "cwd": str(root),
        "hook_event_name": "Stop",
        "stop_hook_active": stop_hook_active,
    }
    return subprocess.run(
        [sys.executable, str(LOG)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=root,
        env=env,
    )


def record_path(root: Path) -> Path:
    return root / "docs" / "nights" / "20261102" / "D9.md"


def test_no_allow_file_writes_nothing(lane):
    root, env, _ = lane
    result = run_log(root, env)
    assert result.returncode == 0
    assert not (root / "docs").exists()


def test_adds_one_line_with_title_branch_hash_and_mark(lane):
    root, env, head = lane
    write_allow(root)
    result = run_log(root, env)
    assert result.returncode == 0, result.stderr
    lines = record_path(root).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    line = lines[0]
    assert TITLE in line
    assert BRANCH in line
    assert head in line
    assert HOOK_MARK in line


def test_keeps_existing_lines(lane):
    root, env, _ = lane
    write_allow(root)
    record = record_path(root)
    record.parent.mkdir(parents=True)
    existing = "# 記録\n- レーンが自分で書いた行"
    record.write_text(existing, encoding="utf-8")
    assert run_log(root, env).returncode == 0
    lines = record.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == existing.splitlines()
    assert len(lines) == 3
    assert HOOK_MARK in lines[2]


def test_does_not_write_summary_file(lane):
    root, env, _ = lane
    write_allow(root)
    assert run_log(root, env).returncode == 0
    assert not (root / "docs" / "nights" / "20261102.md").exists()


def test_two_runs_add_two_lines(lane):
    root, env, _ = lane
    write_allow(root)
    assert run_log(root, env).returncode == 0
    assert run_log(root, env).returncode == 0
    lines = record_path(root).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(HOOK_MARK in line for line in lines)


def test_stop_hook_active_writes_nothing(lane):
    root, env, _ = lane
    write_allow(root)
    result = run_log(root, env, stop_hook_active=True)
    assert result.returncode == 0
    assert not record_path(root).exists()


def test_pytest_summary_reads_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import night_log
    finally:
        sys.path.remove(str(REPO / "scripts"))
    fake = "print('1 failed, 18 passed, 2 skipped in 0.10s')"
    monkeypatch.setattr(night_log, "PYTEST_COMMAND", (sys.executable, "-c", fake))
    monkeypatch.delenv("SEIZOU_NIGHT_LOG_SKIP_PYTEST", raising=False)
    summary = night_log.pytest_summary(tmp_path)
    assert summary == "pytest で通った本数 18 (failed 1・skipped 2)"
