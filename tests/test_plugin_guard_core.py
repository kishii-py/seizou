"""部品の中身への書き込みを止める製品の hook (plugin/hooks/guard_core.py) と、その登録 (hooks.json) のテスト。

一時のフォルダに、会社の置き場・入れた部品 (site-packages)・部品のソースの置き場・置き場の中の部品の写しを作り、
標準入力に PreToolUse の JSON を渡して回す (tests/test_hooks_night_guard.py と同じ形)。
試験を回す Claude Code の環境に CLAUDE_PROJECT_DIR があるかもしれないので、subprocess の環境変数から外してから回し、
要る試験だけが渡す (tests/test_core_mcp_plugin.py と同じ形)。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugin"
GUARD = PLUGIN / "hooks" / "guard_core.py"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"

TOOLS = ["Write", "Edit"]
SITE_PACKAGES_FILE = ".venv/lib/python3.12/site-packages/seizou/core/views.py"


@pytest.fixture
def places(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path.resolve()
    site = root / "sites" / "sample_co"
    for name in ("manage.py", "answers.yaml", "settings.py", "fields/models.py"):
        touch(site / name)
    touch(site / SITE_PACKAGES_FILE)
    touch(site / "seizou" / "parts.py")
    touch(site / "seizou" / "core" / "views.py")
    source = root / "repo"
    touch(source / "src" / "seizou" / "parts.py")
    touch(source / "src" / "seizou" / "core" / "views.py")
    return {"site": site, "source": source}


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def run_guard(cwd: Path, tool: str, file_path: Path | str | None, *, key: str = "file_path", project_dir: Path | None = None):
    tool_input = {"content": "作り物の中身"}
    if file_path is not None:
        tool_input[key] = str(file_path)
    env = {name: value for name, value in os.environ.items() if name != "CLAUDE_PROJECT_DIR"}
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    payload = {
        "session_id": "test",
        "transcript_path": "",
        "cwd": str(cwd),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": tool_input,
    }
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        env=env,
        check=False,
    )


def assert_blocked(result) -> None:
    assert result.returncode == 2, result.stderr
    assert "R20" in result.stderr


def assert_allowed(result) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


# 止める物


@pytest.mark.parametrize("tool", TOOLS)
def test_blocks_site_packages_seizou(places, tool):
    site = places["site"]

    assert_blocked(run_guard(site, tool, site / SITE_PACKAGES_FILE))
    # 会話の今のフォルダが置き場の下でも、まだ無いファイルでも止める。
    assert_blocked(run_guard(site / "fields", tool, site / ".venv/lib/python3.12/site-packages/seizou/new.py"))


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize(
    "rel", ["src/seizou/core/views.py", "src/seizou/parts.py", "src/seizou/new_app/views.py"]
)
def test_blocks_editable_source_of_parts(places, tool, rel):
    assert_blocked(run_guard(places["site"], tool, places["source"] / rel))


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_blocks_copied_parts_in_site(places, tool, relative):
    site = places["site"]
    target = Path("seizou/core/views.py") if relative else site / "seizou" / "core" / "views.py"

    assert_blocked(run_guard(site, tool, target))


def test_blocks_notebook_edit_by_notebook_path(places):
    site = places["site"]

    assert_blocked(run_guard(site, "NotebookEdit", site / SITE_PACKAGES_FILE, key="notebook_path"))
    assert_blocked(run_guard(site, "NotebookEdit", places["source"] / "src" / "seizou" / "core" / "a.ipynb", key="notebook_path"))
    assert_allowed(run_guard(site, "NotebookEdit", site / "manual" / "a.ipynb", key="notebook_path"))


@pytest.mark.parametrize("tool", TOOLS)
def test_blocks_when_project_dir_is_site_and_cwd_is_parts(places, tool):
    # 会話の中で cd して、今のフォルダが部品のソースに移っても、開いたフォルダ (CLAUDE_PROJECT_DIR) が置き場なら止める
    source = places["source"]
    target = source / "src" / "seizou" / "core" / "views.py"

    assert_blocked(run_guard(source / "src" / "seizou", tool, target, project_dir=places["site"]))
    assert_blocked(run_guard(source, tool, "src/seizou/core/views.py", project_dir=places["site"]))


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize(
    "rel", [".venv/lib/python3.12/SITE-PACKAGES/SEIZOU/core/views.py", ".venv/lib/python3.12/Site-Packages/Seizou/new.py"]
)
def test_blocks_upper_case_path(places, tool, rel):
    # 道の名前は小文字にそろえて比べる (macOS と Windows では同じ部品を指す)
    site = places["site"]

    assert_blocked(run_guard(site, tool, site / rel))


# 通す物


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize(
    "rel",
    [
        "fields/models.py",
        "templates/x.html",
        "checks.py",
        "settings.py",
        "manual/a.html",
        "bulk_shipping/views.py",
    ],
)
def test_allows_company_places(places, tool, rel):
    site = places["site"]

    assert_allowed(run_guard(site, tool, site / rel))


@pytest.mark.parametrize("tool", TOOLS)
def test_does_nothing_outside_company_project(places, tool):
    source = places["source"]

    assert_allowed(run_guard(source, tool, source / "src" / "seizou" / "core" / "views.py"))
    assert_allowed(run_guard(source, tool, places["site"] / SITE_PACKAGES_FILE))
    # 開いたフォルダ (CLAUDE_PROJECT_DIR) も置き場でなければ、何もしない
    assert_allowed(run_guard(source, tool, source / "src" / "seizou" / "core" / "views.py", project_dir=source))


@pytest.mark.parametrize("tool", ["Read", "Bash", None])
def test_other_tools_pass(places, tool):
    site = places["site"]

    assert_allowed(run_guard(site, tool, site / SITE_PACKAGES_FILE))


@pytest.mark.parametrize("tool", TOOLS)
def test_unreadable_file_path_passes(places, tool):
    site = places["site"]

    assert_allowed(run_guard(site, tool, None))
    broken = subprocess.run(
        [sys.executable, str(GUARD)], input="{壊れた JSON", capture_output=True, text=True, encoding="utf-8", check=False
    )
    assert_allowed(broken)


def test_message_is_japanese_with_r20(places):
    site = places["site"]

    result = run_guard(site, "Edit", places["source"] / "src" / "seizou" / "core" / "views.py")

    assert result.returncode == 2
    for word in ("R20", "部品 (seizou) の中身", "fields/", "templates/", "checks.py", "担当者"):
        assert word in result.stderr, word
    assert "views.py" in result.stderr


# 書き方と登録


def test_uses_only_standard_library():
    tree = ast.parse(GUARD.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "相対の import"
            names.add(node.module.split(".")[0])
    assert names
    assert names <= set(sys.stdlib_module_names), names - set(sys.stdlib_module_names)


def test_hooks_json_shape():
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))

    assert set(data) <= {"description", "hooks"}
    assert data["description"].strip()
    groups = data["hooks"]["PreToolUse"]
    assert len(groups) == 1
    assert groups[0]["matcher"] == "Write|Edit|NotebookEdit"
    hook = groups[0]["hooks"][0]
    assert hook["type"] == "command"
    assert hook["command"] == "uv"
    assert hook["args"][-1] == "${CLAUDE_PLUGIN_ROOT}/hooks/guard_core.py"
    assert "onFailure" not in hook


def test_manifest_has_no_hooks_key():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert set(data) == {"name", "version", "description"}


# 日本語の道と、UTF-8 を既定にしない標準入力 (日本語の Windows の cp932 と、英語の Windows の cp1252)

NOT_UTF8_ENCODINGS = ["cp932", "cp1252"]


def make_japanese_site(tmp_path: Path) -> Path:
    """日本語のフォルダの下に、places と同じ形の会社の置き場と、置き場の中の部品の写しを作る。"""
    site = tmp_path.resolve() / "作り物の会社" / "sites" / "sample_co"
    for name in ("manage.py", "answers.yaml", "settings.py", "fields/models.py"):
        touch(site / name)
    touch(site / "seizou" / "parts.py")
    touch(site / "seizou" / "core" / "views.py")
    return site


def run_guard_without_utf8_mode(site: Path, file_path: Path, encoding: str) -> tuple[int, str]:
    """Write の JSON を UTF-8 のバイトで渡し (Claude Code と同じ)、UTF-8 を既定にしない子の python で回す。

    子の環境変数から CLAUDE_PROJECT_DIR と PYTHONUTF8 を外し、PYTHONIOENCODING に encoding を置く
    (Windows の CI の job が置く PYTHONUTF8 に左右されないため)。終わりの番号と、UTF-8 として解いた標準エラーを返す。
    """
    env = {name: value for name, value in os.environ.items() if name not in {"CLAUDE_PROJECT_DIR", "PYTHONUTF8"}}
    env["PYTHONIOENCODING"] = encoding
    payload = {
        "session_id": "test",
        "transcript_path": "",
        "cwd": str(site),
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(file_path), "content": "作り物の中身"},
    }
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        cwd=site,
        env=env,
        check=False,
    )
    return result.returncode, result.stderr.decode("utf-8", errors="replace")


@pytest.mark.parametrize("encoding", NOT_UTF8_ENCODINGS)
def test_blocks_japanese_folder_without_utf8_mode(tmp_path, encoding):
    # 標準入力の既定の文字コードが UTF-8 でなくても、日本語のフォルダの下の置き場で部品の写しへの書き込みを止める
    site = make_japanese_site(tmp_path)

    code, stderr = run_guard_without_utf8_mode(site, site / "seizou" / "core" / "views.py", encoding)

    assert code == 2, stderr
    assert "R20" in stderr


@pytest.mark.parametrize("encoding", NOT_UTF8_ENCODINGS)
def test_allows_company_place_in_japanese_folder_without_utf8_mode(tmp_path, encoding):
    # 読み方を変えても、会社の場所への書き込みまでは止めない
    site = make_japanese_site(tmp_path)

    code, stderr = run_guard_without_utf8_mode(site, site / "fields" / "models.py", encoding)

    assert code == 0, stderr
    assert stderr == ""
