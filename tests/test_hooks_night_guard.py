"""夜の許しの一覧を読んで書き込みを止める hook (scripts/night_guard.py) のテスト。

一時の作業場 (git init した物) と .allow を作り、標準入力に PreToolUse の JSON を渡して回す。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GUARD = REPO / "scripts" / "night_guard.py"

ALLOW_NAME = "20261102.allow"
ALLOW_ENTRIES = "src/seizou/sales/\ntests/test_sales_*.py\n"
TOOLS = ["Write", "Edit"]


@pytest.fixture
def lane(tmp_path: Path) -> Path:
    root = tmp_path / "lane"
    root.mkdir()
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env, capture_output=True)
    return root.resolve()


def write_allow(root: Path, entries: str, name: str = ALLOW_NAME) -> Path:
    path = root / "docs" / "nights" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# D9 試しの題\n\n# コメント\n{entries}", encoding="utf-8")
    return path


def run_guard(root: Path, tool: str, file_path: Path | str):
    payload = {
        "session_id": "test",
        "transcript_path": "",
        "cwd": str(root),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(file_path), "content": "作り物の中身"},
    }
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        cwd=root,
    )


@pytest.mark.parametrize("tool", TOOLS)
def test_no_allow_file_does_nothing(lane, tool):
    result = run_guard(lane, tool, lane / "RULES.md")
    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize(
    "rel",
    ["src/seizou/sales/views.py", "src/seizou/sales/sub/forms.py", "tests/test_sales_list.py"],
)
def test_listed_paths_pass(lane, tool, rel):
    write_allow(lane, ALLOW_ENTRIES)
    result = run_guard(lane, tool, lane / rel)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize(
    "rel",
    [
        "RULES.md",
        "src/seizou/core/models.py",
        "tests/test_r01_x.py",
        "plugin/hooks/x.py",
        "tests/sub/test_sales_list.py",
    ],
)
def test_unlisted_paths_are_blocked(lane, tool, rel):
    write_allow(lane, ALLOW_ENTRIES)
    result = run_guard(lane, tool, lane / rel)
    assert result.returncode == 2
    assert "docs/nights" in result.stderr
    assert rel in result.stderr


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("rel", ["docs/nights/x.md", "docs/nights/20261102/D9.md"])
def test_docs_nights_always_passes(lane, tool, rel):
    write_allow(lane, ALLOW_ENTRIES)
    result = run_guard(lane, tool, lane / rel)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("tool", TOOLS)
def test_outside_workspace_is_blocked(lane, tool):
    write_allow(lane, ALLOW_ENTRIES)
    outside = lane.parent / "outside" / "x.txt"
    result = run_guard(lane, tool, outside)
    assert result.returncode == 2
    assert "作業場の外" in result.stderr
    assert "docs/nights" in result.stderr


def test_outside_workspace_passes_when_listed_absolute(lane):
    outside_dir = lane.parent / "outside"
    write_allow(lane, f"{outside_dir}/\n")
    result = run_guard(lane, "Write", outside_dir / "x.txt")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("tool", TOOLS)
def test_two_allow_files_block_all_but_docs_nights(lane, tool):
    write_allow(lane, ALLOW_ENTRIES)
    write_allow(lane, ALLOW_ENTRIES, name="20261103.allow")
    blocked = run_guard(lane, tool, lane / "src/seizou/sales/views.py")
    assert blocked.returncode == 2
    assert "docs/nights" in blocked.stderr
    passed = run_guard(lane, tool, lane / "docs/nights/x.md")
    assert passed.returncode == 0, passed.stderr
