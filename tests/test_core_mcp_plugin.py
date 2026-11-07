"""プラグインの MCP サーバーの登録 (plugin/.mcp.json) と起こし役 (plugin/mcp/start.py) のテスト。

標準の library だけを使い、Django と DB を読まない。起こし役は subprocess で回す (tests/test_plugin_guard_core.py と同じ形)。
一時のフォルダに作り物の会社の置き場 (受けた引数と今のフォルダをファイルに書くだけの manage.py・answers.yaml・
起動の 1 行の python を無い道にした README.md) を作り、置き場の一覧 (seizou.site_registry。一時のフォルダの SEIZOU_HOME) に
この試験の python で登録する。起こし役が README.md でなく一覧の python を使うことは、README の python が無い道なのに子が動くことで見る。
試験を回す Claude Code の環境に CLAUDE_PROJECT_DIR があるかもしれないので、subprocess の環境変数から外してから回す。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from seizou import site_registry

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


#: README.md の起動の 1 行に書く、無い python の道 (起こし役が README を読めば、子は動かない)
README_PYTHON = "/no/such/folder/python"


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """置き場の一覧の置き場 (SEIZOU_HOME)。この試験の process と子の process (_env) の両方が使う。"""
    folder = tmp_path / "seizou-home"
    monkeypatch.setenv("SEIZOU_HOME", str(folder))
    return folder


def _make_site(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "manage.py").write_text(FAKE_MANAGE, encoding="utf-8")
    (folder / "answers.yaml").write_text("id: sample_co\n", encoding="utf-8")
    (folder / "README.md").write_text(
        "# 作り物の会社のシステム (sample_co)\n\n## 起動\n\n```\n"
        f"cd {folder} && {README_PYTHON} manage.py runserver 8001\n```\n",
        encoding="utf-8",
    )
    return folder


@pytest.fixture
def unregistered_site(tmp_path: Path, home: Path) -> Path:
    """印 (manage.py と answers.yaml) はあるが、一覧に登録していない置き場 (人から受け取ったフォルダの形)。"""
    return _make_site((tmp_path / "received" / "sample_co").resolve())


@pytest.fixture
def site(tmp_path: Path, home: Path) -> Path:
    """一覧にこの試験の python で登録した置き場。"""
    folder = _make_site((tmp_path / "seizou-sites" / "sample_co").resolve())
    site_registry.register_site(folder, sys.executable)
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


def test_start_runs_registered_python_without_reading_readme(site: Path):
    # README.md の起動の 1 行の python は無い道。一覧の python で起こすので、子は動く
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


def test_start_does_not_run_unregistered_site(unregistered_site: Path, site: Path):
    # 人から受け取ったフォルダ: README.md の起動の 1 行をこの試験の python にしても、一覧に無ければ起こさない
    (unregistered_site / "README.md").write_text(
        f"```\ncd {unregistered_site} && {sys.executable} manage.py runserver 8001\n```\n", encoding="utf-8"
    )
    for result in (run_start(unregistered_site), run_start(site, env=_env(CLAUDE_PROJECT_DIR=str(unregistered_site)))):
        assert result.returncode == 1
        assert result.stdout == ""
        lines = result.stderr.splitlines()
        assert lines == ["この置き場は登録されていません。`seizou ai-docs <置き場>` で登録してください。"]
    assert not (unregistered_site / "record.json").exists()
    assert not (site / "record.json").exists()


def test_start_unreadable_registry_runs_nothing(site: Path, home: Path):
    (home / "sites.json").write_text("{壊れた JSON", encoding="utf-8")
    result = run_start(site)
    assert result.returncode == 1
    assert "登録されていません" in result.stderr
    assert not (site / "record.json").exists()


def _load_start():
    spec = importlib.util.spec_from_file_location("seizou_plugin_mcp_start", START)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_start_uses_python_from_registry(tmp_path: Path, home: Path, monkeypatch):
    # subprocess.run を作り物に差し替え、一覧の python で manage.py seizou_mcp を打つことを見る
    folder = _make_site((tmp_path / "seizou-sites" / "sample_co").resolve())
    registered = str(tmp_path / "作り物の venv" / "bin" / "python")
    site_registry.register_site(folder, registered)
    start = _load_start()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(start.subprocess, "run", fake_run)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(folder))

    assert start.main(["--read-only"]) == 7
    assert calls == [([registered, "manage.py", "seizou_mcp", "--read-only"], {"cwd": folder, "check": False})]
