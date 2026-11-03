"""AI チャットの道具の形と回し方 (seizou.assistant.tools) のテスト。

道具の中身は seizou.core.mcp_tools が正で、その中身は tests/test_core_mcp_tools.py と
tests/test_core_mcp_manuals.py が見る。ここでは、Claude に渡す schema が mcp の関数の引数と 1 対 1 であることと、
run_tool が mcp の返す物の形を吸収することを見る。
道具は API を使わないが、ほかの AI チャットの試験と同じく、autouse の fixture が _ask_claude を
「呼ばれたら AssertionError」に差し替える。データは部品の services で作る。品番と会社名は作り物。
"""

import inspect
import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.assistant import services, tools
from seizou.core import mcp_tools
from seizou.masters import services as masters_services

#: 名札と要約に書かない字 (画面の全体の字を数えるか、無いことを見る既にある試験に当たる)
WINDOW_WORDS = ("品目", "生産計画", "図面", "合計")


def refuse_api(model, system, messages, tools):
    raise AssertionError("テストが本物の API を呼ぼうとした")


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(services, "_ask_claude", refuse_api)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "AS-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def site(settings, tmp_path):
    """会社の置き場 (一時の置き場) と、空の manual/。"""
    settings.BASE_DIR = tmp_path
    folder = tmp_path / "manual"
    folder.mkdir()
    return folder


def _mcp_functions():
    """Claude に渡すべき道具の名前 → mcp の関数 (読む道具と、書く道具の全部)。"""
    functions = {tool.__name__: tool for tool in mcp_tools.READ_TOOLS}
    functions.update({tool.__name__: tool for tool in mcp_tools.write_tools(None)})
    return functions


def test_tools_match_mcp_functions():
    read_names = {tool.__name__ for tool in mcp_tools.READ_TOOLS}
    names = [tool["name"] for tool in tools.TOOLS]
    assert len(names) == len(set(names))
    assert set(names) == read_names
    assert names == [tool.__name__ for tool in mcp_tools.READ_TOOLS]

    functions = _mcp_functions()
    for tool in tools.TOOLS:
        name = tool["name"]
        assert tool["description"].strip(), name
        schema = tool["input_schema"]
        assert schema["type"] == "object", name
        parameters = inspect.signature(functions[name]).parameters
        arguments = set(parameters) - {"confirm"}
        required = {key for key, value in parameters.items() if value.default is inspect.Parameter.empty} - {
            "confirm"
        }
        assert set(schema["properties"]) == arguments, name
        assert set(schema["required"]) == required, name
        assert "confirm" not in schema["properties"], name


def test_functions_are_the_mcp_read_tools():
    assert set(tools.FUNCTIONS) == {tool.__name__ for tool in mcp_tools.READ_TOOLS}
    for tool in mcp_tools.READ_TOOLS:
        assert tools.FUNCTIONS[tool.__name__] is tool
    assert tools.WRITE_TOOL_NAMES == ("create_order", "ship", "plan_step")
    assert not set(tools.WRITE_TOOL_NAMES) & set(tools.FUNCTIONS)


@pytest.mark.django_db
def test_run_tool_wraps_ok_true_and_collects_urls(shaft):
    found = tools.run_tool("search_items", {"query": "AS-100"})

    assert found["is_error"] is False
    content = json.loads(found["content"])
    assert "ok" not in content
    assert [item["part_no"] for item in content["items"]] == ["AS-100"]
    assert found["urls"] == [reverse("masters:item_detail", args=[shaft.pk])]

    screens = tools.run_tool("list_screens", None)
    assert screens["is_error"] is False
    assert "/orders/" in screens["urls"]
    assert not [url for url in screens["urls"] if "<" in url]
    assert len(screens["urls"]) == len(set(screens["urls"]))


@pytest.mark.django_db
def test_run_tool_wraps_ok_false_as_error():
    missing = tools.run_tool("get_item", {"part_no": "AS-999"})
    assert missing["is_error"] is True
    assert "AS-999" in missing["content"]
    assert missing["content"].endswith("。")
    assert missing["urls"] == []

    bad_status = tools.run_tool("list_orders", {"status": "x"})
    assert bad_status["is_error"] is True
    assert "状態 x は使えません" in bad_status["content"]

    bad_limit = tools.run_tool("search_items", {"query": "AS", "limit": "x"})
    assert bad_limit["is_error"] is True
    assert "件数" in bad_limit["content"]


@pytest.mark.django_db
def test_run_tool_shapes_get_manual_to_text(site):
    (site / "orders_order_list.html").write_text(
        "<section><h2>受注</h2><p>使い方の字</p></section>", encoding="utf-8"
    )
    (site / "masters_item_list.html").write_text("<section><p>" + "あ" * 5000 + "</p></section>", encoding="utf-8")

    page = tools.run_tool("get_manual", {"screen": "orders:order_list"})
    assert page["is_error"] is False
    content = json.loads(page["content"])
    assert "html" not in content
    assert content["text"] == "受注\n使い方の字"
    assert "<" not in page["content"]

    long = json.loads(tools.run_tool("get_manual", {"screen": "masters:item_list"})["content"])
    assert len(long["text"]) == tools.MANUAL_CHARS

    index = tools.run_tool("get_manual", {})
    assert index["is_error"] is False
    assert json.loads(index["content"])["items"]


@pytest.mark.django_db
def test_run_tool_errors(monkeypatch):
    unknown = tools.run_tool("drop_everything", {})
    assert unknown == {"content": "道具 drop_everything はありません。", "is_error": True, "urls": []}

    for name, arguments in [
        ("search_items", {"query": "x", "unknown": 1}),
        ("search_items", {}),
        ("list_screens", ["not", "a", "dict"]),
    ]:
        outcome = tools.run_tool(name, arguments)
        assert outcome == {"content": f"道具 {name} の引数が違います。", "is_error": True, "urls": []}

    def broken():
        raise LookupError("中身は文に入れない")

    monkeypatch.setitem(tools.FUNCTIONS, "list_screens", broken)
    outcome = tools.run_tool("list_screens", None)
    assert outcome == {"content": "list_screens の結果を読めませんでした。", "is_error": True, "urls": []}

    monkeypatch.setitem(tools.FUNCTIONS, "list_screens", lambda: ["辞書でない"])
    assert tools.run_tool("list_screens", {})["content"] == "list_screens の結果を読めませんでした。"

    proposed = tools.run_tool("ship", {"order_no": "SO-1", "line_no": 1, "qty": "3", "shipped_at": "2026-11-05"})
    assert proposed["is_error"] is True
    assert "提案のカードを通します" in proposed["content"]


def test_labels_cover_all_tools():
    names = [tool["name"] for tool in tools.TOOLS] + list(tools.WRITE_TOOL_NAMES)
    for name in names:
        assert name in tools.TOOL_LABELS, name
    assert tools.label_for("search_items") == "部品を探した"
    assert tools.label_for("nope") == "nope"
    for label in list(tools.TOOL_LABELS.values()) + list(tools.PROPOSAL_TITLES.values()):
        for word in WINDOW_WORDS:
            assert word not in label, (label, word)
