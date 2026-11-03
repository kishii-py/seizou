"""受入・検収・入金・フィードバックの読む道具 (seizou.core.mcp_tools) と、会社の設定の出所と、閲覧の人の差し替えを見る。

道具は MCP を通さずに直に呼ぶ。データは部品の services で作る (検収の元の届いた注文だけ
tests/test_orders_acceptance.py の形で直に置く)。取引先・部品・題は作り物。
日数で絞る道具の試験は今日からの相対の日で作る (回す日で結果が変わらないように)。
"""

import inspect
import json
import logging
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from mcp.server.mcpserver import MCPServer

from seizou.accounts import services as accounts_services
from seizou.assistant import services as assistant_services
from seizou.assistant import tools
from seizou.core import access, mcp_tools
from seizou.core.management.commands import seizou_mcp
from seizou.feedback import services as feedback_services
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services
from tests.test_assistant_services import FAKE_KEY, script, text_reply, tool_reply

User = get_user_model()

ROOT = Path(__file__).resolve().parents[1]
#: 試験だけの合言葉 (アカウントの発行の確かめに通る長さ)
PASSWORD = "kumo-tests-2611"
ALL_ON = {"department": "on", "write": "on", "money": "on"}
#: このファイルが見る、足した読む道具 (READ_TOOLS の末尾の並び)
NEW_TOOLS = ["list_receipts"]
#: 閲覧の人を閉じ込める道具 (viewer_tools の並び)
MONEY_TOOLS = ["list_receipts"]
#: 名札に書かない語
WINDOW_WORDS = ("品目", "生産計画", "図面", "合計")


def _json(result: dict) -> dict:
    """道具の返す物が JSON にできることを見て、読み直した物を返す。"""
    return json.loads(json.dumps(result, ensure_ascii=False))


def _has_japanese(text: str) -> bool:
    return any("぀" <= char <= "ヿ" or "一" <= char <= "鿿" for char in text)


def _access_records(caplog):
    return [record for record in caplog.records if record.name == "seizou.access"]


@pytest.fixture(autouse=True)
def fresh_catalog():
    """画面の目録の控え (feedback の screen_catalog) を試験の前後で捨てる。"""
    feedback_services.screen_catalog.cache_clear()
    yield
    feedback_services.screen_catalog.cache_clear()


@pytest.fixture
def user(db):
    return User.objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def manager(db):
    return User.objects.create_superuser("admin", password="pass-for-tests-only")


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "rounding": "round"}, user
    )


@pytest.fixture
def purchase_line(user, subcontractor, item, today):
    """協力企業への発注 (送らない) の明細 1 行 (10 個 × 100)。"""
    order = purchasing_services.create_purchase_order(
        subcontractor,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=today,
        send=False,
    )
    return order.lines.get()


@pytest.fixture
def receipt(user, purchase_line, today):
    """今日の受入 4 個 (金額 400)。"""
    return purchasing_services.receive(purchase_line, Decimal("4"), today, user, note="作り物の受入")


def account(login_id, **flags):
    """権限の行つきの利用者 (部署と編集の権限は無し。flags は see_money など)。"""
    accounts_services.issue_account(login_id, PASSWORD, [], [], flags, None)
    return User.objects.get(username=login_id)


def _viewer(user, name):
    """閲覧の人 user を閉じ込めた道具 name。"""
    return {tool.__name__: tool for tool in mcp_tools.viewer_tools(user)}[name]


# --- 受入 ---


def test_list_receipts_rows_and_filters(user, purchase_line, receipt, today):
    found = _json(mcp_tools.list_receipts())
    assert found["ok"] is True
    assert (found["count"], found["money_hidden"]) == (1, False)
    row = found["items"][0]
    assert row["po_no"] == purchase_line.purchase_order.po_no
    assert row["line_no"] == 1
    assert (row["partner_code"], row["partner_name"]) == ("S001", "作り物の協力企業")
    assert (row["part_no"], row["item_name"]) == ("T-100", "作り物の軸")
    assert row["received_on"] == today.isoformat()
    assert Decimal(row["qty"]) == 4
    assert row["unit"] == "個"
    assert Decimal(row["amount"]) == 400
    assert row["note"] == "作り物の受入"
    assert row["received_by"] == "tester"
    assert row["acceptance_sent_at"] is None
    assert row["acceptance_state"] == ""
    assert row["url"] == reverse("purchasing:receipt_detail", args=[receipt.pk])
    assert "acceptance_message_id" not in row

    purchasing_services.receive(purchase_line, Decimal("2"), today - timedelta(days=2), user)
    assert mcp_tools.list_receipts()["count"] == 2
    recent = mcp_tools.list_receipts(days=1)
    assert recent["count"] == 1
    assert recent["items"][0]["received_on"] == today.isoformat()
    whole = mcp_tools.list_receipts(po_no=purchase_line.purchase_order.po_no, days=1)
    assert whole["count"] == 2
    assert mcp_tools.list_receipts(limit=1)["count"] == 1

    missing = mcp_tools.list_receipts(po_no="PO-NONE")
    assert missing["ok"] is False
    assert "PO-NONE" in missing["error"]
    assert _has_japanese(missing["error"])
    for bad in ({"days": "x"}, {"limit": "x"}):
        refused = mcp_tools.list_receipts(**bad)
        assert refused["ok"] is False, bad
        assert _has_japanese(refused["error"]), bad


def test_list_receipts_masks_money_by_viewer(settings, on, manager, receipt, caplog):
    caplog.set_level(logging.INFO, logger="seizou.access")

    hidden = _json(mcp_tools.list_receipts())
    assert hidden["money_hidden"] is True
    assert hidden["items"][0]["amount"] == access.MASK
    assert Decimal(hidden["items"][0]["qty"]) == 4

    seen = _json(_viewer(manager, "list_receipts")())
    assert seen["money_hidden"] is False
    assert Decimal(seen["items"][0]["amount"]) == 400
    clerk = _viewer(account("jimu"), "list_receipts")()
    assert (clerk["money_hidden"], clerk["items"][0]["amount"]) == (True, access.MASK)
    keeper = _viewer(account("keiri", see_money=True), "list_receipts")()
    assert keeper["money_hidden"] is False
    assert Decimal(keeper["items"][0]["amount"]) == 400

    settings.SEIZOU = {}
    shown = mcp_tools.list_receipts()
    assert shown["money_hidden"] is False
    assert Decimal(shown["items"][0]["amount"]) == 400
    assert _access_records(caplog) == []


# --- 閲覧の人の差し替え ---


def test_viewer_tools_match_plain_tools(receipt):
    plain = {tool.__name__: tool for tool in mcp_tools.READ_TOOLS}
    bound = mcp_tools.viewer_tools(None)
    assert [tool.__name__ for tool in bound] == MONEY_TOOLS
    for tool in bound:
        name = tool.__name__
        assert tool is not plain[name], name
        assert inspect.signature(tool) == inspect.signature(plain[name]), name
        assert tool.__doc__ == plain[name].__doc__, name
        assert _json(tool()) == _json(plain[name]()), name


def test_build_server_registers_bound_money_tools(on, manager, receipt, monkeypatch):
    registered = {}

    def add_tool(server, fn, *args, **kwargs):
        registered[fn.__name__] = fn

    monkeypatch.setattr(MCPServer, "add_tool", add_tool)
    read_names = {tool.__name__ for tool in mcp_tools.READ_TOOLS}

    seizou_mcp.build_server(manager)
    assert set(registered) == read_names | {tool.__name__ for tool in mcp_tools.write_tools(manager)}
    for name in MONEY_TOOLS:
        assert registered[name] is not getattr(mcp_tools, name), name
        assert registered[name].__doc__ == getattr(mcp_tools, name).__doc__, name
    assert registered["list_orders"] is mcp_tools.list_orders
    assert registered["list_receipts"]()["money_hidden"] is False
    assert mcp_tools.list_receipts()["money_hidden"] is True

    registered.clear()
    seizou_mcp.build_server(manager, read_only=True)
    assert set(registered) == read_names
    assert registered["list_receipts"] is not mcp_tools.list_receipts


def test_run_tool_passes_viewer_to_money_tools(on, manager, receipt, monkeypatch):
    plain = tools.run_tool("list_receipts", {})
    assert plain["is_error"] is False
    hidden = json.loads(plain["content"])
    assert (hidden["money_hidden"], hidden["items"][0]["amount"]) == (True, access.MASK)

    viewed = tools.run_tool("list_receipts", {}, user=manager)
    seen = json.loads(viewed["content"])
    assert seen["money_hidden"] is False
    assert Decimal(seen["items"][0]["amount"]) == 400
    assert viewed["urls"] == [reverse("purchasing:receipt_detail", args=[receipt.pk])]
    assert tools.run_tool("list_orders", {}, user=manager)["is_error"] is False
    assert tools.run_tool("list_receipts", {"po_no": "PO-NONE"}, user=manager)["is_error"] is True
    assert tools.run_tool("drop_everything", {}, user=manager) == {
        "content": "道具 drop_everything はありません。",
        "is_error": True,
        "urls": [],
    }

    # answer が窓を開いた人を run_tool に渡す。
    calls = []

    def fake_run_tool(name, arguments, user=None):
        calls.append((name, user))
        return {"content": "{}", "is_error": False, "urls": []}

    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setattr(tools, "run_tool", fake_run_tool)
    script(monkeypatch, [tool_reply("list_receipts", {}), text_reply("今日の受入は 1 件です。")])
    result = assistant_services.answer(
        "今日の受入は", page_url="/", screen_name="ダッシュボード", user=manager, history=[]
    )
    assert result["tool_calls"] == ["list_receipts"]
    assert calls == [("list_receipts", manager)]


# --- 部品の有無・目録 ---


def test_tools_refuse_when_part_missing(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_installed", lambda app: False)
    for app, result in (("purchasing", mcp_tools.list_receipts()),):
        assert result["ok"] is False, app
        assert app in result["error"], app
        assert _has_japanese(result["error"]), app


def test_new_tools_are_listed_everywhere():
    names = [tool.__name__ for tool in mcp_tools.READ_TOOLS]
    assert names[-len(NEW_TOOLS) :] == NEW_TOOLS

    described = {tool["name"]: tool["description"] for tool in tools.TOOLS}
    for name in NEW_TOOLS:
        assert described[name].strip(), name
        assert name in tools.TOOL_LABELS, name
        for word in WINDOW_WORDS:
            assert word not in tools.TOOL_LABELS[name], (name, word)
    assert "検収" in described["list_receipts"]
    assert "発注側" in described["list_receipts"]

    lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    for name in NEW_TOOLS:
        rows = [line for line in lines if line.startswith(f"| `{name}` |")]
        assert len(rows) == 1, name
        assert rows[0].endswith("| 読む |"), name
    assert sum("| 書く |" in line for line in lines) == 3
