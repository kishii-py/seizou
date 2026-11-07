"""フィードバックの詳細の「この件の進み具合」の段の帯と、送る画面の左右の分けと、AI の下書きが右の欄に入る口
(受け渡しの seizou-フィードバックの段と送る画面-20261107/指示書.md。本人の決め 11-07 17:2x〜18:1x)。

本物の API と GitHub は呼ばない (autouse の fixture が差し替える。tests/test_feedback_chat.py と同じ形)。題と名前は作り物。
"""

import json
import re

import pytest
from django.contrib.auth import get_user_model

from seizou.assistant import services as assistant_services
from seizou.feedback import chat, services
from seizou.feedback.models import Feedback
from seizou.feedback.templatetags import feedback_tags
from tests.test_assistant_services import FAKE_KEY, refuse_api
from tests.test_feedback_chat import ASK_URL, DRAFT, HTMX, script, tool_reply

ISSUE_URL = "https://github.com/example-owner/example-repo/issues/7"


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
def manager(db):
    return get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")


@pytest.fixture
def plain(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, plain):
    client.force_login(plain)
    return client


def make(user, title="受注の一覧で並びが戻る"):
    data = {"kind": "bug", "screen": "orders:order_list", "title": title, "problem": "並び替えた後に戻ると、並びが元に戻る。"}
    return services.create_feedback(data, user)


def flags(steps, key):
    return [step[key] for step in steps]


def detail_html(client, row) -> str:
    response = client.get(f"/feedback/{row.pk}/")
    assert response.status_code == 200
    return response.content.decode()


def band_of(html: str) -> str:
    return re.search(r'<section id="feedback-steps".*?</section>', html, re.S).group(0)


# ---- 1. 段の帯の材料 (tag) ----


def test_steps_follow_status(plain, manager):
    row = make(plain)
    steps = feedback_tags.feedback_steps(row)
    assert flags(steps, "key") == ["written", "confirmed", "sent", "fixed"]
    assert flags(steps, "label") == ["書いた", "確認済み", "GitHub に送った", "直した"]
    assert flags(steps, "done") == [True, False, False, False]
    assert flags(steps, "tone") == ["primary", "", "", ""]
    assert steps[0]["when"] == row.created_at and steps[0]["note"] == "tester"
    assert flags(steps, "note")[1:] == ["", "", ""]

    services.mark_confirmed(row, manager)
    assert flags(feedback_tags.feedback_steps(row), "done") == [True, True, False, False]

    services.mark_sent(row, manager, sent_url=ISSUE_URL)
    steps = feedback_tags.feedback_steps(row)
    assert flags(steps, "done") == [True, True, True, False]
    assert flags(steps, "tone") == ["primary", "primary", "primary", ""]
    assert steps[2]["note"] == "Issue #7" and steps[2]["when"] is None
    assert steps[3]["note"] == "" and steps[3]["when"] is None

    services._save_fix_version(row, "0.1.1")
    assert feedback_tags.feedback_steps(row)[3]["note"] == "0.1.1 で直る"

    services.mark_fixed(row, "", manager)
    steps = feedback_tags.feedback_steps(row)
    assert flags(steps, "done") == [True, True, True, True]
    assert flags(steps, "tone") == ["primary", "primary", "primary", "success"]
    assert steps[3]["label"] == "直した" and steps[3]["when"] == row.fixed_at and steps[3]["note"] == ""


def test_steps_of_dismissed_and_of_fixed_from_draft(plain, manager):
    row = make(plain)
    services.mark_sent(row, manager, sent_url=ISSUE_URL)
    services._write_dismissed(row, "GitHub の Issue #7 (閉じた日 2026-11-06。理由 対応しない)")
    steps = feedback_tags.feedback_steps(row)
    assert flags(steps, "done") == [True, True, True, True]
    assert flags(steps, "tone") == ["primary", "primary", "primary", "error"]
    assert steps[3]["label"] == "見送った" and steps[3]["when"] == row.dismissed_at
    assert steps[2]["note"] == "Issue #7"

    # 世話役が確認せずに送った件と、下書きから直接「直した」にした件も、間の段は済んだ印 (指示書の「決めを待つ所」1 の勧め)。
    direct = make(plain, "下書きから直した題")
    services.mark_fixed(direct, "", manager)
    steps = feedback_tags.feedback_steps(direct)
    assert flags(steps, "done") == [True, True, True, True]
    assert steps[2]["note"] == ""  # Issue の URL が無いので番号は出さない

    unsent = make(plain, "送った先の無い題")
    services.mark_sent(unsent, manager)
    assert feedback_tags.feedback_steps(unsent)[2] == {
        "key": "sent", "label": "GitHub に送った", "done": True, "tone": "primary", "when": None, "note": ""
    }


# ---- 2. 詳細の帯 ----


def test_detail_shows_band_between_head_and_tabs(client_logged_in, plain, manager):
    row = make(plain)
    html = detail_html(client_logged_in, row)
    band = band_of(html)

    assert html.count('id="feedback-steps"') == 1
    assert html.index("data-detail-head") < html.index('id="feedback-steps"') < html.index('role="tablist"')
    assert 'class="mb-6 rounded-xl border border-base-300 bg-base-100 p-6 shadow-xs"' in band
    assert "この件の進み具合" in band and 'data-steps-done="draft"' in band
    assert '<ul class="steps steps-vertical mt-4 w-full text-xs sm:steps-horizontal"' in band
    assert re.findall(r'data-step="([a-z]+)"', band) == ["written", "confirmed", "sent", "fixed"]
    assert band.count('<li class="step step-primary" data-content="✓"') == 1
    assert band.count('<li class="step" data-step=') == 3
    assert band.count("step-success") == 0 and band.count("step-error") == 0
    assert "tester" in band and row.created_at.strftime("%Y-%m-%d") in band
    assert 'id="feedback-fixed"' not in html and 'id="feedback-dismissed"' not in html

    services.mark_sent(row, manager, sent_url=ISSUE_URL)
    services._save_fix_version(row, "0.1.1")
    band = band_of(detail_html(client_logged_in, row))
    assert band.count('data-content="✓"') == 3 and "Issue #7" in band and "0.1.1 で直る" in band


def test_detail_band_colors_fixed_green_and_dismissed_red(client_logged_in, plain, manager):
    fixed = make(plain, "直した題")
    services.mark_sent(fixed, manager, sent_url=ISSUE_URL)
    services.mark_fixed(fixed, "出荷の画面 0.1.1", manager)
    html = detail_html(client_logged_in, fixed)
    band = band_of(html)
    assert '<li class="step step-success" data-content="✓" data-step="fixed">' in band
    assert band.count('data-content="✓"') == 4 and "step-error" not in band
    assert 'data-steps-done="fixed"' in band
    assert 'id="feedback-fixed"' in html and "この件から直しました。" in html

    dismissed = make(plain, "見送った題")
    services.mark_sent(dismissed, manager, sent_url=ISSUE_URL)
    services._write_dismissed(dismissed, "GitHub の Issue #7 (閉じた日 2026-11-06。理由 対応しない)")
    html = detail_html(client_logged_in, dismissed)
    band = band_of(html)
    assert '<li class="step step-error" data-content="✕" data-step="fixed">' in band
    assert ">見送った</span>" in band and ">直した</span>" not in band
    assert band.count('data-content="✓"') == 3
    assert 'id="feedback-dismissed"' in html and "この件は、GitHub で対応しないとして閉じられました。" in html
    assert "一言" not in html


# ---- 3. 送る画面の左右 ----


def test_form_screen_splits_into_chat_and_form_with_key(with_key, client_logged_in):
    html = client_logged_in.get("/feedback/new/?screen=orders:order_list").content.decode()

    assert html.count('id="feedback-columns"') == 1
    columns = re.search(r'<div id="feedback-columns" class="([^"]*)"', html).group(1)
    assert columns == "grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:items-start"
    chat_card = re.search(r'<section id="feedback-chat" class="([^"]*)"', html).group(1)
    assert "lg:sticky lg:top-22" in chat_card and "bg-base-100" in chat_card
    assert html.count('id="feedback-form-column"') == 1
    assert html.index('id="feedback-chat"') < html.index('id="feedback-form-column"') < html.index('<form method="post" class="rounded-xl')
    assert "入力の形に下書きを入れます" in html and "下の入力の形" not in html
    assert html.count("btn-primary") == 1

    # 似た困りごとを探す link は画面の頭の白い帯の中 (タブの帯より上)。灰色の地に字の行を直接置かない。
    link = re.search(r'<a id="feedback-issues-link".*?</a>', html, re.S).group(0)
    assert 'class="btn btn-outline border-base-300"' in link and 'href="/feedback/?tab=issues#issues"' in link
    assert html.index("data-screen-head") < html.index('id="feedback-issues-link"') < html.index('id="feedback-tabs"')
    assert '<p id="feedback-issues-link"' not in html


def test_form_screen_without_key_has_form_only_and_link_in_band(without_key, client_logged_in):
    html = client_logged_in.get("/feedback/new/").content.decode()

    assert 'id="feedback-columns"' not in html and 'id="feedback-chat"' not in html
    assert 'id="feedback-form-column"' not in html and 'name="title"' in html
    assert html.count('id="feedback-issues-link"') == 1
    assert html.index("data-screen-head") < html.index('id="feedback-issues-link"') < html.index('id="feedback-tabs"')


# ---- 4. 下書きが右の欄に入る口 ----


def test_ask_fills_right_column_with_edit_form_on_draft(with_key, client_logged_in, plain, monkeypatch):
    script(monkeypatch, [tool_reply("draft_feedback", DRAFT)])
    response = client_logged_in.post(ASK_URL, {"message": "受注の一覧です", "screen": "orders:order_list"}, **HTMX)
    row = Feedback.objects.get()
    html = response.content.decode()

    assert response.status_code == 200 and "<html" not in html
    assert "HX-Redirect" not in response
    assert json.loads(response["HX-Trigger"]) == {"seizou:saved": {"message": chat.DRAFT_READY_MESSAGE, "level": "success"}}
    assert chat.CHAT_KEY not in client_logged_in.session

    # 先頭は吹き出し 2 つ (利用者の字と、下書きを入れた 1 文)。
    bubbles = html[: html.index('<div id="feedback-form-column"')]
    assert bubbles.count('<div class="chat chat-') == 2 and "受注の一覧です" in bubbles and chat.DRAFT_READY_MESSAGE in bubbles
    assert bubbles.index("chat-end") < bubbles.index("chat-start")

    # 続きは右の欄 (hx-swap-oob)。編集の形 = 送り先は編集の画面、欄の値は下書きの物、送られた字 (message) は欄の誤りにならない。
    column = html[html.index('<div id="feedback-form-column"') :]
    assert column.startswith('<div id="feedback-form-column" class="min-w-0" hx-swap-oob="true">')
    assert f'action="/feedback/{row.pk}/edit/"' in column
    assert 'value="受注の一覧で並びが戻る"' in column and "並び替えた後に詳細から戻ると、並びが元に戻る。" in column
    assert "csrfmiddlewaretoken" in column and "text-error" not in column
    assert column.count("btn-primary") == 1 and "キャンセル" in column
    assert 'name="message"' not in column

    # 右の形を保存すると、同じ件が 2 つにならず、詳細へ移る。
    saved = client_logged_in.post(
        f"/feedback/{row.pk}/edit/",
        {
            "kind": "bug",
            "screen": "orders:order_list",
            "title": "直した題",
            "problem": "並び替えた後に詳細から戻ると、並びが元に戻る。",
            "situation": "",
            "wish": "",
            "screens": "",
        },
    )
    assert saved.status_code == 302 and saved["Location"] == f"/feedback/{row.pk}/"
    assert Feedback.objects.count() == 1 and Feedback.objects.get().title == "直した題"


def test_draft_ready_message_mentions_form_not_screen():
    assert "入力の形" in chat.DRAFT_READY_MESSAGE
    assert "編集の画面" not in chat.DRAFT_READY_MESSAGE and "下の" not in chat.DRAFT_READY_MESSAGE
    assert "下の" not in chat.MESSAGE_NO_KEY


# ---- 5. 呼び方 (「一言」→「件」) ----


def test_tabs_and_notices_say_ken_not_hitokoto(client_logged_in, plain, manager):
    html = client_logged_in.get("/feedback/new/").content.decode()
    assert ">送った件<span" in html and "一言" not in html

    row = make(plain)
    services.mark_fixed(row, "", manager)
    assert services.fixed_notice([row]).startswith("あなたのフィードバックから直しました: ")
    assert "「送った件」のタブ" in services.fixed_notice([row])
    assert [tab["label"] for tab in feedback_tags.feedback_tabs({})] == ["送る", "送った件", "みんなの Issue"]
