"""会話の吹き出しの形 (N133。フィードバックの聞き取りと AI チャットの窓)。

吹き出しの横の丸の印 (自分は人、AI は AI の絵)・自分の吹き出しの主色・待つ間に AI の吹き出しの中に出る点の動きを、
core の断片 (core/_chat_bubble.html) と 2 つの画面の HTML の字で見る。本物の API は呼ばない
(autouse の fixture が assistant の _ask_claude を「呼ばれたら AssertionError」に差し替える)。
"""

import pytest
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.urls import reverse

from seizou.assistant import services as assistant_services
from seizou.core.templatetags.seizou_icons import PATHS, icon
from seizou.feedback import services
from tests.test_assistant_services import FAKE_KEY, refuse_api
from tests.test_feedback_chat import script, text_reply
from tests.test_feedback_views import make

HTMX = {"HTTP_HX_REQUEST": "true"}
BUBBLE = "core/_chat_bubble.html"
FEEDBACK_PENDING = 'id="feedback-pending" class="mt-2 hidden [&.htmx-request]:block" aria-hidden="true"'
ASSISTANT_PENDING = 'id="assistant-pending" class="hidden [&.htmx-request]:block" aria-hidden="true"'
DOTS = '<div class="chat-bubble break-words"><span class="loading loading-dots loading-sm" aria-hidden="true"></span></div>'


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


def between(html, start, end):
    """html の中の start の字から end の字の前まで (どちらも 1 回だけある字)。"""
    assert html.count(start) == 1 and html.count(end) == 1
    return html[html.index(start) : html.index(end)]


def test_bubble_sides_icons_and_sizes():
    mine = render_to_string(BUBBLE, {"who": "me", "text": "<b>太字</b>\n2 行目"})
    theirs = render_to_string(BUBBLE, {"who": "ai", "text": "作り物の答え", "small": True})

    assert PATHS["bot"] and PATHS["bot"] in icon("bot")
    assert '<div class="chat chat-end">' in mine and "chat-start" not in mine
    assert '<div class="chat-image avatar avatar-placeholder" aria-hidden="true">' in mine
    assert '<div class="rounded-full bg-primary text-primary-content w-10">' in mine
    assert PATHS["user"] in mine and PATHS["bot"] not in mine
    assert '<div class="chat-bubble chat-bubble-primary break-words">&lt;b&gt;太字&lt;/b&gt;<br>2 行目</div>' in mine
    assert '<div class="chat chat-start">' in theirs and "chat-end" not in theirs
    assert '<div class="rounded-full bg-neutral text-neutral-content w-8">' in theirs
    assert PATHS["bot"] in theirs and PATHS["user"] not in theirs
    assert '<div class="chat-bubble break-words">作り物の答え</div>' in theirs
    assert "loading" not in mine + theirs


def test_pending_bubble_has_dots_inside():
    html = render_to_string(BUBBLE, {"who": "ai", "pending": True, "text": "出さない字"})

    assert '<div class="chat chat-start">' in html and PATHS["bot"] in html
    assert DOTS in html
    assert "出さない字" not in html


@pytest.mark.django_db
def test_feedback_form_shows_pending_bubble_while_sending(with_key, client_logged_in):
    html = client_logged_in.get("/feedback/new/?screen=orders:order_list").content.decode()

    pending = between(html, 'id="feedback-pending"', 'id="feedback-chat-form"')
    assert pending.startswith(FEEDBACK_PENDING)
    assert DOTS in pending and PATHS["bot"] in pending and " w-10" in pending
    assert html.index('id="feedback-chat-messages"') < html.index('id="feedback-pending"')
    assert 'hx-indicator="#feedback-pending"' in html
    assert "feedback-sending" not in html


@pytest.mark.django_db
def test_feedback_ask_returns_bubbles_with_marks(with_key, client_logged_in, monkeypatch):
    script(monkeypatch, [text_reply("どの画面で起きましたか？")])

    html = client_logged_in.post(
        "/feedback/ask/", {"message": "出荷の数を直したい", "screen": "orders:order_list"}, **HTMX
    ).content.decode()

    assert html.index('<div class="chat chat-end">') < html.index('<div class="chat chat-start">')
    assert html.count('class="chat-image avatar avatar-placeholder"') == 2
    assert PATHS["user"] in html and PATHS["bot"] in html
    assert '<div class="chat-bubble chat-bubble-primary break-words">出荷の数を直したい</div>' in html
    assert '<div class="chat-bubble break-words">どの画面で起きましたか？</div>' in html
    assert "loading" not in html


@pytest.mark.django_db
def test_feedback_transcript_has_marks_and_no_dots(without_key, client_logged_in, plain):
    row = make(plain, transcript=[{"role": "user", "text": "受注の一覧で困る"}, {"role": "assistant", "text": "どの画面で起きましたか？"}])

    html = client_logged_in.get(f"/feedback/{row.pk}/").content.decode()

    assert html.count('class="chat-image avatar avatar-placeholder"') == 2
    assert PATHS["user"] in html and PATHS["bot"] in html
    assert "loading-dots" not in html and "feedback-pending" not in html


@pytest.mark.django_db
def test_assistant_window_shows_small_pending_bubble(with_key, client_logged_in, monkeypatch):
    html = client_logged_in.get(reverse("core:home")).content.decode()

    pending = between(html, 'id="assistant-pending"', 'id="assistant-form"')
    assert pending.startswith(ASSISTANT_PENDING)
    assert DOTS in pending and PATHS["bot"] in pending and " w-8" in pending
    assert html.index('id="assistant-messages"') < html.index('id="assistant-pending"')
    assert 'hx-indicator="#assistant-pending"' in html
    assert "assistant-sending" not in html

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert "assistant-pending" not in client_logged_in.get(reverse("core:home")).content.decode()


def test_assistant_answer_bubble_holds_tools_proposals_and_links():
    exchange = {
        "question": "作り物の質問",
        "answer": "作り物の答え",
        "tools": ["get_order"],
        "links": ["/orders/1/"],
        "proposals": [{"id": "p1", "title": "作り物の提案", "summary": ["1 行目"], "state": "pending"}],
    }

    html = render_to_string("assistant/_messages.html", {"exchanges": [exchange]})

    assert html.count('class="rounded-full ') == 2 and html.count(" w-8") == 2
    assert html.index('<div class="chat chat-end">') < html.index('<div class="chat chat-start">')
    answer = html[html.index('<div class="chat-bubble break-words">作り物の答え') :]
    assert answer.index('class="badge badge-ghost badge-sm"') < answer.index('id="assistant-proposal-p1"')
    assert answer.index('id="assistant-proposal-p1"') < answer.index('href="/orders/1/"')
    assert answer.index('href="/orders/1/"') < answer.index("</div>\n</div>")
