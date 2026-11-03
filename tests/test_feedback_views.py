"""フィードバックの画面 (一覧・新規登録・詳細・編集)・口 (確認済み・送った)・左のメニューの下の入口のテスト。

画面の HTML は Django の test client で開いて見る。本物の API は呼ばない (autouse の fixture が assistant の
_ask_claude を「呼ばれたら AssertionError」に差し替える)。画面の目録の控えは試験の前後で捨てる。題と名前は作り物。
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.assistant import services as assistant_services
from seizou.core import access
from seizou.feedback import services
from seizou.feedback.models import Feedback
from tests.test_assistant_services import refuse_api


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(assistant_services, "_ask_claude", refuse_api)


@pytest.fixture(autouse=True)
def fresh_catalog():
    services.screen_catalog.cache_clear()
    yield
    services.screen_catalog.cache_clear()


@pytest.fixture
def manager(db):
    return get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")


@pytest.fixture
def plain(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def other(db):
    return get_user_model().objects.create_user("other", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, plain):
    client.force_login(plain)
    return client


@pytest.fixture
def manager_client(client, manager):
    client.force_login(manager)
    return client


def html_of(response) -> str:
    return response.content.decode()


def nav_of(html: str) -> str:
    return re.search(r'<nav class="flex-1 overflow-y-auto p-4" aria-label="メニュー">(.*?)</nav>', html, re.S).group(1)


def make(user, title="受注の一覧で並びが戻る", **extra):
    data = {"kind": "bug", "screen": "orders:order_list", "title": title, "problem": "並び替えた後に戻ると、並びが元に戻る。"}
    return services.create_feedback({**data, **extra.pop("data", {})}, user, **extra)


def form_data(**change):
    data = {
        "kind": "bug",
        "screen": "orders:order_list",
        "title": "受注の一覧で並びが戻る",
        "problem": "並び替えた後に戻ると、並びが元に戻る。",
        "situation": "",
        "wish": "",
        "screens": "",
    }
    return {**data, **change}


def test_sidebar_has_entry_with_current_screen(client_logged_in, client):
    html = html_of(client_logged_in.get("/orders/"))

    assert 'id="feedback-entry"' in html
    assert "フィードバック" in html
    assert 'href="/feedback/new/?screen=orders%3Aorder_list"' in html
    assert "feedback-entry" not in nav_of(html)
    assert html.index("</nav>") < html.index('id="feedback-entry"')

    client.logout()
    assert "feedback-entry" not in html_of(client.get(reverse("core:login")))


def test_create_screen_initial_screen_and_save(client_logged_in, plain):
    response = client_logged_in.get("/feedback/new/?screen=orders:order_list")
    html = html_of(response)

    assert response.status_code == 200
    assert re.search(r'<select name="screen"[^>]*>.*?<option value="orders:order_list" selected>', html, re.S)
    assert html.count("btn-primary") == 1

    response = client_logged_in.post("/feedback/new/", form_data())
    row = Feedback.objects.get()
    assert response.status_code == 302
    assert response["Location"] == f"/feedback/{row.pk}/"
    assert row.created_by == plain
    assert row.status == "draft"
    assert "保存しました。" in html_of(client_logged_in.get(response["Location"]))


def test_create_refuses_bad_screens_with_red_notice(client_logged_in):
    response = client_logged_in.post("/feedback/new/", form_data(screens="orders:order_list no:such"))
    html = html_of(response)

    assert response.status_code == 200
    assert "alert-error" in html
    assert "画面の目録に無い名前" in html
    assert Feedback.objects.count() == 0


def test_list_shows_own_only_and_manager_sees_all(client, plain, other, manager):
    make(plain, "自分の題", input_tokens=98765, output_tokens=4321)
    make(other, "ほかの人の題")

    client.force_login(plain)
    html = html_of(client.get("/feedback/"))
    assert "自分の題" in html and "ほかの人の題" not in html
    assert "入力 token" in html and "出力 token" in html
    assert "98,765" in html and "4,321" in html
    assert "受注 (orders:order_list)" in html

    client.force_login(manager)
    html = html_of(client.get("/feedback/"))
    assert "自分の題" in html and "ほかの人の題" in html


def test_detail_for_other_is_404_and_shows_issue_text(client, plain, other, manager):
    mine = make(plain, "自分の題")
    theirs = make(other, "ほかの人の題")

    client.force_login(plain)
    assert client.get(f"/feedback/{theirs.pk}/").status_code == 404
    response = client.get(f"/feedback/{mine.pk}/")
    html = html_of(response)
    assert response.status_code == 200
    issue = re.search(r'<textarea id="feedback-issue"[^>]*>(.*?)</textarea>', html, re.S).group(1)
    assert "# 自分の題" in issue
    assert "## 困りごと" in issue
    assert "確認済みにする" not in html
    assert "送ったにする" not in html
    assert 'id="feedback-copy"' in html

    client.force_login(manager)
    html = html_of(client.get(f"/feedback/{theirs.pk}/"))
    assert "確認済みにする" in html and "送ったにする" in html
    assert 'action="/feedback/%d/confirm/"' % theirs.pk in html


def test_confirm_and_sent_endpoints(client, plain, manager):
    row = make(plain)
    detail = f"/feedback/{row.pk}/"

    client.force_login(plain)
    response = client.post(f"/feedback/{row.pk}/confirm/")
    assert response.status_code == 302 and response["Location"] == detail
    html = html_of(client.get(detail))
    assert "bg-error" in html and "アカウント管理の担当者だけ" in html
    row.refresh_from_db()
    assert row.status == "draft"
    assert client.get(f"/feedback/{row.pk}/confirm/").status_code == 405

    client.force_login(manager)
    response = client.post(f"/feedback/{row.pk}/confirm/")
    assert response.status_code == 302 and response["Location"] == detail
    row.refresh_from_db()
    assert row.status == "confirmed"
    assert "確認済みにしました。" in html_of(client.get(detail))

    response = client.post(f"/feedback/{row.pk}/sent/", {"sent_url": "https://example.com/issues/1"})
    assert response.status_code == 302 and response["Location"] == detail
    row.refresh_from_db()
    assert (row.status, row.sent_url) == ("sent", "https://example.com/issues/1")
    html = html_of(client.get(detail))
    assert "送った状態にしました。" in html
    assert "送ったにする" not in html and "確認済みにする" not in html
    assert client.get(f"/feedback/{row.pk}/sent/").status_code == 405

    client.logout()
    response = client.post(f"/feedback/{row.pk}/sent/")
    assert response.status_code == 302 and reverse("core:login") in response["Location"]


def test_update_screen_saves_and_refuses_others(client, plain, other):
    row = make(plain, "元の題")
    edit = f"/feedback/{row.pk}/edit/"

    client.force_login(plain)
    detail_html = html_of(client.get(f"/feedback/{row.pk}/"))
    assert f'href="{edit}"' in detail_html
    response = client.get(edit)
    assert response.status_code == 200
    assert 'value="元の題"' in html_of(response)

    response = client.post(edit, form_data(title="直した題"))
    assert response.status_code == 302 and response["Location"] == f"/feedback/{row.pk}/"
    row.refresh_from_db()
    assert row.title == "直した題"

    client.force_login(other)
    response = client.post(edit, form_data(title="ほかの人の題"))
    html = html_of(response)
    assert response.status_code == 200
    assert "alert-error" in html and "書いた人" in html
    row.refresh_from_db()
    assert row.title == "直した題"


def test_transcript_is_rendered_not_dumped(client_logged_in, plain):
    row = make(
        plain,
        transcript=[{"role": "user", "text": "受注の一覧で困る"}, {"role": "assistant", "text": "どの画面で起きましたか？"}],
        input_tokens=11,
        output_tokens=22,
    )

    html = html_of(client_logged_in.get(f"/feedback/{row.pk}/"))

    assert "会話の記録" in html
    assert html.count('class="chat-bubble') == 2
    assert "受注の一覧で困る" in html and "どの画面で起きましたか？" in html
    assert "入力 11 / 出力 22" in html
    assert "&quot;role&quot;" not in html and '"role"' not in html


def test_actions_are_classified_and_app_is_open():
    assert "feedback.create_feedback" in access.FREE_ACTIONS
    assert "feedback.update_feedback" in access.FREE_ACTIONS
    assert "feedback" in access.ALWAYS_OPEN_APPS


def test_urls():
    assert reverse("feedback:feedback_list") == "/feedback/"
    assert reverse("feedback:feedback_create") == "/feedback/new/"
    assert reverse("feedback:feedback_detail", args=[1]) == "/feedback/1/"
    assert reverse("feedback:feedback_update", args=[1]) == "/feedback/1/edit/"
    assert reverse("feedback:confirm", args=[1]) == "/feedback/1/confirm/"
    assert reverse("feedback:sent", args=[1]) == "/feedback/1/sent/"
