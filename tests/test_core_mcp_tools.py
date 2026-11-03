"""MCP サーバーの道具 (seizou.core.mcp_tools) を直に呼んで見る。

道具は MCP を通さずに呼ぶ (サーバーは道具を別の thread で回すので、試験の DB の transaction の中の行が見えない)。
データは部品の services で作る。返す物は json.dumps が通る辞書か、書く道具が表を変えたかを見る。
"""

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from seizou.core import mcp_tools
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.shipping import services as shipping_services
from seizou.shipping.models import Shipment
from seizou.workorders import services as workorders_services
from seizou.workorders.models import WorkOrderStep

ORDER_DATE = date(2026, 11, 2)

#: 1 段目の読む道具の芯
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


def _json(result: dict) -> dict:
    """道具の返す物が JSON にできることを見て、読み直した物を返す。"""
    return json.loads(json.dumps(result, ensure_ascii=False))


def _has_japanese(text: str) -> bool:
    return any("぀" <= char <= "ヿ" or "一" <= char <= "鿿" for char in text)


def _tools(user) -> dict:
    return {tool.__name__: tool for tool in mcp_tools.write_tools(user)}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def world(user, settings, tmp_path):
    """得意先 2 つ・部品 2 つ (取引先ごとの品番と標準の工程つき)・受注 1 件 (明細 2 行)・出荷 1 件・指示書 1 枚。"""
    settings.MEDIA_ROOT = tmp_path / "media"
    today = timezone.localdate()
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    other = masters_services.create_partner({"code": "C002", "name": "作り物の別の得意先", "role": "customer"}, user)
    cut = masters_services.create_process(
        {"code": "P10", "name": "作り物の切断", "sort_order": 1, "use_in_plan": True}, user
    )
    bend = masters_services.create_process({"code": "P20", "name": "作り物の曲げ", "sort_order": 2}, user)
    shaft = masters_services.create_item(
        {
            "part_no": "T-100",
            "name": "作り物の軸",
            "unit": "個",
            "drawing_no": "DW-100",
            "drawing": SimpleUploadedFile("t-100.pdf", b"%PDF-1.4 fake drawing for tests"),
            "standard_price": Decimal("120"),
        },
        user,
    )
    plate = masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)
    masters_services.map_partner_item(customer, shaft, "CUST-9", user, partner_item_name="得意先の呼び名の軸")
    # 標準の工程は曲げ → 切断の順 (順の並びがコードの順と違うことを見る)。
    masters_services.set_item_processes(shaft, [bend, cut], user)
    order = orders_services.create_order(
        customer,
        [
            {"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 20)},
            {"item": plate, "qty": Decimal("5"), "unit_price": Decimal("250")},
        ],
        user,
        order_date=ORDER_DATE,
        customer_order_no="PO-1",
    )
    line = order.lines.get(line_no=1)
    shipment = shipping_services.ship(line, Decimal("4"), today, user)
    workorder = workorders_services.issue_workorder(
        line,
        Decimal("10"),
        [{"process": cut, "planned_qty": Decimal("10")}, {"process": bend, "planned_qty": Decimal("10")}],
        user,
    )
    return SimpleNamespace(
        user=user,
        today=today,
        customer=customer,
        other=other,
        cut=cut,
        bend=bend,
        shaft=shaft,
        plate=plate,
        order=order,
        line=line,
        shipment=shipment,
        workorder=workorder,
    )


def test_read_tools_return_json_ready_dicts(world):
    names = {tool.__name__ for tool in mcp_tools.READ_TOOLS}
    assert CORE_READ_TOOLS <= names

    calls = {
        "company_info": mcp_tools.company_info(),
        "list_screens": mcp_tools.list_screens(),
        "search_items": mcp_tools.search_items("T-"),
        "get_item": mcp_tools.get_item("T-100"),
        "list_orders": mcp_tools.list_orders(),
        "get_order": mcp_tools.get_order(world.order.order_no),
        "list_workorders": mcp_tools.list_workorders(),
        "list_shipments": mcp_tools.list_shipments(),
        "get_manual": mcp_tools.get_manual(),
    }
    assert set(calls) == CORE_READ_TOOLS
    for name, result in calls.items():
        assert isinstance(result, dict), name
        assert result["ok"] is True, (name, result)
        _json(result)

    info = calls["company_info"]
    assert info["settings"]["over_shipment"] == "warn"
    assert "orders" in info["parts"]
    assert any(item["url_name"] == "orders:order_list" for item in calls["list_screens"]["items"])
    assert [item["part_no"] for item in calls["search_items"]["items"]] == ["T-100", "T-200"]
    assert [item["part_no"] for item in mcp_tools.search_items("作り物 軸")["items"]] == ["T-100"]
    assert mcp_tools.search_items("T-", limit=1)["count"] == 1
    assert mcp_tools.search_items("T-", limit="abc")["ok"] is False

    workorders = calls["list_workorders"]["items"]
    assert [row["wo_no"] for row in workorders] == [world.workorder.wo_no]
    steps = workorders[0]["steps"]
    assert [(step["seq"], step["process_code"]) for step in steps] == [(1, "P10"), (2, "P20")]
    assert steps[0]["start"] is None
    assert Decimal(steps[0]["recorded_qty"]) == 0
    assert workorders[0]["url"] == reverse("workorders:workorder_detail", args=[world.workorder.pk])

    shipments = calls["list_shipments"]["items"]
    assert [row["shipment_no"] for row in shipments] == [world.shipment.shipment_no]
    assert shipments[0]["customer_code"] == "C001"
    assert shipments[0]["delivery_note_no"] == ""
    assert shipments[0]["shipped_at"] == world.today.isoformat()


def test_list_orders_shows_remaining(world, user):
    other_order = orders_services.create_order(
        world.other,
        [{"item": world.plate, "qty": Decimal("3"), "unit_price": Decimal("80")}],
        user,
        order_date=date(2026, 11, 3),
    )
    cancelled = orders_services.create_order(
        world.customer,
        [{"item": world.plate, "qty": Decimal("1"), "unit_price": Decimal("80")}],
        user,
        order_date=date(2026, 11, 1),
    )
    orders_services.cancel_order(cancelled, user)

    listed = _json(mcp_tools.list_orders())
    assert [row["order_no"] for row in listed["items"]] == [other_order.order_no, world.order.order_no]
    first = next(row for row in listed["items"] if row["order_no"] == world.order.order_no)
    assert first["customer_code"] == "C001"
    assert first["status"] == "partial"
    assert first["status_label"] == "一部出荷"
    assert first["url"] == reverse("orders:order_detail", args=[world.order.pk])
    line = first["lines"][0]
    assert line["line_no"] == 1
    assert Decimal(line["qty"]) == 10
    assert Decimal(line["shipped_qty"]) == 4
    assert Decimal(line["remaining_qty"]) == 6
    assert line["due_date"] == "2026-11-20"
    assert Decimal(first["lines"][1]["remaining_qty"]) == 5

    detail = _json(mcp_tools.get_order(world.order.order_no))
    assert detail["ok"] is True
    assert Decimal(detail["lines"][0]["shipped_qty"]) == 4
    assert Decimal(detail["lines"][0]["remaining_qty"]) == 6
    assert [row["shipment_no"] for row in detail["lines"][0]["shipments"]] == [world.shipment.shipment_no]
    assert Decimal(detail["lines"][0]["shipments"][0]["qty"]) == 4
    assert [row["wo_no"] for row in detail["lines"][0]["workorders"]] == [world.workorder.wo_no]
    assert detail["lines"][1]["shipments"] == []
    # 指示書を足しても、出荷済みの合計はふくらまない。
    workorders_services.issue_workorder(world.line, Decimal("1"), [{"process": world.cut, "planned_qty": 1}], user)
    again = mcp_tools.get_order(world.order.order_no)
    assert Decimal(again["lines"][0]["shipped_qty"]) == 4
    assert len(again["lines"][0]["workorders"]) == 2

    only_cancelled = mcp_tools.list_orders(status="cancelled")
    assert [row["order_no"] for row in only_cancelled["items"]] == [cancelled.order_no]
    only_other = mcp_tools.list_orders(customer_code="C002")
    assert [row["order_no"] for row in only_other["items"]] == [other_order.order_no]
    assert mcp_tools.list_orders(limit=1)["count"] == 1
    assert mcp_tools.list_orders(limit=0)["count"] == 1

    bad_status = mcp_tools.list_orders(status="done")
    assert bad_status["ok"] is False
    assert _has_japanese(bad_status["error"])
    unknown_customer = mcp_tools.list_orders(customer_code="C999")
    assert unknown_customer["ok"] is False
    assert "C999" in unknown_customer["error"]
    unknown_order = mcp_tools.get_order("NO-SUCH")
    assert unknown_order["ok"] is False
    assert "NO-SUCH" in unknown_order["error"]


def test_get_item_and_unknown_part_no(world):
    shaft = _json(mcp_tools.get_item("T-100"))
    assert shaft["ok"] is True
    assert shaft["name"] == "作り物の軸"
    assert shaft["drawing_no"] == "DW-100"
    assert shaft["has_drawing"] is True
    assert shaft["unit"] == "個"
    assert Decimal(shaft["standard_price"]) == 120
    assert shaft["partner_items"] == [
        {
            "partner_code": "C001",
            "partner_name": "作り物の得意先",
            "partner_part_no": "CUST-9",
            "partner_item_name": "得意先の呼び名の軸",
        }
    ]
    assert [(row["seq"], row["process_code"]) for row in shaft["processes"]] == [(1, "P20"), (2, "P10")]
    assert shaft["url"] == reverse("masters:item_detail", args=[world.shaft.pk])

    plate = mcp_tools.get_item("T-200")
    assert plate["has_drawing"] is False
    assert plate["standard_price"] is None
    assert plate["partner_items"] == []
    assert plate["processes"] == []

    unknown = mcp_tools.get_item("NOPE-1")
    assert unknown["ok"] is False
    assert "NOPE-1" in unknown["error"]
    assert _has_japanese(unknown["error"])


def test_get_manual_reads_fragment(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    folder = tmp_path / "manual"
    folder.mkdir()
    text = "<section><h2>受注の一覧</h2><p>作り物の説明</p></section>\n"
    (folder / "orders_order_list.html").write_text(text, encoding="utf-8")
    (folder / "orders_order_create.html").write_bytes(b"\xff\xfe\x00broken")

    listed = _json(mcp_tools.get_manual())
    assert listed["ok"] is True
    assert listed["intro"] == ""
    flags = {row["url_name"]: row["has_fragment"] for row in listed["items"]}
    assert flags["orders:order_list"] is True
    assert flags["shipping:shipment_list"] is False

    (folder / "index.html").write_text("<section><p>前書き</p></section>", encoding="utf-8")
    assert mcp_tools.get_manual()["intro"] == "<section><p>前書き</p></section>"

    page = _json(mcp_tools.get_manual("orders:order_list"))
    assert page["ok"] is True
    assert page["html"] == text
    assert page["message"] == ""
    assert page["url_name"] == "orders:order_list"
    assert page["name"]

    missing = mcp_tools.get_manual("shipping:shipment_list")
    assert missing["ok"] is True
    assert missing["html"] == ""
    assert "まだありません" in missing["message"]

    broken = mcp_tools.get_manual("orders:order_create")
    assert broken["html"] == ""
    assert "UTF-8" in broken["message"]
    assert "作り直" in broken["message"]

    unknown = mcp_tools.get_manual("nope:none")
    assert unknown["ok"] is False
    assert "nope:none" in unknown["error"]


def test_create_order_goes_through_services(world, user):
    create_order = _tools(user)["create_order"]
    before = Order.objects.count()
    result = _json(
        create_order(
            "C001",
            [
                {"part_no": "T-100", "qty": "3", "unit_price": "120", "due_date": "2026-11-30", "note": "作り物の備考"},
                {"part_no": "T-200", "qty": 2, "unit_price": 300.5},
            ],
            "2026-11-03",
            customer_order_no="PO-9",
        )
    )
    assert result["ok"] is True, result
    assert Order.objects.count() == before + 1
    order = Order.objects.get(order_no=result["order_no"])
    assert result["url"] == reverse("orders:order_detail", args=[order.pk])
    assert result["line_count"] == 2
    assert order.customer == world.customer
    assert order.order_date == date(2026, 11, 3)
    assert order.customer_order_no == "PO-9"
    assert order.source == "manual"
    first, second = order.lines.order_by("line_no")
    assert first.amount == first.qty * first.unit_price == Decimal("360")
    assert first.unit == "個"
    assert first.due_date == date(2026, 11, 30)
    assert first.note == "作り物の備考"
    assert second.amount == Decimal("601")  # 2 × 300.5 = 601.0
    assert second.due_date is None

    good_line = [{"part_no": "T-100", "qty": "1", "unit_price": "100"}]
    refusals = [
        create_order("C999", good_line, "2026-11-03"),
        create_order("C001", [{"part_no": "NOPE-1", "qty": "1", "unit_price": "100"}], "2026-11-03"),
        create_order("C001", good_line, "2026-13-01"),
        create_order("C001", [], "2026-11-03"),
        create_order("C001", [{"part_no": "T-100", "qty": "abc", "unit_price": "100"}], "2026-11-03"),
        create_order("C001", [{"part_no": "T-100", "qty": "1.234", "unit_price": "100"}], "2026-11-03"),
    ]
    for refused in refusals:
        assert refused["ok"] is False, refused
        assert _has_japanese(refused["error"]), refused
        _json(refused)
    assert "C999" in refusals[0]["error"]
    assert "NOPE-1" in refusals[1]["error"]
    assert "2026-13-01" in refusals[2]["error"]

    # 決まりの番号のある断り (R02) と、services の ValueError (得意先でない取引先) も文で返る。
    zero = create_order("C001", [{"part_no": "T-100", "qty": "0", "unit_price": "100"}], "2026-11-03")
    assert zero["ok"] is False
    assert zero["code"] == "R02"
    masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)
    not_customer = create_order("S001", good_line, "2026-11-03")
    assert not_customer["ok"] is False
    assert "得意先ではありません" in not_customer["error"]
    assert Order.objects.count() == before + 1


def test_ship_stop_mode_refuses_over_shipment(world, user, settings):
    settings.SEIZOU = {"over_shipment": "stop"}
    ship = _tools(user)["ship"]
    before = Shipment.objects.count()
    result = _json(ship(world.order.order_no, 1, "7", world.today.isoformat()))
    assert result["ok"] is False
    assert result["code"] == "R01"
    assert "超えます" in result["error"]
    assert Shipment.objects.count() == before

    within = ship(world.order.order_no, 1, "6", world.today.isoformat(), note="作り物の出荷")
    assert within["ok"] is True, within
    assert Decimal(within["remaining_qty"]) == 0
    assert Shipment.objects.get(shipment_no=within["shipment_no"]).note == "作り物の出荷"


def test_ship_warn_needs_confirm(world, user, settings):
    settings.SEIZOU = {"over_shipment": "warn"}
    single = orders_services.create_order(
        world.customer,
        [{"item": world.plate, "qty": Decimal("10"), "unit_price": Decimal("50")}],
        user,
        order_date=ORDER_DATE,
    )
    ship = _tools(user)["ship"]
    before = Shipment.objects.count()
    day = world.today.isoformat()

    warned = _json(ship(single.order_no, 1, "12", day))
    assert warned["ok"] is False
    assert [warning["code"] for warning in warned["warnings"]] == ["R01"]
    assert "超えます" in warned["warnings"][0]["message"]
    assert warned["hint"] == mcp_tools.CONFIRM_HINT
    assert Shipment.objects.count() == before
    # 書く道具は write_tools の中にだけあり、module の上には無い (利用者を閉じ込めない道を作らない)。
    assert not hasattr(mcp_tools, "ship")

    not_confirmed = ship(single.order_no, 1, "12", day, confirm="false")
    assert not_confirmed["ok"] is False
    assert Shipment.objects.count() == before

    saved = _json(ship(single.order_no, 1, "12", day, confirm=True))
    assert saved["ok"] is True, saved
    assert Shipment.objects.count() == before + 1
    assert saved["status"] == "shipped"
    assert saved["status_label"] == "出荷済"
    assert Decimal(saved["remaining_qty"]) == -2
    assert saved["url"] == reverse("orders:order_detail", args=[single.pk])
    single.refresh_from_db()
    assert single.status == "shipped"


def test_ship_to_cancelled_order_is_r05(world, user):
    orders_services.cancel_order(world.order, user)
    ship = _tools(user)["ship"]
    before = Shipment.objects.count()
    result = _json(ship(world.order.order_no, 1, "1", world.today.isoformat()))
    assert result["ok"] is False
    assert result["code"] == "R05"
    assert _has_japanese(result["error"])
    assert Shipment.objects.count() == before


def test_ship_refuses_unreadable_args(world, user):
    ship = _tools(user)["ship"]
    before = Shipment.objects.count()
    day = world.today.isoformat()
    cases = [
        ship("NO-SUCH", 1, "1", day),
        ship(world.order.order_no, 9, "1", day),
        ship(world.order.order_no, "a", "1", day),
        ship(world.order.order_no, 1, "abc", day),
        ship(world.order.order_no, 1, "1.234", day),
        ship(world.order.order_no, 1, "1", "11/05"),
    ]
    for refused in cases:
        assert refused["ok"] is False, refused
        assert _has_japanese(refused["error"]), refused
    zero = ship(world.order.order_no, 1, "0", day)
    assert zero["ok"] is False
    assert zero["code"] == "R02"
    assert Shipment.objects.count() == before


def test_write_tools_need_user(world):
    tools = _tools(None)
    assert set(tools) == {"create_order", "ship", "plan_step"}
    orders_before = Order.objects.count()
    shipments_before = Shipment.objects.count()
    results = [
        tools["create_order"]("C001", [{"part_no": "T-100", "qty": "1", "unit_price": "100"}], "2026-11-03"),
        tools["ship"](world.order.order_no, 1, "1", world.today.isoformat()),
        tools["plan_step"](world.workorder.wo_no, "P10", "2026-11-09", "2026-11-10"),
    ]
    for result in results:
        assert result == {"ok": False, "error": mcp_tools.NO_USER_MESSAGE}
    assert "SEIZOU_MCP_USER" in mcp_tools.NO_USER_MESSAGE
    assert Order.objects.count() == orders_before
    assert Shipment.objects.count() == shipments_before
    step = WorkOrderStep.objects.get(workorder=world.workorder, process=world.cut)
    assert step.planned_start is None
    assert step.planned_end is None


def test_plan_step_dates(world, user):
    plan_step = _tools(user)["plan_step"]
    wo_no = world.workorder.wo_no
    step = WorkOrderStep.objects.get(workorder=world.workorder, process=world.cut)

    placed = _json(plan_step(wo_no, "P10", "2026-11-09", "2026-11-11"))
    assert placed == {
        "ok": True,
        "wo_no": wo_no,
        "process_code": "P10",
        "start": "2026-11-09",
        "end": "2026-11-11",
        "url": reverse("workorders:workorder_detail", args=[world.workorder.pk]),
    }
    step.refresh_from_db()
    assert (step.planned_start, step.planned_end) == (date(2026, 11, 9), date(2026, 11, 11))

    backwards = plan_step(wo_no, "P10", "2026-11-12", "2026-11-10")
    assert backwards["ok"] is False
    assert "開始日が終了日より後です" in backwards["error"]
    one_side = plan_step(wo_no, "P10", "2026-11-12", "")
    assert one_side["ok"] is False
    assert "両方" in one_side["error"]
    unknown = plan_step(wo_no, "P99", "2026-11-12", "2026-11-13")
    assert unknown["ok"] is False
    assert "P99" in unknown["error"]
    no_workorder = plan_step("NO-SUCH", "P10", "2026-11-12", "2026-11-13")
    assert no_workorder["ok"] is False
    step.refresh_from_db()
    assert (step.planned_start, step.planned_end) == (date(2026, 11, 9), date(2026, 11, 11))

    cleared = plan_step(wo_no, "P10", "", "")
    assert cleared["ok"] is True
    assert cleared["start"] is None
    assert cleared["end"] is None
    step.refresh_from_db()
    assert (step.planned_start, step.planned_end) == (None, None)


def test_plan_step_refuses_twice_listed_process(world, user):
    twice = workorders_services.issue_workorder(
        world.line,
        Decimal("1"),
        [{"process": world.cut, "planned_qty": 1}, {"process": world.cut, "planned_qty": 1}],
        user,
    )
    result = _tools(user)["plan_step"](twice.wo_no, "P10", "2026-11-09", "2026-11-10")
    assert result["ok"] is False
    assert "2 つあります" in result["error"]
    assert not WorkOrderStep.objects.filter(workorder=twice, planned_start__isnull=False).exists()


def test_plan_board_lists_bars_and_unplanned(world, user, settings):
    # 同じ明細に指示書をもう 1 枚出し、その「切断」は日を置かずに残す。
    unplanned = workorders_services.issue_workorder(
        world.line, Decimal("2"), [{"process": world.cut, "planned_qty": Decimal("2")}], user
    )
    placed = _tools(user)["plan_step"](world.workorder.wo_no, "P10", "2026-11-10", "2026-11-11")
    assert placed["ok"] is True, placed

    board = _json(mcp_tools.plan_board("P10", start="2026-11-12"))
    assert board["ok"] is True, board
    assert board["process_code"] == "P10"
    assert board["process_name"] == "作り物の切断"
    assert [day["date"] for day in board["days"]] == [f"2026-11-{day:02d}" for day in range(9, 16)]
    assert [day["weekday"] for day in board["days"]] == list("月火水木金土日")
    assert [bar["wo_no"] for bar in board["bars"]] == [world.workorder.wo_no]
    bar = board["bars"][0]
    assert (bar["start"], bar["end"]) == ("2026-11-10", "2026-11-11")
    assert bar["part_no"] == "T-100"
    assert Decimal(bar["qty"]) == 10
    assert bar["due_date"] == "2026-11-20"
    assert [card["wo_no"] for card in board["unplanned"]] == [unplanned.wo_no]
    assert board["unplanned_count"] == 1
    assert board["unplanned"][0]["start"] is None
    assert board["marked"] == ["P10"]

    other_week = mcp_tools.plan_board("P10", start="2026-11-16")
    assert other_week["bars"] == []
    assert other_week["unplanned_count"] == 1
    assert len(mcp_tools.plan_board("P10")["days"]) == 7

    not_marked = mcp_tools.plan_board("P20")
    assert not_marked["ok"] is False
    assert _has_japanese(not_marked["error"])
    unknown = mcp_tools.plan_board("P99")
    assert unknown["ok"] is False
    assert "P99" in unknown["error"]
    bad_start = mcp_tools.plan_board("P10", start="2026-99-99")
    assert bad_start["ok"] is False
    assert "2026-99-99" in bad_start["error"]

    settings.SEIZOU = {"production_plan": False}
    turned_off = mcp_tools.plan_board("P10")
    assert turned_off["ok"] is False
    assert "生産計画を使わない設定" in turned_off["error"]


def test_list_rules_shows_modes(db, settings):
    settings.SEIZOU = {"over_shipment": "stop"}
    rules = _json(mcp_tools.list_rules())
    assert rules["ok"] is True
    modes = {(row["code"], row["action"]): row for row in rules["checks"]}
    shipping = modes[("R01", "shipping.ship")]
    assert shipping["mode"] == "stop"
    assert shipping["mode_label"] == "止める"
    assert shipping["summary"].startswith("R01")
    issue = modes[("R06", "workorders.issue_workorder")]
    assert issue["mode"] == "warn"
    assert issue["mode_label"] == "知らせる"
    assert {"R01", "R04", "R06", "R07"} <= {row["code"] for row in rules["checks"]}
    code_rules = {row["code"]: row["message"] for row in rules["code_rules"]}
    assert "R13" in code_rules
    assert "services" in code_rules["R13"]
    assert rules["note"] == mcp_tools.CORE_RULES_NOTE

    settings.SEIZOU = {"over_shipment": "off"}
    again = mcp_tools.list_rules()
    assert {row["mode"] for row in again["checks"] if row["code"] == "R01"} == {"off"}


def test_list_screens_returns_usecase():
    screens = _json(mcp_tools.list_screens())
    assert screens["ok"] is True
    items = screens["items"]
    assert items
    assert all(isinstance(item.get("usecase"), str) for item in items)
    assert all(item["url_name"] for item in items)
    home = next(item for item in items if item["url_name"] == "core:home")
    assert home["usecase"].startswith("使う人:")
