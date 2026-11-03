"""フィードバックの AI の聞き取り (seizou.feedback.chat)・口 (ask・reset)・入力の画面の AI の段・設定と案内と README のテスト。

本物の API は呼ばない。autouse の fixture が assistant の _ask_claude を「呼ばれたら AssertionError」に差し替え、
返事が要るテストだけが作り物の返事 (types.SimpleNamespace。usage を持つ物と持たない物) を返す関数に差し替える。
鍵はテストごとに setenv か delenv で明示する。session の単体の試験は辞書を使う。題・会社名・取引先は作り物。
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from seizou.assistant import services as assistant_services
from seizou.assistant import tools as assistant_tools
from seizou.core import conf
from seizou.core.models import Company
from seizou.feedback import chat, services
from seizou.feedback.models import Feedback
from tests.test_assistant_services import FAKE_KEY, refuse_api

ROOT = Path(__file__).resolve().parents[1]
HTMX = {"HTTP_HX_REQUEST": "true"}
ASK_URL = "/feedback/ask/"
RESET_URL = "/feedback/reset/"
USAGE = SimpleNamespace(input_tokens=10, output_tokens=5)
DRAFT = {
    "kind": "bug",
    "screen": "orders:order_list",
    "title": "受注の一覧で並びが戻る",
    "problem": "並び替えた後に詳細から戻ると、並びが元に戻る。",
    "situation": "朝の確認のとき。",
    "wish": "並びを覚えてほしい。",
    "screens": ["orders:order_list", "no:such"],
}


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(assistant_services, "_ask_claude", refuse_api)


@pytest.fixture(autouse=True)
def fresh_catalog():
    services.screen_catalog.cache_clear()
    yield
    services.screen_catalog.cache_clear()


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)


@pytest.fixture
def without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def plain(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, plain):
    client.force_login(plain)
    return client


def text_reply(text, stop_reason="end_turn", usage=USAGE):
    content = [SimpleNamespace(type="text", text=text)] if text else []
    reply = SimpleNamespace(stop_reason=stop_reason, content=content)
    if usage is not None:
        reply.usage = usage
    return reply


def tool_reply(name, arguments, tool_id="t1", usage=USAGE):
    reply = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="thinking", thinking="", signature="sig-for-tests"),
            SimpleNamespace(type="text", text="調べます。"),
            SimpleNamespace(type="tool_use", id=tool_id, name=name, input=arguments),
        ],
    )
    if usage is not None:
        reply.usage = usage
    return reply


def script(monkeypatch, replies):
    """_ask_claude を、決めた返事を順に返す関数に差し替える (最後の返事は何回でも返す)。呼ばれた引数の写しの一覧を返す。"""
    calls = []
    queue = list(replies)

    def _fake(model, system, messages, tools):
        calls.append({"model": model, "system": system, "messages": list(messages), "tools": tools})
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(assistant_services, "_ask_claude", _fake)
    return calls


def ask(session, message, user, screen="orders:order_list"):
    return chat.answer(session, message, user=user, screen=screen)


def test_model_name_default_and_setting(settings):
    settings.SEIZOU = {}
    assert conf.DEFAULTS["feedback_model"] == "claude-haiku-5-5"
    assert chat.model_name() == conf.DEFAULTS["feedback_model"]

    settings.SEIZOU = {"feedback_model": " claude-sonnet-5-5 "}
    assert chat.model_name() == "claude-sonnet-5-5"

    settings.SEIZOU = {"feedback_model": "  "}
    assert chat.model_name() == chat.DEFAULT_MODEL


def test_tools_are_three_readers_and_draft():
    assert [tool["name"] for tool in chat.TOOLS] == ["company_info", "list_screens", "get_manual", "draft_feedback"]
    names = {tool["name"] for tool in chat.TOOLS}
    assert not names & set(assistant_tools.WRITE_TOOL_NAMES)
    assert "confirm" not in chat.DRAFT_SCHEMA["input_schema"]["properties"]
    by_name = {tool["name"]: tool for tool in assistant_tools.TOOLS}
    for tool in chat.TOOLS[:3]:
        assert tool is by_name[tool["name"]]


def test_no_key_returns_sentence_without_api(without_key, plain):
    session = {}

    result = ask(session, "出荷の数を直したい", plain)

    assert result == {"text": chat.MESSAGE_NO_KEY, "turns": [], "feedback": None}
    assert session == {}
    assert Feedback.objects.count() == 0


def test_empty_and_long_message(with_key, plain):
    session = {}

    assert ask(session, "   ", plain)["text"] == chat.MESSAGE_EMPTY
    assert ask(session, "あ" * 2001, plain)["text"] == chat.MESSAGE_TOO_LONG
    assert ask(session, None, plain)["text"] == chat.MESSAGE_EMPTY
    assert session == {}


def test_question_is_stored_with_tokens(with_key, plain, monkeypatch):
    calls = script(monkeypatch, [text_reply("どの画面で起きましたか？")])
    session = {}

    result = ask(session, " 出荷の数を直したい ", plain)

    assert result["text"] == "どの画面で起きましたか？"
    assert result["feedback"] is None
    assert result["turns"] == [{"role": "user", "text": "出荷の数を直したい"}, {"role": "assistant", "text": "どの画面で起きましたか？"}]
    state = chat.chat(session)
    assert state["turns"] == result["turns"]
    assert state["questions"] == 1
    assert (state["input_tokens"], state["output_tokens"]) == (10, 5)
    assert state["screen"] == "orders:order_list"
    assert Feedback.objects.count() == 0
    assert calls[0]["model"] == "claude-haiku-5-5"
    assert calls[0]["tools"] is chat.TOOLS
    assert calls[0]["messages"] == [{"role": "user", "content": "出荷の数を直したい"}]

    # 2 回目: 前の往復は字だけの turn で渡す。画面を送らなければ session の画面を使う。
    ask(session, "出荷の新規登録です", plain, screen="")
    assert calls[1]["messages"] == [
        {"role": "user", "content": "出荷の数を直したい"},
        {"role": "assistant", "content": "どの画面で起きましたか？"},
        {"role": "user", "content": "出荷の新規登録です"},
    ]
    assert "受注 (orders:order_list)" in calls[1]["system"]
    assert chat.chat(session)["questions"] == 2


def test_reading_tool_round_trip(with_key, plain, monkeypatch, settings, tmp_path):
    settings.BASE_DIR = tmp_path
    (tmp_path / "manual").mkdir()
    (tmp_path / "manual" / "orders_order_list.html").write_text("<h2>受注</h2><p>受注の一覧の使い方の作り物。</p>", encoding="utf-8")
    calls = script(
        monkeypatch,
        [tool_reply("get_manual", {"screen": "orders:order_list"}), text_reply("一覧の上の「並び」で選べます。")],
    )
    session = {}

    result = ask(session, "受注の一覧の並び替えはどうやるの", plain)

    assert result["text"] == "一覧の上の「並び」で選べます。"
    assert len(calls) == 2
    last = calls[1]["messages"][-1]
    assert last["role"] == "user" and len(last["content"]) == 1
    tool_result = last["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "t1"
    body = json.loads(tool_result["content"])
    assert "text" in body and "html" not in body
    assert "受注の一覧の使い方の作り物。" in body["text"]
    assert calls[1]["messages"][-2]["role"] == "assistant"
    assert calls[0]["system"] == calls[1]["system"]
    assert calls[0]["tools"] is calls[1]["tools"] is chat.TOOLS
    state = chat.chat(session)
    assert (state["input_tokens"], state["output_tokens"]) == (20, 10)
    assert state["questions"] == 1
    assert Feedback.objects.count() == 0


def test_write_tool_is_refused_not_run(with_key, plain, monkeypatch):
    from seizou.shipping.models import Shipment

    calls = script(
        monkeypatch,
        [
            tool_reply("ship", {"order_no": "SO-1", "line_no": 1, "qty": "1", "shipped_at": "2026-11-03"}),
            text_reply("出荷はこの窓ではできません。", usage=None),
        ],
    )
    session = {}

    result = ask(session, "出荷して", plain)

    tool_result = calls[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "この窓では使えません" in tool_result["content"]
    assert result["text"] == "出荷はこの窓ではできません。"
    assert Shipment.objects.count() == 0
    # usage の無い返事は 0 として足す。
    assert (chat.chat(session)["input_tokens"], chat.chat(session)["output_tokens"]) == (10, 5)


def test_draft_tool_creates_row_and_clears_session(with_key, plain, monkeypatch):
    calls = script(monkeypatch, [text_reply("どの画面で起きましたか？"), tool_reply("draft_feedback", DRAFT)])
    session = {}

    ask(session, "並びが戻って困る", plain)
    result = ask(session, "受注の一覧です", plain)

    row = result["feedback"]
    assert isinstance(row, Feedback)
    assert result["text"] == "" and result["turns"] == []
    assert len(calls) == 2
    row.refresh_from_db()
    assert row.status == "draft"
    assert row.created_by == plain
    assert (row.kind, row.screen, row.title) == ("bug", "orders:order_list", "受注の一覧で並びが戻る")
    assert (row.situation, row.wish) == ("朝の確認のとき。", "並びを覚えてほしい。")
    assert row.transcript == [
        {"role": "user", "text": "並びが戻って困る"},
        {"role": "assistant", "text": "どの画面で起きましたか？"},
        {"role": "user", "text": "受注の一覧です"},
    ]
    assert (row.input_tokens, row.output_tokens) == (20, 10)
    assert row.screens == "orders:order_list"
    assert chat.CHAT_KEY not in session


def test_fourth_question_forces_draft(with_key, plain, monkeypatch):
    first = "受注の一覧で並び替えた後に詳細を開いて戻ると、並びが元に戻ってしまい、毎回並べ直すのが手間です。"
    # 4 往復 (AI の字の返事が 4 つ)。今の字は 4 つ目の問いへの答え。
    turns = [{"role": "user", "text": first}]
    for number in range(1, 5):
        if number > 1:
            turns.append({"role": "user", "text": f"答え {number - 1}"})
        turns.append({"role": "assistant", "text": f"問い {number}"})
    session = {chat.CHAT_KEY: {"turns": turns, "questions": 4, "input_tokens": 100, "output_tokens": 50, "screen": "orders:order_list"}}
    calls = script(monkeypatch, [text_reply("もう 1 つ聞かせてください。")])

    result = ask(session, "5 回目の答え", plain)

    content = calls[0]["messages"][-1]["content"]
    assert content.startswith("5 回目の答え") and content.endswith(chat.FORCE_DRAFT_NOTE)
    row = result["feedback"]
    assert isinstance(row, Feedback)
    assert row.title == first[:40]
    assert row.problem == first
    assert "AI: 問い 1" in row.situation and "答え: 答え 1" in row.situation
    assert row.kind == "common" and row.screen == "orders:order_list"
    assert row.transcript[-1] == {"role": "user", "text": "5 回目の答え"}
    assert (row.input_tokens, row.output_tokens) == (110, 55)
    assert chat.CHAT_KEY not in session


def test_draft_data_sanitizes(db):
    data = chat._draft_data(
        {"kind": "other", "title": "あ" * 101, "problem": "困る", "screen": "no:such", "screens": ["orders:order_list", "no:such", 3]},
        "orders:order_list",
    )
    assert data["kind"] == "common"
    assert data["title"] == "あ" * 100
    assert data["screen"] == "orders:order_list"
    assert data["screens"] == "orders:order_list"

    long_problem = "困りごとの字" * 10
    assert chat._draft_data({"kind": "bug", "title": "  ", "problem": long_problem}, "")["title"] == long_problem[:40]
    assert chat._draft_data({}, "", [{"role": "user", "text": "最初の字"}])["problem"] == "最初の字"
    assert chat._draft_data("辞書でない", "") == {
        "kind": "common",
        "screen": "",
        "title": "",
        "problem": "",
        "situation": "",
        "wish": "",
        "screens": "",
    }


def test_system_mentions_company_screen_and_rules(db):
    Company.objects.create(name="作り物の製作所")

    system = chat.build_system("orders:order_list")

    assert "作り物の製作所" in system
    assert timezone.localdate().isoformat() in system
    assert "利用者が開いていた画面: 受注 (orders:order_list)" in system
    assert "draft_feedback" in system
    assert "4" in system
    assert "コードは読めません" in system
    assert "利用者が開いていた画面" not in chat.build_system("no:such")


def test_api_error_is_returned_not_stored(with_key, plain, monkeypatch):
    script(monkeypatch, [ValueError(assistant_services.MESSAGE_BUSY)])
    before = {"turns": [{"role": "user", "text": "a"}, {"role": "assistant", "text": "b"}], "questions": 1, "input_tokens": 3, "output_tokens": 4, "screen": ""}
    session = {chat.CHAT_KEY: dict(before)}

    result = ask(session, "出荷の数を直したい", plain)

    assert result == {"text": assistant_services.MESSAGE_BUSY, "turns": [], "feedback": None}
    assert session == {chat.CHAT_KEY: before}
    assert Feedback.objects.count() == 0


def test_form_page_shows_chat_only_with_key(with_key, client_logged_in, plain, monkeypatch):
    html = client_logged_in.get("/feedback/new/?screen=orders:order_list").content.decode()
    for word in ('id="feedback-chat"', 'hx-post="/feedback/ask/"', 'name="message"', 'hx-post="/feedback/reset/"', 'name="title"'):
        assert word in html, word
    assert '<input type="hidden" name="screen" value="orders:order_list">' in html
    assert html.count("btn-primary") == 1
    assert html.index('id="feedback-chat"') < html.index('name="title"')

    row = services.create_feedback({"kind": "bug", "title": "題", "problem": "困る"}, plain)
    assert "feedback-chat" not in client_logged_in.get(f"/feedback/{row.pk}/edit/").content.decode()

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    html = client_logged_in.get("/feedback/new/").content.decode()
    assert "feedback-chat" not in html
    assert 'name="title"' in html


def test_ask_returns_bubbles_and_redirects_on_draft(with_key, client_logged_in, monkeypatch):
    script(monkeypatch, [text_reply("どの画面で起きましたか？")])

    response = client_logged_in.post(ASK_URL, {"message": "出荷の数を直したい", "screen": "orders:order_list"}, **HTMX)
    html = response.content.decode()
    assert response.status_code == 200
    assert "chat-bubble" in html and "どの画面で起きましたか？" in html and "出荷の数を直したい" in html
    assert "<html" not in html

    script(monkeypatch, [tool_reply("draft_feedback", DRAFT)])
    response = client_logged_in.post(ASK_URL, {"message": "受注の一覧です", "screen": "orders:order_list"}, **HTMX)
    row = Feedback.objects.get()
    assert response.status_code == 200
    assert response.content == b""
    assert response["HX-Redirect"] == f"/feedback/{row.pk}/edit/"
    assert chat.CHAT_KEY not in client_logged_in.session

    html = client_logged_in.get(response["HX-Redirect"]).content.decode()
    assert chat.DRAFT_READY_MESSAGE in html
    assert 'value="受注の一覧で並びが戻る"' in html


def test_ask_guards(with_key, client, plain):
    response = client.post(ASK_URL, {"message": "困る"}, **HTMX)
    assert response.status_code == 302 and "/login/" in response["Location"]

    client.force_login(plain)
    assert client.get(ASK_URL).status_code == 405
    assert client.post(ASK_URL, {"message": "困る"}).status_code == 400
    assert client.post(RESET_URL).status_code == 400
    assert chat.CHAT_KEY not in client.session
    assert Feedback.objects.count() == 0


def test_reset_clears_session(with_key, client_logged_in, monkeypatch):
    script(monkeypatch, [text_reply("どの画面で起きましたか？")])
    client_logged_in.post(ASK_URL, {"message": "出荷の数を直したい"}, **HTMX)
    assert chat.CHAT_KEY in client_logged_in.session

    response = client_logged_in.post(RESET_URL, **HTMX)

    assert response.status_code == 200 and response.content == b""
    assert chat.CHAT_KEY not in client_logged_in.session


def test_list_shows_tokens_of_chat_row(with_key, client_logged_in, plain, monkeypatch):
    script(monkeypatch, [tool_reply("draft_feedback", DRAFT, usage=SimpleNamespace(input_tokens=1234, output_tokens=567))])
    chat.answer(client_logged_in.session, "並びが戻って困る", user=plain, screen="orders:order_list")
    row = Feedback.objects.get()
    assert (row.input_tokens, row.output_tokens) == (1234, 567)

    html = client_logged_in.get("/feedback/").content.decode()

    assert "受注の一覧で並びが戻る" in html
    assert "1,234" in html and "567" in html


def test_assistant_prompt_mentions_feedback():
    assert "フィードバックで伝えますか" in assistant_services.PROMPT_RULES
    assert "/feedback/new/" in assistant_services.PROMPT_RULES


def _section(text, heading):
    """見出しの行から次の同じ深さか浅い見出しの前までの行 (tests/test_docs_readme.py の読み方)。"""
    lines = text.splitlines()
    start = next((number + 1 for number, line in enumerate(lines) if line.strip() == heading), None)
    if start is None:
        return []
    body = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        body.append(line)
    return body


def _table_rows(lines):
    """表の行 (| で始まる行) を、前後の空白と ` を外したセルの並びにする (tests/test_docs_readme.py の読み方)。"""
    return [[cell.strip().strip("`") for cell in line.strip().strip("|").split("|")] for line in lines if line.startswith("|")]


def test_readme_has_setting_row_and_section():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    rows = _table_rows(_section(text, "## 選べる設定"))
    header = rows[0]
    table = {row[header.index("設定の鍵")]: row[header.index("既定")] for row in rows[2:]}
    assert table["feedback_model"] == conf.DEFAULTS["feedback_model"]

    flow = "\n".join(_section(text, "## 業務の流れ"))
    assert re.search(r"^### フィードバック$", flow, re.M)
    assert "feedback_model" in flow
