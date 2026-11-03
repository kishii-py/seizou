"""受入・検収・入金・フィードバックの読む道具 (seizou.core.mcp_tools) と、会社の設定の出所と、閲覧の人の差し替えを見る。

道具は MCP を通さずに直に呼ぶ。データは部品の services で作る (検収の元の届いた注文だけ
tests/test_orders_acceptance.py の形で直に置く)。取引先・部品・題は作り物。
日数で絞る道具の試験は今日からの相対の日で作る (回す日で結果が変わらないように)。
"""

import json
import logging
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.accounts import services as accounts_services
from seizou.assistant import tools
from seizou.core import access, mcp_tools
from seizou.feedback import services as feedback_services
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services

User = get_user_model()

ROOT = Path(__file__).resolve().parents[1]
#: 試験だけの合言葉 (アカウントの発行の確かめに通る長さ)
PASSWORD = "kumo-tests-2611"
ALL_ON = {"department": "on", "write": "on", "money": "on"}
#: このファイルが見る、足した読む道具 (READ_TOOLS の末尾の並び)
NEW_TOOLS = ["list_receipts"]
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


def test_list_receipts_masks_money_by_viewer(settings, on, receipt, caplog):
    caplog.set_level(logging.INFO, logger="seizou.access")

    hidden = _json(mcp_tools.list_receipts())
    assert hidden["money_hidden"] is True
    assert hidden["items"][0]["amount"] == access.MASK
    assert Decimal(hidden["items"][0]["qty"]) == 4

    settings.SEIZOU = {}
    shown = mcp_tools.list_receipts()
    assert shown["money_hidden"] is False
    assert Decimal(shown["items"][0]["amount"]) == 400
    assert _access_records(caplog) == []


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
