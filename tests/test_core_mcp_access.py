"""MCP と AI チャットの道具 (seizou.core.mcp_tools) の権限の判定を見る (精査 A2 の直し。02 の 7 節)。

道具は MCP を通さずに直に呼ぶ。データは tests/test_core_mcp_tools.py の world を借り、人は accounts の services で発行する。
人の名前は作り物 (genba = 権限なし・金額なし、jimu = 伝票編集・金額なし、keiri = 金額あり、a2_manager = アカウント管理だけ)。
判定を有効にする試験は settings の fixture で SEIZOU を替える (tests/settings.py は SEIZOU = {} のまま = 3 つとも記録だけ)。
"""

import logging

import pytest
from django.contrib.auth import get_user_model

from seizou.accounts import services as accounts_services
from seizou.core import mcp_tools
from seizou.orders.models import Order
from seizou.shipping.models import Shipment
from seizou.workorders.models import WorkOrderStep
from tests import test_core_mcp_tools

User = get_user_model()

# world と、world が使う user の fixture を借りる (pytest は module の名前で fixture を見つける)。
user = test_core_mcp_tools.user
world = test_core_mcp_tools.world

#: 試験だけの合言葉 (アカウントの発行の確かめに通る長さ)
PASSWORD = "kumo-tests-2611"
ALL_ON = {"department": "on", "write": "on", "money": "on"}
#: 書く道具を通す引数 (world の受注と指示書。どれも検査の層を通る)
NEW_ORDER_LINES = [{"part_no": "T-100", "qty": "1", "unit_price": "100"}]
NEW_ORDER_DATE = "2026-11-03"
PLAN_START = "2026-11-09"
PLAN_END = "2026-11-10"


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


@pytest.fixture
def admin(db):
    return User.objects.create_superuser("admin", password=PASSWORD)


def account(login_id, *, permissions=(), **flags):
    """権限の行つきの利用者 (部署は無し。flags は see_money など)。控えの無い利用者の物を返す。"""
    accounts_services.issue_account(login_id, PASSWORD, [], list(permissions), flags, None)
    return User.objects.get(username=login_id)


def access_records(caplog):
    return [record for record in caplog.records if record.name == "seizou.access"]


def _tools(person) -> dict:
    return {tool.__name__: tool for tool in mcp_tools.write_tools(person)}


def _write_all(tools, world) -> dict:
    """書く道具 3 つを、world の正しい引数で 1 回ずつ呼ぶ。"""
    return {
        "create_order": tools["create_order"]("C001", NEW_ORDER_LINES, NEW_ORDER_DATE),
        "ship": tools["ship"](world.order.order_no, 1, "1", world.today.isoformat()),
        "plan_step": tools["plan_step"](world.workorder.wo_no, "P10", PLAN_START, PLAN_END),
    }


def _planned(world):
    step = WorkOrderStep.objects.get(workorder=world.workorder, process=world.cut)
    return step.planned_start, step.planned_end


# --- 書く道具 (精査 A2 の B1) ---


def test_write_tools_refuse_without_permission(on, world, admin):
    genba = account("genba")
    orders_before = Order.objects.count()
    shipments_before = Shipment.objects.count()

    results = _write_all(_tools(genba), world)

    for name, result in results.items():
        assert result["ok"] is False, name
        assert result["code"] == "権限", name
        assert "編集の権限がありません" in result["error"], name
        assert "アカウント管理の担当者" in result["error"], name
    assert "受注の編集の権限がありません" in results["create_order"]["error"]
    assert "出荷と納品書の編集の権限がありません" in results["ship"]["error"]
    assert "指示書の編集の権限がありません" in results["plan_step"]["error"]
    assert Order.objects.count() == orders_before
    assert Shipment.objects.count() == shipments_before
    assert _planned(world) == (None, None)

    # 権限の無い人には、引数が誤っていても権限の文を先に返す (AI チャットの承認と同じ順)。
    wrong = _tools(genba)["ship"]("NO-SUCH", 1, "x", "")
    assert wrong["code"] == "権限"

    saved = _write_all(_tools(admin), world)
    for name, result in saved.items():
        assert result["ok"] is True, (name, result)
    assert Order.objects.count() == orders_before + 1
    assert Shipment.objects.count() == shipments_before + 1
    assert _planned(world) != (None, None)


def test_create_order_refused_without_money(on, world):
    jimu = account("jimu", permissions=["slip_edit"])
    orders_before = Order.objects.count()
    tools = _tools(jimu)

    refused = tools["create_order"]("C001", NEW_ORDER_LINES, NEW_ORDER_DATE)
    assert refused["ok"] is False
    assert refused["code"] == "権限"
    assert "受注は金額が主な内容のため" in refused["error"]
    assert Order.objects.count() == orders_before

    # 金額が主な登録は受注と請求書と入金だけ (MONEY_ACTIONS)。出荷は伝票編集の権限で通る。
    shipped = tools["ship"](world.order.order_no, 1, "1", world.today.isoformat())
    assert shipped["ok"] is True, shipped


def test_write_tools_log_mode_records_and_saves(world, caplog):
    caplog.set_level(logging.WARNING, logger="seizou.access")
    genba = account("genba")
    orders_before = Order.objects.count()

    result = _tools(genba)["create_order"]("C001", NEW_ORDER_LINES, NEW_ORDER_DATE)

    assert result["ok"] is True, result
    assert Order.objects.count() == orders_before + 1
    messages = [record.getMessage() for record in access_records(caplog)]
    wanted = "記録だけ (編集の権限): genba が orders.create_order を保存しようとした"
    assert sum(message.startswith(wanted) for message in messages) == 1, messages
