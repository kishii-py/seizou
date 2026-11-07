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
    return re.search(r'<nav class="flex-1 overflow-y-auto px-4 py-2" aria-label="メニュー">(.*?)</nav>', html, re.S).group(1)


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
    assert 'data-notice="stop"' in html
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
    assert "## 実際の動き" in issue
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
    assert f'href="{edit}?next=/feedback/{row.pk}/"' in detail_html
    response = client.get(edit)
    assert response.status_code == 200
    assert 'value="元の題"' in html_of(response)

    response = client.post(edit, form_data(title="直した題"))
    assert response.status_code == 302 and response["Location"] == f"/feedback/{row.pk}/"
    row.refresh_from_db()
    assert row.title == "直した題"

    client.force_login(other)
    response = client.post(edit, form_data(title="ほかの人の題"))
    assert response.status_code == 404
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


def test_delete_button_and_endpoint(client, plain, other, manager):
    mine = make(plain, "消す下書き")
    endpoint = f"/feedback/{mine.pk}/delete/"

    client.force_login(plain)
    html = html_of(client.get(f"/feedback/{mine.pk}/"))
    assert 'id="feedback-delete"' in html
    assert f'action="{endpoint}"' in html

    client.force_login(other)
    assert client.post(endpoint).status_code == 404
    assert Feedback.objects.filter(pk=mine.pk).exists()

    client.force_login(plain)
    assert client.get(endpoint).status_code == 405
    response = client.post(endpoint)
    assert response.status_code == 302 and response["Location"] == "/feedback/"
    assert not Feedback.objects.filter(pk=mine.pk).exists()
    assert "下書きを削除しました。" in html_of(client.get("/feedback/"))

    theirs = make(other, "ほかの人の下書き")
    client.force_login(manager)
    assert 'id="feedback-delete"' in html_of(client.get(f"/feedback/{theirs.pk}/"))
    response = client.post(f"/feedback/{theirs.pk}/delete/")
    assert response.status_code == 302 and response["Location"] == "/feedback/"
    assert not Feedback.objects.filter(pk=theirs.pk).exists()
    client.get("/feedback/")

    kept = make(plain, "確認済みの物")
    services.mark_confirmed(kept, manager)
    detail = f"/feedback/{kept.pk}/"
    client.force_login(plain)
    assert 'id="feedback-delete"' not in html_of(client.get(detail))
    client.force_login(manager)
    assert 'id="feedback-delete"' not in html_of(client.get(detail))
    response = client.post(f"/feedback/{kept.pk}/delete/")
    assert response.status_code == 302 and response["Location"] == detail
    html = html_of(client.get(detail))
    assert "bg-error" in html and "下書きだけ" in html
    assert Feedback.objects.filter(pk=kept.pk).exists()

    client.logout()
    response = client.post(f"/feedback/{kept.pk}/delete/")
    assert response.status_code == 302 and reverse("core:login") in response["Location"]
    assert Feedback.objects.filter(pk=kept.pk).exists()


def test_list_filters_by_status_and_kind(client, plain, other, manager):
    make(plain, "下書きの不具合", data={"kind": "bug"})
    confirmed = make(plain, "確認済みの要望", data={"kind": "common"})
    services.mark_confirmed(confirmed, manager)
    make(other, "ほかの人の下書き")

    client.force_login(plain)
    html = html_of(client.get("/feedback/?status=draft"))
    assert "下書きの不具合" in html
    assert "確認済みの要望" not in html and "ほかの人の下書き" not in html
    assert 'id="feedback-filter"' in html
    assert 'name="status"' in html and 'name="kind"' in html
    assert '<option value="draft" selected>' in html

    html = html_of(client.get("/feedback/?kind=common"))
    assert "確認済みの要望" in html and "下書きの不具合" not in html

    html = html_of(client.get("/feedback/?status=draft&kind=common"))
    assert "確認済みの要望" not in html and "下書きの不具合" not in html

    html = html_of(client.get("/feedback/?status=nonsense&kind="))
    assert "確認済みの要望" in html and "下書きの不具合" in html

    html = html_of(client.get("/feedback/?status=confirmed", HTTP_HX_REQUEST="true"))
    assert "<html" not in html
    assert "確認済みの要望" in html and "下書きの不具合" not in html

    client.force_login(manager)
    html = html_of(client.get("/feedback/?status=draft"))
    assert "下書きの不具合" in html and "ほかの人の下書き" in html
    assert "確認済みの要望" not in html


def test_update_screen_is_limited_to_visible_rows(client, plain, other, manager):
    from seizou.core.views import BaseFormView

    row = make(plain, "元の題")
    edit = f"/feedback/{row.pk}/edit/"

    client.force_login(other)
    assert client.get(edit).status_code == 404
    assert client.post(edit, form_data(title="ほかの人の題")).status_code == 404
    row.refresh_from_db()
    assert row.title == "元の題"

    client.force_login(manager)
    response = client.get(edit)
    assert response.status_code == 200
    assert 'value="元の題"' in html_of(response)

    services.mark_confirmed(row, manager)
    client.force_login(plain)
    assert client.get(edit).status_code == 200
    response = client.post(edit, form_data(title="確認済みの後の題"))
    html = html_of(response)
    assert response.status_code == 200
    assert 'data-notice="stop"' in html and "書いた人" in html
    row.refresh_from_db()
    assert row.title == "元の題"

    client.logout()
    response = client.get(edit)
    assert response.status_code == 302 and reverse("core:login") in response["Location"]

    assert not hasattr(BaseFormView, "get_queryset")


# ---- 見送ったと直る版 (依頼書 N322。決め 11-07 09:0x〜09:4x) ----

ISSUE_URL = "https://github.com/example-owner/example-repo/issues/7"
DISMISSED_SENTENCE = "この一言は、GitHub で対応しないとして閉じられました。理由は Issue に書かれています。"


@pytest.fixture
def installed(monkeypatch):
    """会社が入れている版を差し替える (既定 0.1.0)。返す関数で版を変える。"""
    from seizou.core import manual

    current = {"value": "0.1.0"}
    monkeypatch.setattr(manual, "package_version", lambda: current["value"])

    def set_version(value):
        current["value"] = value

    return set_version


def test_detail_shows_dismissed_section(client, plain, manager):
    row = make(plain, "見送った題")
    services.mark_sent(row, manager, sent_url=ISSUE_URL)
    services._write_dismissed(row, "GitHub の Issue #7 (閉じた日 2026-11-06。理由 対応しない)")
    other = make(plain, "下書きの題")

    client.force_login(plain)
    html = html_of(client.get(f"/feedback/{row.pk}/"))
    assert 'id="feedback-dismissed"' in html and "見送りました" in html
    assert DISMISSED_SENTENCE in html
    assert "GitHub の Issue #7 (閉じた日 2026-11-06。理由 対応しない)" in html
    assert f'href="{ISSUE_URL}" target="_blank" rel="noopener"' in html
    assert 'id="feedback-fixed"' not in html
    assert 'id="feedback-dismissed"' not in html_of(client.get(f"/feedback/{other.pk}/"))

    client.force_login(manager)
    html = html_of(client.get(f"/feedback/{row.pk}/"))
    assert 'id="feedback-dismissed"' in html
    assert "今の状態: 見送った" in html
    assert "直したと記録する" not in html and f'action="/feedback/{row.pk}/fixed/"' not in html
    assert "送ったにする" not in html and "確認済みにする" not in html


def test_fix_version_line_only_for_manager(client, plain, manager, installed):
    row = make(plain, "版待ちの題")
    services.mark_sent(row, manager, sent_url=ISSUE_URL)
    Feedback.objects.filter(pk=row.pk).update(fix_version="0.1.1")
    detail = f"/feedback/{row.pk}/"

    client.force_login(manager)
    html = html_of(client.get(detail))
    assert 'id="feedback-fix-version"' in html
    assert "直る版 0.1.1 が出ています。この会社は 0.1.0 です。版を入れた後の取り込みで「直した」になります。" in html

    installed("")
    assert "直る版 0.1.1 が出ています。この会社の版が分かりません。" in html_of(client.get(detail))

    client.force_login(plain)
    html = html_of(client.get(detail))
    assert 'id="feedback-fix-version"' not in html and "直る版 0.1.1 が出ています" not in html

    plain_row = make(plain, "版待ちでない題")
    services.mark_sent(plain_row, manager, sent_url=ISSUE_URL)
    client.force_login(manager)
    assert 'id="feedback-fix-version"' not in html_of(client.get(f"/feedback/{plain_row.pk}/"))


def test_list_has_fix_version_column_and_dismissed_filter(client, plain, manager):
    waiting = make(plain, "版待ちの題")
    services.mark_sent(waiting, manager, sent_url=ISSUE_URL)
    Feedback.objects.filter(pk=waiting.pk).update(fix_version="0.1.1")
    dismissed = make(plain, "見送った題")
    services.mark_sent(dismissed, manager, sent_url=ISSUE_URL)
    services._write_dismissed(dismissed, "GitHub の Issue #7")

    client.force_login(plain)
    html = html_of(client.get("/feedback/"))
    assert re.search(r'href="\?&sort=fix_version"[^>]*>直る版<', html)
    assert html.index(">直る版<") > html.index('href="?&sort=status"')
    assert "0.1.1" in html
    assert '<option value="dismissed"' in html and "見送った" in html

    html = html_of(client.get("/feedback/?status=dismissed"))
    assert "見送った題" in html and "版待ちの題" not in html
    assert '<option value="dismissed" selected>' in html


# ---- 詳細の「返事」の節 (依頼書 N324。決め 11-07 09:1x〜09:4x) ----


@pytest.fixture
def reply_env(settings, tmp_path, monkeypatch):
    """GitHub の置き場 (.env) を試験ごとの一時の物にする (つないでいない会社から始める)。返す関数でつなぐ。"""
    from seizou.feedback import github

    settings.SEIZOU_ENV_FILE = tmp_path / ".env"
    for key in (github.TOKEN_ENV, github.REPO_ENV, github.LOGIN_ENV):
        monkeypatch.delenv(key, raising=False)

    def connected():
        github.write_env_value(github.TOKEN_ENV, "ghp_test0000fake")
        github.write_env_value(github.LOGIN_ENV, "example-feedback-bot")
        monkeypatch.setenv(github.TOKEN_ENV, "ghp_test0000fake")
        monkeypatch.setenv(github.LOGIN_ENV, "example-feedback-bot")

    return connected


def github_replies(row, monkeypatch, *items):
    """GitHub の返事 (id, login, 本文) を取り込みと同じ関数で表に入れる (本物の GitHub は呼ばない)。"""
    from seizou.feedback import github

    placed = [
        {"id": number, "login": login, "body": body, "created_at": "2026-11-06T04:00:00Z", "html_url": f"{ISSUE_URL}#issuecomment-{number}"}
        for number, login, body in items
    ]
    monkeypatch.setattr(github, "list_comments", lambda repo, number, token="": placed)
    return services.import_replies(row, "example-owner/example-repo", 7)


def replies_section(html: str) -> str:
    return re.search(r'<section id="feedback-replies".*?</section>', html, re.S).group(0)


def test_replies_section_only_for_sent_fixed_dismissed(client, plain, manager, reply_env):
    draft = make(plain, "下書きの題")
    confirmed = make(plain, "確認済みの題")
    services.mark_confirmed(confirmed, manager)
    sent = make(plain, "送った題")
    services.mark_sent(sent, manager, sent_url=ISSUE_URL)
    fixed = make(plain, "直した題")
    services.mark_fixed(fixed, "", manager)
    dismissed = make(plain, "見送った題")
    services.mark_sent(dismissed, manager, sent_url=ISSUE_URL)
    services._write_dismissed(dismissed, "GitHub の Issue #7")

    client.force_login(plain)
    for row in (draft, confirmed):
        assert 'id="feedback-replies"' not in html_of(client.get(f"/feedback/{row.pk}/"))
    for row in (sent, fixed, dismissed):
        html = html_of(client.get(f"/feedback/{row.pk}/"))
        section = replies_section(html)
        assert '<h2 id="feedback-replies-title"' in section and "返事" in section
        assert "まだ返事はありません。" in section
        assert f'action="/feedback/{row.pk}/replies/"' in section and "返事を書く" in section
        assert "作者からの返事に答えます。送るのは世話役です。" in section
        # 返事の節は「Issue の文」の節の次
        assert html.index('id="feedback-issue-title"') < html.index('id="feedback-replies"')


def test_replies_show_github_and_drafts(client, plain, manager, reply_env, monkeypatch):
    reply_env()
    row = make(plain, "返事の来た題")
    services.mark_sent(row, manager, sent_url=ISSUE_URL)
    github_replies(row, monkeypatch, (101, "example-author", "どの画面で起きましたか"), (102, "example-feedback-bot", "会社から"))
    services.add_reply(row, "受注の一覧の画面です。", plain)

    client.force_login(plain)
    section = replies_section(html_of(client.get(f"/feedback/{row.pk}/")))
    assert '<span class="badge badge-sm tabular-nums">3</span>' in section
    assert "example-author" in section and "どの画面で起きましたか" in section
    assert '<span class="badge badge-sm">この会社</span>' in section
    assert "受注の一覧の画面です。" in section and '<span class="badge badge-sm">下書き</span>' in section
    assert "世話役の確認を待っています。" in section
    assert "/send/" not in section and "/sent/" not in section
    assert section.index("どの画面で起きましたか") < section.index("受注の一覧の画面です。")
    assert "html_url" not in section and "issuecomment" not in section


def test_manager_form_starts_with_masked_text(client, plain, manager, reply_env):
    from seizou.masters import services as masters_services

    masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "short_name": "作り物", "role": "customer"}, plain)
    row = make(plain, "伏せ字の題")
    services.mark_sent(row, manager, sent_url=ISSUE_URL)
    reply = services.add_reply(row, "作り物の得意先の 1,200 円の件です。", plain)

    client.force_login(manager)
    section = replies_section(html_of(client.get(f"/feedback/{row.pk}/")))
    area = re.search(rf'<textarea id="feedback-reply-body-{reply.pk}" name="body" rows="4"[^>]*>(.*?)</textarea>', section, re.S).group(1)
    assert area == "〇〇の 〇〇の件です。"
    assert "伏せ字 (〇〇) を確かめ、要るところを直してから送ってください。" in section
    # つないでいない会社: 写す と 送ったにする
    assert f'action="/feedback/replies/{reply.pk}/sent/"' in section
    assert f'data-reply-copy="feedback-reply-body-{reply.pk}"' in section and "送ったにする" in section
    assert "GitHub に送る" not in section
    assert "世話役の確認を待っています。" not in section

    # つないだ会社: GitHub に送る
    reply_env()
    section = replies_section(html_of(client.get(f"/feedback/{row.pk}/")))
    assert f'action="/feedback/replies/{reply.pk}/send/"' in section and "GitHub に送る" in section
    assert "data-reply-copy" not in section and "送ったにする" not in section

    # 送った返事には形を出さない
    services.mark_reply_sent(reply, manager)
    section = replies_section(html_of(client.get(f"/feedback/{row.pk}/")))
    assert f"feedback-reply-body-{reply.pk}" not in section
    assert '<span class="badge badge-sm">送った</span>' in section


def test_reply_body_is_plain_text(client, plain, manager, reply_env, monkeypatch):
    row = make(plain, "字のままの題")
    services.mark_sent(row, manager, sent_url=ISSUE_URL)
    github_replies(row, monkeypatch, (201, "example-author", '<script>alert("x")</script>\n**太字** <b>b</b>'))

    client.force_login(plain)
    section = replies_section(html_of(client.get(f"/feedback/{row.pk}/")))
    assert "<script>alert" not in section and "<b>b</b>" not in section
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;\n**太字** &lt;b&gt;b&lt;/b&gt;" in section
    assert '<p class="mt-2 whitespace-pre-wrap break-words">&lt;script&gt;' in section


def test_reply_urls():
    assert reverse("feedback:reply_add", args=[1]) == "/feedback/1/replies/"
    assert reverse("feedback:reply_send", args=[2]) == "/feedback/replies/2/send/"
    assert reverse("feedback:reply_mark_sent", args=[2]) == "/feedback/replies/2/sent/"
