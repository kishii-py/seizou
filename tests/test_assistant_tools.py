"""AI チャットの読むだけの道具 (seizou.assistant.tools) のテスト。

道具は API を使わないが、ほかの AI チャットの試験と同じく、autouse の fixture が _ask_claude を
「呼ばれたら AssertionError」に差し替える。データは試験の中で部品の services の関数で作る。品番と会社名は作り物。
"""

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.assistant import services, tools
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
TOOL_NAMES = [
    "search_items",
    "get_item",
    "list_open_orders",
    "get_order",
    "list_workorders",
    "list_recent_shipments",
    "get_manual",
    "list_screens",
]


def refuse_api(model, system, messages, tools):
    raise AssertionError("テストが本物の API を呼ぼうとした")


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(services, "_ask_claude", refuse_api)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "AS01", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def other_customer(user):
    return masters_services.create_partner({"code": "AS02", "name": "作り物の別の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item(
        {
            "part_no": "AS-100",
            "name": "作り物の軸",
            "unit": "個",
            "drawing_no": "DW-AS-100",
            "standard_price": Decimal("120.50"),
        },
        user,
    )


@pytest.fixture
def plate(user):
    return masters_services.create_item({"part_no": "BX-200", "name": "作り物の板", "unit": "枚"}, user)


@pytest.fixture
def order(user, customer, shaft, plate):
    return orders_services.create_order(
        customer,
        [
            {"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 20)},
            {"item": plate, "qty": Decimal("5"), "unit_price": Decimal("200"), "due_date": date(2026, 11, 10)},
        ],
        user,
        order_date=ORDER_DATE,
    )


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


@pytest.fixture
def bend(user):
    return masters_services.create_process({"code": "BEND", "name": "曲げ", "sort_order": 2}, user)


@pytest.fixture
def site(settings, tmp_path):
    """会社の置き場 (一時の置き場) と、空の manual/。"""
    settings.BASE_DIR = tmp_path
    folder = tmp_path / "manual"
    folder.mkdir()
    return folder


def test_tools_and_functions_match():
    assert [tool["name"] for tool in tools.TOOLS] == TOOL_NAMES
    assert list(tools.FUNCTIONS) == TOOL_NAMES
    for tool in tools.TOOLS:
        assert tool["description"].strip()
        assert tool["input_schema"]["type"] == "object"
        assert isinstance(tool["input_schema"]["properties"], dict)
        assert isinstance(tool["input_schema"]["required"], list)


@pytest.mark.django_db
def test_search_items_uses_masters_search(shaft, plate):
    result = tools.search_items("AS-1")

    assert result["total"] == 1
    assert result["items"] == [
        {
            "part_no": "AS-100",
            "name": "作り物の軸",
            "drawing_no": "DW-AS-100",
            "unit": "個",
            "url": reverse("masters:item_detail", args=[shaft.pk]),
        }
    ]
    assert tools.search_items("   ") == {"total": 0, "items": []}
    assert tools.search_items("作り物", limit=999)["total"] == 2
    assert len(tools.search_items("作り物", limit=0)["items"]) == 1


@pytest.mark.django_db
def test_get_item_with_processes(user, shaft, cut, bend):
    masters_services.set_item_processes(shaft, [bend, cut], user)

    result = tools.get_item("AS-100")

    assert result["part_no"] == "AS-100"
    assert result["standard_price"] == "120.50"
    assert result["processes"] == ["曲げ", "切断"]
    assert result["url"] == reverse("masters:item_detail", args=[shaft.pk])
    assert tools.get_item("AS-999") == {}


@pytest.mark.django_db
def test_list_open_orders_remaining(user, order, other_customer, shaft):
    first_line = order.lines.get(line_no=1)
    shipping_services.ship(first_line, Decimal("10"), date(2026, 11, 5), user)
    other = orders_services.create_order(
        other_customer,
        [{"item": shaft, "qty": Decimal("3"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )

    result = tools.list_open_orders()

    assert result["total"] == 2
    first, second = result["lines"]
    assert (first["order_no"], first["line_no"]) == (order.order_no, 2)
    assert first["remaining"] == "5.00"
    assert first["shipped"] == "0.00"
    assert first["customer_code"] == "AS01"
    assert first["due_date"] == "2026-11-10"
    assert first["url"] == reverse("orders:order_detail", args=[order.pk])
    # 納期の無い明細は後。
    assert (second["order_no"], second["due_date"]) == (other.order_no, None)

    only = tools.list_open_orders(customer_code="AS02")
    assert [line["order_no"] for line in only["lines"]] == [other.order_no]

    orders_services.cancel_order(other, user)
    after = tools.list_open_orders()
    assert [line["order_no"] for line in after["lines"]] == [order.order_no]


@pytest.mark.django_db
def test_get_order_lines(user, order):
    first_line = order.lines.get(line_no=1)
    shipping_services.ship(first_line, Decimal("4"), date(2026, 11, 5), user)

    result = tools.get_order(order.order_no)

    assert result["order_no"] == order.order_no
    assert result["customer_name"] == "作り物の得意先"
    assert result["order_date"] == "2026-11-02"
    assert result["status"] == Order.objects.get(pk=order.pk).get_status_display()
    assert result["url"] == reverse("orders:order_detail", args=[order.pk])
    assert [line["line_no"] for line in result["lines"]] == [1, 2]
    line = result["lines"][0]
    assert (line["qty"], line["shipped"], line["remaining"]) == ("10.00", "4.00", "6.00")
    assert line["unit_price"] == "100.00"
    assert line["amount"] == "1000.00"
    assert tools.get_order("SO-NOT-THERE") == {}


@pytest.mark.django_db
def test_list_workorders_and_shipments(user, order, cut):
    line = order.lines.get(line_no=1)
    workorder = workorders_services.issue_workorder(
        line, Decimal("3"), [{"process": cut, "planned_qty": Decimal("3")}], user
    )
    today = timezone.localdate()
    recent = shipping_services.ship(line, Decimal("2"), today, user)
    shipping_services.ship(line, Decimal("1"), today - timedelta(days=40), user)

    issued = tools.list_workorders()
    assert issued["total"] == 1
    row = issued["workorders"][0]
    assert row["wo_no"] == workorder.wo_no
    assert row["status"] == "発行"
    assert row["order_no"] == order.order_no
    assert row["qty"] == "3.00"
    assert row["url"] == reverse("workorders:workorder_detail", args=[workorder.pk])
    assert tools.list_workorders(status="cancelled")["total"] == 0
    assert tools.list_workorders(status="issued")["total"] == 1

    shipped = tools.list_recent_shipments(days=30)
    assert [s["shipment_no"] for s in shipped["shipments"]] == [recent.shipment_no]
    assert shipped["shipments"][0]["delivery_note_no"] == ""
    assert shipped["shipments"][0]["shipped_at"] == today.isoformat()
    assert shipped["shipments"][0]["url"] == reverse("shipping:shipment_detail", args=[recent.pk])
    assert tools.list_recent_shipments(days=999)["total"] == 2


@pytest.mark.django_db
def test_get_manual_and_list_screens(site):
    (site / "orders_order_list.html").write_text(
        "<section><h2>受注</h2><p>使い方の字</p></section>", encoding="utf-8"
    )
    (site / "masters_item_list.html").write_text(
        "<section><p>" + "あ" * 5000 + "</p></section>", encoding="utf-8"
    )

    result = tools.get_manual("orders:order_list")
    assert result["url_name"] == "orders:order_list"
    assert result["url"] == "/orders/"
    assert "使い方の字" in result["text"]
    assert "<" not in result["text"]
    assert "note" not in result

    by_name = tools.get_manual(result["name"])
    assert by_name["url_name"] == "orders:order_list"

    assert len(tools.get_manual("masters:item_list")["text"]) == 4000

    missing = tools.get_manual("shipping:shipment_list")
    assert missing["text"] == ""
    assert missing["note"]
    assert tools.get_manual("無い画面") == {}

    screens = tools.list_screens()
    by_url_name = {s["url_name"]: s for s in screens["screens"]}
    assert screens["total"] == len(screens["screens"])
    assert by_url_name["orders:order_list"]["url"] == "/orders/"
    assert by_url_name["orders:order_detail"]["url"] is None


@pytest.mark.django_db
def test_results_are_json(user, order, cut, site):
    line = order.lines.get(line_no=1)
    workorders_services.issue_workorder(line, Decimal("3"), [{"process": cut, "planned_qty": Decimal("3")}], user)
    shipping_services.ship(line, Decimal("2"), timezone.localdate(), user)

    results = [
        tools.search_items("AS"),
        tools.get_item("AS-100"),
        tools.list_open_orders(),
        tools.get_order(order.order_no),
        tools.list_workorders(),
        tools.list_recent_shipments(),
        tools.get_manual("orders:order_list"),
        tools.list_screens(),
    ]

    for result in results:
        assert result
        json.dumps(result, ensure_ascii=False)
    assert isinstance(results[3]["lines"][0]["amount"], str)
    assert isinstance(results[3]["order_date"], str)


@pytest.mark.django_db
def test_run_tool_errors(shaft, monkeypatch):
    unknown = tools.run_tool("drop_everything", {})
    assert unknown == {"content": "道具 drop_everything はありません。", "is_error": True, "urls": []}

    for name, arguments in [
        ("search_items", {"query": "AS", "limit": "x"}),
        ("search_items", {}),
        ("search_items", {"query": "AS", "unknown": 1}),
        ("get_item", {"part_no": 5}),
        ("list_workorders", {"status": "bogus"}),
        ("list_recent_shipments", {"days": True}),
        ("list_screens", ["not", "a", "dict"]),
    ]:
        outcome = tools.run_tool(name, arguments)
        assert outcome["is_error"] is True
        assert outcome["content"] == f"道具 {name} の引数が違います。"
        assert outcome["urls"] == []

    def broken():
        raise LookupError("中身は文に入れない")

    monkeypatch.setitem(tools.FUNCTIONS, "list_screens", broken)
    outcome = tools.run_tool("list_screens", None)
    assert outcome == {"content": "list_screens の結果を読めませんでした。", "is_error": True, "urls": []}

    ok = tools.run_tool("get_item", {"part_no": "AS-100"})
    assert ok["is_error"] is False
    assert json.loads(ok["content"])["part_no"] == "AS-100"
    assert ok["urls"] == [reverse("masters:item_detail", args=[shaft.pk])]
