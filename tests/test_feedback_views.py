"""フィードバックの画面 (一覧・新規登録・詳細・編集)・口 (確認済み・送った)・左のメニューの下の入口のテスト。

画面の HTML は Django の test client で開いて見る。本物の API は呼ばない (autouse の fixture が assistant の
_ask_claude を「呼ばれたら AssertionError」に差し替える)。画面の目録の控えは試験の前後で捨てる。題と名前は作り物。
"""

import re
from urllib.parse import urlencode

import pytest
import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils.html import escape

from seizou.assistant import services as assistant_services
from seizou.core import access
from seizou.feedback import services
from seizou.feedback.models import Feedback
from seizou.feedback import github
from tests import test_feedback_github as gh_tests
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


# ---- 送る前に直せる・うちも・自社の印・閉じた理由 (依頼書 N323。決め 11-07 09:0x〜10:1x) ----

GH_KEYS = (github.TOKEN_ENV, github.REPO_ENV, github.LOGIN_ENV)


@pytest.fixture
def gh(settings, tmp_path, monkeypatch):
    """GitHub の作り物 (requests.get・requests.post を差し替える。本物へは送らない)。

    .env は一時の置き場で、3 つの鍵と django の cache は前後で戻す。つないだ形は connect_env が os.environ に置く (.env が無ければ os.environ を読む)。
    """
    settings.SEIZOU_ENV_FILE = tmp_path / ".env"
    for key in GH_KEYS:
        monkeypatch.delenv(key, raising=False)
    cache.clear()
    fake = gh_tests.FakeGithub()
    monkeypatch.setattr(requests, "get", fake.get)
    monkeypatch.setattr(requests, "post", fake.post)
    yield fake
    cache.clear()


def connect_env(monkeypatch, repo="kishii-py/seizou"):
    """つないだ会社にする (作り物のトークンと送り先)。"""
    monkeypatch.setenv(github.TOKEN_ENV, gh_tests.TOKEN)
    monkeypatch.setenv(github.REPO_ENV, repo)


def test_detail_send_form_has_issue_text_only_for_manager_connected_confirmed(client, plain, manager, gh, monkeypatch):
    row = make(plain)
    services.mark_confirmed(row, manager)
    detail = f"/feedback/{row.pk}/"

    client.force_login(manager)
    assert 'name="issue_text"' not in html_of(client.get(detail))

    connect_env(monkeypatch)
    html = html_of(client.get(detail))
    assert html.count('name="issue_text"') == 1
    area = re.search(r'<textarea id="feedback-issue-edit" name="issue_text"[^>]*>(.*?)</textarea>', html, re.S)
    assert area is not None and area.group(1) == escape(services.export_markdown(row))
    assert "伏せ字 (〇〇) のうち、外に出してよい数や名前だけを戻してから送ります。単価や取引先の名前は伏せたままにします。" in html
    assert html.index('id="feedback-issue-edit"') < html.index("GitHub に送る</button>") < html.index("送ったにする</button>")
    assert 'id="feedback-issue" readonly' in html

    draft = make(plain, "下書きのまま")
    assert 'name="issue_text"' not in html_of(client.get(f"/feedback/{draft.pk}/"))

    client.force_login(plain)
    assert 'name="issue_text"' not in html_of(client.get(detail))


ME_TOO_URL = "/feedback/issues/1/me-too/"


def one_issue(fake):
    """みんなの Issue の作り物の 1 件 (#1。本文は不具合)。"""
    fake.listing = gh_tests.Answer(200, [gh_tests.issue(1, "請求の合計が 1 ずれる", gh_tests.ME_TOO_BODY, plus=2)])


def test_me_too_endpoint_returns_to_issues_with_green_notice(client, plain, gh, monkeypatch):
    one_issue(gh)
    connect_env(monkeypatch)
    client.force_login(plain)

    response = client.post(ME_TOO_URL, {"issue_q": "請求 ずれる"})

    assert response.status_code == 302
    assert response["Location"] == "/feedback/?" + urlencode({"tab": "issues", "issue_q": "請求 ずれる"}) + "#issues"
    row = Feedback.objects.get()
    assert (row.status, row.created_by, row.sent_url) == ("sent", plain, "https://github.com/kishii-py/seizou/issues/1")
    assert len(gh_tests.reactions(gh)) == 1
    html = html_of(client.get(response["Location"]))
    assert "成功しました。" in html and "Issue #1 に「うちも」を付けました。" in html

    response = client.post(ME_TOO_URL)
    assert response["Location"] == "/feedback/?tab=issues#issues"
    html = html_of(client.get(response["Location"]))
    assert "失敗しました。" in html and "保存できません。この Issue には、あなたの「うちも」が既にあります。" in html
    assert Feedback.objects.count() == 1
    assert client.get(ME_TOO_URL).status_code == 405


def test_me_too_endpoint_without_connection_leaves_draft_with_yellow_notice(client, plain, gh):
    one_issue(gh)
    client.force_login(plain)

    response = client.post(ME_TOO_URL, {"issue_q": ""})

    assert response["Location"] == "/feedback/?tab=issues&issue_q=#issues"
    assert not gh_tests.reactions(gh)
    assert Feedback.objects.get().status == "draft"
    html = html_of(client.get(response["Location"]))
    assert "確認してください。" in html
    assert "Issue #1 を同じ困りごととして下書きに残しました (GitHub とつないでいないので 👍 は付けていません)。" in html

    client.logout()
    response = client.post(ME_TOO_URL)
    assert response.status_code == 302 and "/login/" in response["Location"]
    assert Feedback.objects.count() == 1
