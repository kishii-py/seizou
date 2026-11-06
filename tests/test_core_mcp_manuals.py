"""業務マニュアルを読む道具 (seizou.core.mcp_tools の search_manuals・get_manual_body) と、
字にする関数 html_to_text と、書く道具の action の表 WRITE_ACTIONS を見る。

道具は MCP を通さずに直に呼ぶ。マニュアルは業務マニュアルの部品の services で作る。題と本文は作り物。
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.core import mcp_tools


def _json(result: dict) -> dict:
    """道具の返す物が JSON にできることを見て、読み直した物を返す。"""
    return json.loads(json.dumps(result, ensure_ascii=False))


def _has_japanese(text: str) -> bool:
    return any("぀" <= char <= "ヿ" or "一" <= char <= "鿿" for char in text)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


def _manual(user, title, body, folder=None):
    from seizou.manuals import services

    manual = services.create_manual(folder, title, "steps", user)
    services.update_manual(manual, {"body": body}, user)
    return manual


def test_search_manuals_finds_by_title_and_body(user):
    from seizou.manuals import services

    folder = services.create_folder(services.create_folder(None, "段取り", "blue", user), "旋盤", "gray", user)
    blade = _manual(user, "刃物の交換の手順", "刃物の番号を確かめる", folder)
    _manual(user, "朝の点検", "油の量を見る")

    found = _json(mcp_tools.search_manuals("刃物 番号"))
    assert found["ok"] is True
    assert (found["count"], found["total"]) == (1, 1)
    assert found["items"] == [
        {
            "id": blade.pk,
            "title": "刃物の交換の手順",
            "folder": "段取り / 旋盤",
            "format": "ステップ形式",
            "updated_at": timezone.localdate().isoformat(),
            "url": reverse("manuals:manual_detail", args=[blade.pk]),
        }
    ]

    everything = mcp_tools.search_manuals("")
    assert [row["title"] for row in everything["items"]] == ["刃物の交換の手順", "朝の点検"]
    assert everything["items"][1]["folder"] == "トップ"
    one = mcp_tools.search_manuals("", limit=1)
    assert (one["count"], one["total"]) == (1, 2)

    bad = mcp_tools.search_manuals("刃物", limit="abc")
    assert bad["ok"] is False
    assert _has_japanese(bad["error"])
    assert mcp_tools.search_manuals("無い言葉")["items"] == []


def test_get_manual_body_returns_text(user):
    manual = _manual(user, "刃物の交換の手順", "## 準備\n刃物の番号を確かめる\n:::注意\n手袋をする\n:::")

    body = _json(mcp_tools.get_manual_body(manual.pk))
    assert body["ok"] is True
    assert body["id"] == manual.pk
    assert body["title"] == "刃物の交換の手順"
    assert body["folder"] == "トップ"
    assert body["url"] == reverse("manuals:manual_detail", args=[manual.pk])
    assert "刃物の番号を確かめる" in body["text"]
    assert "手袋をする" in body["text"]
    assert "<" not in body["text"]
    assert body["truncated"] is False
    assert body["total_chars"] == len(body["text"])
    assert mcp_tools.get_manual_body(str(manual.pk))["ok"] is True

    missing = mcp_tools.get_manual_body(manual.pk + 100)
    assert missing["ok"] is False
    assert _has_japanese(missing["error"])
    assert "search_manuals" in missing["error"]
    unreadable = mcp_tools.get_manual_body("abc")
    assert unreadable["ok"] is False
    assert _has_japanese(unreadable["error"])

    long = _manual(user, "長い手順", "あ" * 9000)
    cut = mcp_tools.get_manual_body(long.pk)
    assert len(cut["text"]) == mcp_tools.MANUAL_BODY_CHARS
    assert cut["truncated"] is True
    assert cut["total_chars"] >= 9000


def test_manual_tools_without_manuals_part(monkeypatch, user):
    manual = _manual(user, "刃物の交換の手順", "刃物の番号を確かめる")
    monkeypatch.setattr(mcp_tools, "_installed", lambda app: False)

    for result in (mcp_tools.search_manuals("刃物"), mcp_tools.get_manual_body(manual.pk)):
        assert result["ok"] is False
        assert _has_japanese(result["error"])
        assert "manuals" in result["error"]


def test_html_to_text_keeps_block_breaks():
    html = "<section><h2>題</h2><p>a&amp;b  c</p><ul><li>1</li><li>2</li></ul></section>"

    assert mcp_tools.html_to_text(html, 100) == "題\na&b c\n1\n2"
    assert mcp_tools.html_to_text(html, 3) == "題\na"
    assert mcp_tools.html_to_text("<p>1行目<br>2行目<br />3行目</p>", 100) == "1行目\n2行目\n3行目"
    assert mcp_tools.html_to_text("<p>&lt;b&gt; は字</p>", 100) == "<b> は字"
    assert mcp_tools.html_to_text("", 100) == ""


def test_write_actions_match_write_tools():
    assert set(mcp_tools.WRITE_ACTIONS) == {tool.__name__ for tool in mcp_tools.write_tools(None)}
    assert mcp_tools.WRITE_ACTIONS == {
        "create_order": "orders.create_order",
        "ship": "shipping.ship",
        "plan_step": "workorders.plan_step",
    }


def test_manual_tools_are_registered():
    names = [tool.__name__ for tool in mcp_tools.READ_TOOLS]

    assert "search_manuals" in names
    assert "get_manual_body" in names
    for tool in (mcp_tools.search_manuals, mcp_tools.get_manual_body):
        assert (tool.__doc__ or "").strip()
