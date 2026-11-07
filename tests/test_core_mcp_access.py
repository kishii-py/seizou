"""MCP と AI チャットの道具 (seizou.core.mcp_tools) の権限の判定を見る (精査 A2 の直し。02 の 7 節)。

道具は MCP を通さずに直に呼ぶ。データは tests/test_core_mcp_tools.py の world を借り、人は accounts の services で発行する。
人の名前は作り物 (genba = 権限なし・金額なし、jimu = 伝票編集・金額なし、keiri = 金額あり、a2_manager = アカウント管理だけ)。
判定を有効にする試験は settings の fixture で SEIZOU を替える (tests/settings.py は SEIZOU = {} のまま = 3 つとも記録だけ)。
"""

import json
import logging
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.accounts import services as accounts_services
from seizou.assistant import tools as assistant_tools
from seizou.core import access, mcp_tools
from seizou.feedback import services as feedback_services
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


def _viewer(person, name):
    """閲覧の人 person を閉じ込めた読む道具 name。"""
    return {tool.__name__: tool for tool in mcp_tools.viewer_tools(person)}[name]


def _money_reads(read) -> dict:
    """部品・受注の一覧・受注の 1 件を読む道具で読み、金額の欄と数の欄を集める。read(name) は道具を返す。"""

    def lines(result):
        return [line for order in result.get("items", [result]) for line in order["lines"]]

    item = read("get_item")("T-100")
    listed = read("list_orders")()
    order = read("get_order")(listed["items"][0]["order_no"])
    return {
        "hidden": {item["money_hidden"], listed["money_hidden"], order["money_hidden"]},
        "standard_price": item["standard_price"],
        "unit_prices": [line["unit_price"] for line in lines(listed) + lines(order)],
        "amounts": [line["amount"] for line in lines(listed) + lines(order)],
        "qty": [(line["qty"], line["shipped_qty"], line["remaining_qty"]) for line in lines(order)],
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

    # 金額が主な登録は受注と請求書と入金と単価表の行の追加と編集と削除だけ (MONEY_ACTIONS)。出荷は伝票編集の権限で通る。
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


# --- 読む道具の金額 (精査 A2 の S1) ---


def test_read_tools_mask_money_by_viewer(settings, on, world, admin, caplog):
    caplog.set_level(logging.INFO, logger="seizou.access")
    plain = {tool.__name__: tool for tool in mcp_tools.READ_TOOLS}

    hidden = _money_reads(plain.get)
    assert hidden["hidden"] == {True}
    assert hidden["standard_price"] == access.MASK
    assert set(hidden["unit_prices"]) == set(hidden["amounts"]) == {access.MASK}
    # 数は金額でないので見える (明細 1 = 10 個のうち 4 個出荷済み)。
    assert [tuple(Decimal(value) for value in row) for row in hidden["qty"]] == [(10, 4, 6), (5, 0, 5)]
    # 標準単価の無い部品も「—」(見せないときは無いことも知らせない)。
    assert mcp_tools.get_item("T-200")["standard_price"] == access.MASK

    keiri = account("keiri", see_money=True)
    for person in (admin, keiri):
        seen = _money_reads(lambda name, person=person: _viewer(person, name))
        assert seen["hidden"] == {False}, person
        assert Decimal(seen["standard_price"]) == Decimal("120"), person
        assert [Decimal(value) for value in seen["unit_prices"]] == [Decimal("100"), Decimal("250")] * 2, person
        assert [Decimal(value) for value in seen["amounts"]] == [Decimal("1000"), Decimal("1250")] * 2, person
    genba = account("genba")
    masked = _money_reads(lambda name: _viewer(genba, name))
    assert masked["hidden"] == {True}
    assert masked["standard_price"] == access.MASK
    assert set(masked["unit_prices"]) == {access.MASK}

    settings.SEIZOU = {}
    for read in (plain.get, lambda name: _viewer(genba, name)):
        shown = _money_reads(read)
        assert shown["hidden"] == {False}
        assert Decimal(shown["standard_price"]) == Decimal("120")
        assert [Decimal(value) for value in shown["amounts"]] == [Decimal("1000"), Decimal("1250")] * 2
    assert mcp_tools.get_item("T-200")["standard_price"] is None
    # 読む道具は金額を見たことを記録しない (_money_shown は record=False)。
    assert access_records(caplog) == []


def test_run_tool_masks_order_money_for_viewer(on, world, admin):
    genba = account("genba")
    arguments = {"order_no": world.order.order_no}

    masked = assistant_tools.run_tool("get_order", arguments, user=genba)
    assert masked["is_error"] is False
    body = json.loads(masked["content"])
    assert body["money_hidden"] is True
    assert {line["unit_price"] for line in body["lines"]} == {access.MASK}

    seen = json.loads(assistant_tools.run_tool("get_order", arguments, user=admin)["content"])
    assert seen["money_hidden"] is False
    assert [Decimal(line["unit_price"]) for line in seen["lines"]] == [Decimal("100"), Decimal("250")]


# --- フィードバックの道具 (精査 A2 の S8 の道具の側) ---


def _ids(result) -> list:
    return [row["id"] for row in result["items"]]


def test_feedback_tools_show_own_rows_to_non_manager(admin):
    genba = account("genba")
    jimu = account("jimu", permissions=["slip_edit"])
    own = feedback_services.create_feedback(
        {"kind": "common", "title": "一覧に納期の列がほしい", "problem": "納期を見るのに、毎回詳細を開いている。"}, genba
    )
    other = feedback_services.create_feedback(
        {"kind": "bug", "title": "受注の一覧で並びが戻る", "problem": "詳細から戻ると、並びが元に戻る。"}, jimu
    )

    assert _ids(_viewer(genba, "list_feedback")()) == [own.pk]
    assert _viewer(genba, "get_feedback")(own.pk)["ok"] is True
    hidden = _viewer(genba, "get_feedback")(other.pk)
    missing = _viewer(genba, "get_feedback")(999999)
    assert hidden["ok"] is False
    # 見えない行は、無い番号と同じ文 (番号の字だけ違う)。ほかの人の下書きがあることを知らせない。
    assert hidden["error"] == missing["error"].replace("999999", str(other.pk))

    a2_manager = account("a2_manager", manage_accounts=True)
    for person in (a2_manager, admin):
        assert _ids(_viewer(person, "list_feedback")()) == [other.pk, own.pk], person
        assert _viewer(person, "get_feedback")(other.pk)["ok"] is True, person
    # 利用者を決めずに起こした道具 (READ_TOOLS) は今までどおり全部。
    assert _ids(mcp_tools.list_feedback()) == [other.pk, own.pk]
    assert mcp_tools.get_feedback(other.pk)["ok"] is True
