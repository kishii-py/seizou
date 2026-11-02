"""プラグインの MCP サーバーの登録 (plugin/.mcp.json) と起こし役 (plugin/mcp/start.py) のテスト。

標準の library だけを使い、Django と DB を読まない。起こし役は subprocess で回す (tests/test_plugin_guard_core.py と同じ形)。
一時のフォルダに作り物の会社の置き場 (受けた引数と今のフォルダをファイルに書くだけの manage.py・answers.yaml・
起動の 1 行の python をこの試験の python にした README.md) を作る。
試験を回す Claude Code の環境に CLAUDE_PROJECT_DIR があるかもしれないので、subprocess の環境変数から外してから回す。
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
MCP_JSON = PLUGIN / ".mcp.json"
START = PLUGIN / "mcp" / "start.py"
MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"

#: 作り物の manage.py。受けた引数と今のフォルダを record.json に書く。引数に --echo があれば標準入力を標準出力へ写す。
FAKE_MANAGE = """\
import json
import os
import sys
from pathlib import Path

Path(__file__).with_name("record.json").write_text(
    json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}), encoding="utf-8"
)
if "--echo" in sys.argv:
    sys.stdout.write(sys.stdin.read())
"""


def _python_in_start_line() -> str:
    """seizou new の起動の 1 行と同じ書き方 (空白があれば " で囲む)。"""
    python = sys.executable
    return f'"{python}"' if " " in python else python


@pytest.fixture
def site(tmp_path: Path) -> Path:
    folder = (tmp_path / "seizou-sites" / "sample_co").resolve()
    folder.mkdir(parents=True)
    (folder / "manage.py").write_text(FAKE_MANAGE, encoding="utf-8")
    (folder / "answers.yaml").write_text("id: sample_co\n", encoding="utf-8")
    (folder / "README.md").write_text(
        "# 作り物の会社のシステム (sample_co)\n\n## 起動\n\n```\n"
        f"cd {folder} && {_python_in_start_line()} manage.py runserver 8001\n```\n",
        encoding="utf-8",
    )
    return folder


def _env(**extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "CLAUDE_PROJECT_DIR"}
    env.update(extra)
    return env


def run_start(cwd: Path, *args: str, env: dict[str, str] | None = None, stdin: str = ""):
    return subprocess.run(
        [sys.executable, str(START), *args],
        cwd=cwd,
        env=env if env is not None else _env(),
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def _record(site: Path) -> dict:
    return json.loads((site / "record.json").read_text(encoding="utf-8"))


def test_mcp_json_registers_seizou():
    data = json.loads(MCP_JSON.read_text(encoding="utf-8"))
    assert set(data) == {"mcpServers"}
    assert set(data["mcpServers"]) == {"seizou"}
    server = data["mcpServers"]["seizou"]
    assert set(server) == {"command", "args"}
    assert server["command"] == "uv"
    assert server["args"] == ["run", "--no-project", "python", "${CLAUDE_PLUGIN_ROOT}/mcp/start.py"]
    # 目録 (plugin.json) に mcpServers を足さない。鍵は 3 つのまま。
    assert set(json.loads(MANIFEST.read_text(encoding="utf-8"))) == {"name", "version", "description"}


def test_start_uses_only_standard_library():
    tree = ast.parse(START.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    assert modules
    assert modules <= set(sys.stdlib_module_names) | {"__future__"}, modules


def test_start_runs_site_python(site: Path):
    result = run_start(site, "--read-only")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    record = _record(site)
    assert record["argv"] == ["seizou_mcp", "--read-only"]
    assert Path(record["cwd"]).resolve() == site


def test_start_uses_claude_project_dir(site: Path, tmp_path: Path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = run_start(outside, env=_env(CLAUDE_PROJECT_DIR=str(site)))
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    record = _record(site)
    assert record["argv"] == ["seizou_mcp"]
    assert Path(record["cwd"]).resolve() == site


def test_start_passes_stdin_and_stdout_to_child(site: Path):
    message = '{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n'
    result = run_start(site, "--echo", stdin=message)
    assert result.returncode == 0, result.stderr
    # 子の標準出力だけが出る (起こし役は 1 字も足さない)。
    assert result.stdout == message


def test_start_outside_site_exits_1(site: Path, tmp_path: Path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = run_start(outside)
    assert result.returncode == 1
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    assert "会社の置き場" in lines[0]

    (site / "README.md").write_text("# 起動の 1 行の無い README\n", encoding="utf-8")
    broken = run_start(site)
    assert broken.returncode == 1
    assert broken.stdout == ""
    assert "seizou new" in broken.stderr
    assert not (site / "record.json").exists()
