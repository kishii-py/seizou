"""管理コマンド seizou_mcp (MCP サーバーを立てる) を見る。

サーバーは回さない (stdio を回すと試験が標準入力を待つ)。build_server が登録した道具の一覧と schema を見て、
handle は MCPServer.run を置き換えて、標準出力に何も書かないことと利用者の決め方を見る。
mcp は製品の依存なので、無いときに試験を飛ばさずにそのまま import する。
"""

import sys
from io import StringIO

import anyio
import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from mcp.server.mcpserver import MCPServer

from seizou.core import mcp_tools
from seizou.core.management.commands import seizou_mcp

CORE_READ_TOOLS = {
    "company_info",
    "list_screens",
    "search_items",
    "get_item",
    "list_orders",
    "get_order",
    "list_workorders",
    "list_shipments",
    "get_manual",
}
WRITE_TOOLS = {"create_order", "ship", "plan_step"}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("operator", password="pass-for-tests-only")


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv(seizou_mcp.USER_ENV, raising=False)


def _listed(server) -> dict:
    return {tool.name: tool for tool in anyio.run(server.list_tools)}


class _Recorder:
    """MCPServer.run と build_server の代わり。呼ばれた引数を覚える。"""

    def __init__(self):
        self.runs = []
        self.users = []

    def patch_run(self, monkeypatch):
        """MCPServer.run を、transport を覚えるだけの関数に置き換える。"""
        runs = self.runs

        def run(server, transport="stdio", **kwargs):
            runs.append(transport)

        monkeypatch.setattr(MCPServer, "run", run)

    def build(self, user, *, read_only=False):
        self.users.append(user)
        recorder = self

        class _Server:
            def run(self, transport="stdio", **kwargs):
                recorder.runs.append(transport)

        return _Server()


def test_build_server_registers_tools(user):
    server = seizou_mcp.build_server(user)
    listed = _listed(server)
    expected = {tool.__name__ for tool in mcp_tools.READ_TOOLS} | {
        tool.__name__ for tool in mcp_tools.write_tools(user)
    }
    assert set(listed) == expected
    assert CORE_READ_TOOLS | WRITE_TOOLS <= set(listed)
    for name, tool in listed.items():
        assert (tool.description or "").strip(), name
        # 返す物は辞書 (structured output)。
        assert tool.output_schema is not None, name

    text = seizou_mcp.instructions(user)
    assert "operator" in text
    assert "confirm=true" in text
    assert "書く道具は画面と同じ決まりとポカよけを通ります" in text
    assert "使えません" in seizou_mcp.instructions(None)
    assert "読むだけ" in seizou_mcp.instructions(user, read_only=True)


def test_build_server_read_only(user):
    listed = _listed(seizou_mcp.build_server(user, read_only=True))
    assert not WRITE_TOOLS & set(listed)
    assert CORE_READ_TOOLS <= set(listed)


def test_write_tool_schema_has_no_user(user):
    listed = _listed(seizou_mcp.build_server(user))
    for name in WRITE_TOOLS:
        properties = listed[name].input_schema["properties"]
        assert "user" not in properties, name
        assert "confirm" in properties, name
    assert set(listed["ship"].input_schema["properties"]) == {
        "order_no",
        "line_no",
        "qty",
        "shipped_at",
        "note",
        "confirm",
    }
    lines = listed["create_order"].input_schema
    line_schema = next(iter(lines["$defs"].values()))
    assert set(line_schema["required"]) == {"part_no", "qty", "unit_price"}
    assert {"due_date", "note"} <= set(line_schema["properties"])


def test_handle_runs_stdio_and_keeps_stdout_empty(user, no_env, monkeypatch):
    recorder = _Recorder()
    recorder.patch_run(monkeypatch)
    out, err = StringIO(), StringIO()
    call_command("seizou_mcp", "--user", "operator", stdout=out, stderr=err)
    assert recorder.runs == ["stdio"]
    assert out.getvalue() == ""
    lines = err.getvalue().splitlines()
    assert len(lines) == 1
    assert "MCP サーバーを立てました" in lines[0]
    assert "operator" in lines[0]


def test_user_from_env_and_flag_wins(user, no_env, monkeypatch):
    other = get_user_model().objects.create_user("checker", password="pass-for-tests-only")
    recorder = _Recorder()
    monkeypatch.setattr(seizou_mcp, "build_server", recorder.build)

    monkeypatch.setenv(seizou_mcp.USER_ENV, "operator")
    call_command("seizou_mcp", stdout=StringIO(), stderr=StringIO())
    call_command("seizou_mcp", "--user", "checker", stdout=StringIO(), stderr=StringIO())
    monkeypatch.delenv(seizou_mcp.USER_ENV)
    call_command("seizou_mcp", stdout=StringIO(), stderr=StringIO())

    assert recorder.users == [user, other, None]
    assert recorder.runs == ["stdio", "stdio", "stdio"]


def test_unknown_or_inactive_user_stops(user, no_env, monkeypatch):
    get_user_model().objects.create_user("former", password="pass-for-tests-only", is_active=False)
    recorder = _Recorder()
    recorder.patch_run(monkeypatch)

    with pytest.raises(CommandError, match="nobody") as unknown:
        call_command("seizou_mcp", "--user", "nobody", stdout=StringIO(), stderr=StringIO())
    assert "いません" in str(unknown.value)
    with pytest.raises(CommandError, match="former") as inactive:
        call_command("seizou_mcp", "--user", "former", stdout=StringIO(), stderr=StringIO())
    assert "有効でない" in str(inactive.value)
    monkeypatch.setenv(seizou_mcp.USER_ENV, "nobody")
    with pytest.raises(CommandError, match="nobody"):
        call_command("seizou_mcp", stdout=StringIO(), stderr=StringIO())
    assert recorder.runs == []


def test_missing_mcp_package_stops(user, no_env, monkeypatch):
    monkeypatch.setitem(sys.modules, "mcp.server.mcpserver", None)
    with pytest.raises(CommandError) as stopped:
        seizou_mcp.build_server(user)
    assert str(stopped.value) == seizou_mcp.MISSING_PACKAGE_MESSAGE
    assert "uv sync" in str(stopped.value)
    out = StringIO()
    with pytest.raises(CommandError):
        call_command("seizou_mcp", "--user", "operator", stdout=out, stderr=StringIO())
    assert out.getvalue() == ""
