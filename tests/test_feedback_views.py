"""フィードバックの画面 (一覧・新規登録・詳細・編集)・口 (確認済み・送った)・左のメニューの下の入口のテスト。

画面の HTML は Django の test client で開いて見る。本物の API は呼ばない (autouse の fixture が assistant の
_ask_claude を「呼ばれたら AssertionError」に差し替える)。画面の目録の控えは試験の前後で捨てる。題と名前は作り物。
"""

import re
from urllib.parse import urlencode

import pytest
import requests
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.urls import reverse
from django.utils.html import escape

from seizou.assistant import services as assistant_services
from seizou.core import access
from seizou.feedback import services
from seizou.feedback import github
from seizou.feedback.models import Feedback
from seizou.feedback.templatetags import feedback_tags
from tests import test_feedback_github as gh_tests
from tests.test_assistant_services import refuse_api
from tests.test_feedback_github import Answer, FakeGithub, issue


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(assistant_services, "_ask_claude", refuse_api)


@pytest.fixture(autouse=True)
def fresh_catalog():
    services.screen_catalog.cache_clear()
    yield
    services.screen_catalog.cache_clear()


@pytest.fixture(autouse=True)
def no_real_github(monkeypatch):
    """本物の GitHub へ送らない (依頼書 N325)。「みんなの Issue」のタブは開くと盤のために GitHub を読むので、
    作り物の GitHub (gh・github_listing) を使わない試験では、requests をつながらない形にする (盤は読めなかった 1 文を出す)。
    作り物の fixture は autouse の後に差し替えるので、そちらが勝つ。
    """

    def unreachable(*args, **kwargs):
        raise requests.ConnectionError("試験では本物の GitHub につながない")

    monkeypatch.setattr(requests, "get", unreachable)
    monkeypatch.setattr(requests, "post", unreachable)


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
    html = html_of(client.get("/feedback/?f_status=draft"))
    assert "下書きの不具合" in html
    assert "確認済みの要望" not in html and "ほかの人の下書き" not in html
    # 絞りの置き場は一覧のカードの中の「絞り込み」の枠 1 つ (決め UX-FEEDBACK-1。候補 C114)
    assert 'id="feedback-filter"' not in html
    assert '<select name="status"' not in html and '<select name="kind"' not in html
    assert "絞り込み</button>" not in html
    assert html.index('id="list-card"') < html.index('id="list-f-status"') < html.index('id="list-f-kind"') < html.index('id="rows"')
    assert 'name="f_status"' in html and 'name="f_kind"' in html
    assert '<option value="draft" selected>' in html
    assert '状態: <span class="font-medium">下書き</span>' in html and "絞りを外す" in html

    html = html_of(client.get("/feedback/?f_kind=common"))
    assert "確認済みの要望" in html and "下書きの不具合" not in html

    html = html_of(client.get("/feedback/?f_status=draft&f_kind=common"))
    assert "確認済みの要望" not in html and "下書きの不具合" not in html

    html = html_of(client.get("/feedback/?f_status=nonsense&f_kind="))
    assert "確認済みの要望" in html and "下書きの不具合" in html
    assert "絞りを外す" not in html

    # 古い GET の名前 (status・kind) は土台が見ない (読み替えない。候補 C114 の「作らない物」)
    html = html_of(client.get("/feedback/?status=draft"))
    assert "確認済みの要望" in html and "下書きの不具合" in html

    html = html_of(client.get("/feedback/?f_status=confirmed", HTTP_HX_REQUEST="true"))
    assert "<html" not in html
    assert "確認済みの要望" in html and "下書きの不具合" not in html
    assert 'id="list-filter-clear" hx-swap-oob="true"' in html

    client.force_login(manager)
    html = html_of(client.get("/feedback/?f_status=draft"))
    assert "下書きの不具合" in html and "ほかの人の下書き" in html
    assert "確認済みの要望" not in html


def test_list_filter_specs_accept_known_values_only(db):
    from django.http import QueryDict

    from seizou.feedback.views import FeedbackListView

    specs = {spec.name: spec for spec in FeedbackListView().filter_specs()}
    assert (specs["status"].param, specs["kind"].param) == ("f_status", "f_kind")
    assert specs["status"].value({"f_status": "draft"}) == "draft"
    assert specs["kind"].value({"f_kind": "bug"}) == "bug"
    assert specs["status"].value({"f_status": "x"}) is None
    assert specs["kind"].value({"f_kind": ""}) is None
    assert specs["status"].value({}) is None
    both = QueryDict("f_status=sent&f_kind=common")
    assert (specs["status"].value(both), specs["kind"].value(both)) == ("sent", "common")
    assert specs["status"].choice_values() == [(value, str(label)) for value, label in Feedback.STATUS_CHOICES]


def test_list_summary_opens(client, plain):
    # 土台の集計 (状態と種類の軸) がこの表で初めて動くので、落ちないことだけを見る (候補 C114 の (h))
    make(plain, "下書きの不具合", data={"kind": "bug"})
    client.force_login(plain)
    assert "集計</a>" in html_of(client.get("/feedback/"))

    response = client.get("/feedback/", {"view": "summary"})
    html = html_of(response)
    assert response.status_code == 200
    assert 'id="list-g-by"' in html and "表</a>" in html


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

    html = html_of(client.get("/feedback/?f_status=dismissed"))
    assert "見送った題" in html and "版待ちの題" not in html
    assert '<option value="dismissed" selected>' in html


# ---------------------------------------------------------------- タブの帯 (依頼書 N321。決め 11-07 09:1x〜09:2x)

TAB_IDS = ["feedback-tab-send", "feedback-tab-sent", "feedback-tab-issues"]
HIDDEN_TAB = '<input type="hidden" name="tab" value="issues">'


def tabs_of(html: str) -> str:
    """タブの帯 (#feedback-tabs) の HTML。"""
    return re.search(r'<div id="feedback-tabs".*?</div>', html, re.S).group(0)


def selected_tabs(html: str) -> list[str]:
    """帯の中で tab-active の付いたタブの id の並び。"""
    return re.findall(r'<a id="(feedback-tab-[a-z]+)" role="tab" class="tab tab-active"', tabs_of(html))


def sent_count(html: str) -> int:
    """帯の「送った一言」の件数の札の数。"""
    return int(re.search(r'id="feedback-tab-sent"[^>]*>送った一言<span class="badge[^"]*">(\d+)</span>', html).group(1))


@pytest.fixture
def github_listing(monkeypatch, settings, tmp_path):
    """皆の Issue の一覧を返す作り物の GitHub (requests.get・requests.post を差し替える。本物の GitHub へは送らない)。

    .env は一時の置き場、GitHub の鍵の環境変数は外し、django の cache は前後で捨てる (test_feedback_github.py の env_file と同じ)。
    """
    settings.SEIZOU_ENV_FILE = tmp_path / ".env"
    for key in (github.TOKEN_ENV, github.REPO_ENV, github.LOGIN_ENV):
        monkeypatch.delenv(key, raising=False)
    fake = FakeGithub()
    fake.listing = Answer(
        200,
        [
            issue(1, "受注の一覧で並びが戻る", "並び替えた後に戻ると元に戻る", updated="2026-11-02T00:00:00Z", plus=3),
            issue(2, "出荷の画面が重い", "受注を開くと待つ", state="closed", updated="2026-11-05T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(requests, "get", fake.get)
    monkeypatch.setattr(requests, "post", fake.post)
    cache.clear()
    yield fake
    cache.clear()


@pytest.mark.parametrize(
    ("url", "chosen", "below"),
    [
        ("/feedback/new/", "feedback-tab-send", 'id="feedback-issues-link"'),
        ("/feedback/", "feedback-tab-sent", 'id="list-card"'),
        ("/feedback/?tab=issues", "feedback-tab-issues", 'id="issues"'),
    ],
)
def test_tabs_are_the_same_on_three_screens(client_logged_in, url, chosen, below):
    response = client_logged_in.get(url)
    html = html_of(response)

    assert response.status_code == 200
    assert html.count('id="feedback-tabs"') == 1
    bar = tabs_of(html)
    assert 'role="tablist" aria-label="フィードバックの区分" class="tabs tabs-border mb-4"' in bar
    assert re.findall(r'id="(feedback-tab-[a-z]+)"', bar) == TAB_IDS
    assert bar.count('role="tab"') == 3
    assert ">送る</a>" in bar and ">送った一言<span" in bar and ">みんなの Issue</a>" in bar
    assert selected_tabs(html) == [chosen]
    assert bar.count("tab-active") == 1 and bar.count('aria-selected="true"') == 1
    assert bar.count('<span class="badge') == 1
    assert 'href="/feedback/"' in bar and 'href="/feedback/?tab=issues"' in bar
    assert html.index('id="feedback-tabs"') < html.index(below)


def test_sent_tab_counts_own_and_manager_counts_all(client, plain, other, manager):
    make(plain, "自分の題")
    make(plain, "自分の 2 つ目の題")
    make(other, "ほかの人の題")

    client.force_login(plain)
    for url in ("/feedback/new/", "/feedback/", "/feedback/?tab=issues"):
        assert sent_count(html_of(client.get(url))) == 2

    client.force_login(manager)
    assert sent_count(html_of(client.get("/feedback/new/"))) == 3
    assert sent_count(html_of(client.get("/feedback/"))) == 3

    assert services.visible_count(plain) == 2
    assert services.visible_count(other) == 1
    assert services.visible_count(manager) == 3
    assert services.visible_count(AnonymousUser()) == 0
    assert services.visible_count(None) == 0


def test_issues_tab_shows_only_search_and_keeps_tab(client, plain, manager, github_listing):
    make(plain, "自分の題")
    client.force_login(plain)
    html = html_of(client.get("/feedback/?tab=issues"))

    assert 'id="issues"' in html and 'id="issues-form"' in html
    assert 'id="rows"' not in html and 'id="list-f-status"' not in html
    assert "自分の題" not in html
    assert 'action="/feedback/?tab=issues#issues"' in html
    assert HIDDEN_TAB in html
    # 「みんなの Issue」のタブは盤が GitHub を読む (開いている物と閉じた物の 2 回。N325 = 367 回目)
    assert [call[2]["params"]["state"] for call in github_listing.calls] == ["open", "closed"]

    html = html_of(client.get("/feedback/", {"tab": "issues", "issue_q": "受注 戻る"}))
    assert selected_tabs(html) == ["feedback-tab-issues"]
    assert 'href="https://github.com/kishii-py/seizou/issues/1"' in html
    assert "受注の一覧で並びが戻る" in html and "出荷の画面が重い" not in html
    assert 'value="受注 戻る"' in html
    assert HIDDEN_TAB in html
    assert 'id="rows"' not in html and 'id="list-f-status"' not in html
    # 盤は控えから読み、tag の feedback_github が今までの探す (state=all) を 1 回読む (N325)
    assert [call[2]["params"]["state"] for call in github_listing.calls] == ["open", "closed", "all"]

    html = html_of(client.get("/feedback/"))
    assert 'id="rows"' in html and 'id="list-f-status"' in html and 'id="issues"' in html
    assert 'action="/feedback/#issues"' in html and HIDDEN_TAB not in html

    client.force_login(manager)
    assert 'id="feedback-github-settings"' in html_of(client.get("/feedback/"))
    assert 'id="feedback-github-settings"' not in html_of(client.get("/feedback/?tab=issues"))


def test_send_tab_keeps_screen_and_issue_link_opens_issues_tab(client_logged_in):
    html = html_of(client_logged_in.get("/feedback/new/?screen=orders%3Aorder_list"))
    bar = tabs_of(html)

    assert 'id="feedback-tab-send" role="tab" class="tab tab-active" href="/feedback/new/?screen=orders%3Aorder_list"' in bar
    link = re.search(r'<p id="feedback-issues-link".*?</p>', html, re.S).group(0)
    assert 'href="/feedback/?tab=issues#issues"' in link
    assert "似た困りごとが GitHub に出ていないか探す" in link
    assert html.index('id="feedback-tabs"') < html.index('id="feedback-issues-link"')

    assert 'id="feedback-tab-send" role="tab" class="tab" href="/feedback/new/"' in tabs_of(html_of(client_logged_in.get("/feedback/")))


def test_detail_and_update_screens_have_no_tabs(client_logged_in, plain):
    row = make(plain)

    for url in (f"/feedback/{row.pk}/", f"/feedback/{row.pk}/edit/"):
        response = client_logged_in.get(url)
        assert response.status_code == 200
        assert 'id="feedback-tabs"' not in html_of(response)


def test_tabs_tag_without_request_selects_nothing():
    tabs = feedback_tags.feedback_tabs({})

    assert [tab["key"] for tab in tabs] == ["send", "sent", "issues"]
    assert [tab["label"] for tab in tabs] == ["送る", "送った一言", "みんなの Issue"]
    assert [tab["href"] for tab in tabs] == ["/feedback/new/", "/feedback/", "/feedback/?tab=issues"]
    assert [tab["count"] for tab in tabs] == [None, 0, None]
    assert not any(tab["selected"] for tab in tabs)


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


def issue_rows(html) -> dict:
    """みんなの Issue の節の行を {番号: 行の HTML} にする。"""
    section = html[html.index('<section id="issues"') :]
    rows = re.findall(r"<tr>.*?</tr>", section, re.S)
    return {int(number): row for row in rows for number in re.findall(r'href="https://github.com/kishii-py/seizou/issues/(\d+)"', row)}


def three_issues(fake):
    """みんなの Issue の作り物の 3 件 (#1 は自社が送った件の Issue、#2 は「うちも」を押す Issue、#3 は印の無い Issue)。"""
    fake.listing = gh_tests.Answer(
        200,
        [
            gh_tests.issue(1, "請求の合計が 1 ずれる", gh_tests.ME_TOO_BODY, updated="2026-11-06T00:00:00Z", plus=2),
            gh_tests.issue(2, "受注の CSV に列を足したい", "列を足したい", updated="2026-11-05T00:00:00Z", plus=1),
            gh_tests.issue(3, "出荷の画面が重い", "待つ", updated="2026-11-04T00:00:00Z"),
        ],
    )


def test_issue_rows_show_our_marks_and_me_too_button(client, plain, other, manager, gh, monkeypatch):
    three_issues(gh)
    connect_env(monkeypatch)
    sent = make(other, "自社が送った件")
    services.mark_sent(sent, manager, sent_url="https://github.com/kishii-py/seizou/issues/1")
    services.me_too(1, plain)
    mine = services.me_too(2, plain)["feedback"]

    client.force_login(plain)
    html = html_of(client.get("/feedback/", {"issue_q": ""}))
    assert "同じ困りごとなら「うちも」を押してください (会社のトークンで 👍 が付き、あなたの件として残ります)。" in html
    rows = issue_rows(html)
    assert sorted(rows) == [1, 2, 3]

    # 自社が送った件とうちもの両方がある行は「自社が送った」だけ。ほかの人の件なので link にしない (詳細は 404 のため)。
    assert '<span class="badge badge-soft badge-info">自社が送った</span>' in rows[1]
    assert "うちも済み" not in rows[1] and f'href="/feedback/{sent.pk}/"' not in rows[1]
    assert "/me-too/" not in rows[1] and '<td class="text-right tabular-nums">2</td>' in rows[1]

    assert f'<a class="badge badge-soft badge-info" href="/feedback/{mine.pk}/">うちも済み</a>' in rows[2]
    assert "/me-too/" not in rows[2] and '<td class="text-right tabular-nums">1</td>' in rows[2]

    assert "自社が送った" not in rows[3] and "うちも済み" not in rows[3]
    assert 'action="/feedback/issues/3/me-too/"' in rows[3]
    assert '<input type="hidden" name="issue_q" value="">' in rows[3]
    assert 'aria-label="うちも (Issue #3)">👍 0 うちも</button>' in rows[3]

    client.force_login(manager)
    rows = issue_rows(html_of(client.get("/feedback/", {"issue_q": "請求"})))
    assert sorted(rows) == [1]
    assert f'<a class="badge badge-soft badge-info" href="/feedback/{sent.pk}/">自社が送った</a>' in rows[1]

    assert "うちも済み" not in html_of(client.get("/feedback/"))


def test_issue_rows_show_closed_reason_and_milestone(client, plain, gh):
    gh.listing = gh_tests.Answer(
        200,
        [
            gh_tests.issue(1, "直った件", state="closed", state_reason="completed", milestone={"title": "0.1.1"}),
            gh_tests.issue(2, "対応しない件", state="closed", state_reason="not_planned"),
            gh_tests.issue(3, "重複の件", state="closed", state_reason="duplicate"),
            gh_tests.issue(4, "理由の無い件", state="closed"),
            gh_tests.issue(5, "版待ちの件", milestone={"title": "0.1.2"}),
        ],
    )
    client.force_login(plain)

    rows = issue_rows(html_of(client.get("/feedback/", {"issue_q": ""})))

    assert '<span class="badge badge-soft badge-success">直した</span>' in rows[1] and "で直る" not in rows[1]
    assert '<span class="badge badge-ghost">見送った</span>' in rows[2]
    assert '<span class="badge badge-ghost">見送った</span>' in rows[3]
    assert '<span class="badge badge-ghost">閉じた</span>' in rows[4] and "見送った" not in rows[4]
    assert '<span class="badge badge-soft badge-success">開いている</span>' in rows[5]
    assert '<span class="badge badge-soft badge-info">0.1.2 で直る</span>' in rows[5]


# ---------------------------------------------------------------- みんなの Issue の盤 (依頼書 N325。決め 11-07 09:3x〜10:1x)


def board_answers(fake):
    """盤の作り物の Issue。開いている 3 件 (#7 直す予定・#8 受け付けた・#9 直している) と閉じた 1 件 (#10 直した)。見送ったは 0 件。"""
    fake.board[("open", 1)] = gh_tests.Answer(
        200,
        [
            gh_tests.board_issue(7, "請求の合計が 1 ずれる", body=gh_tests.ME_TOO_BODY, milestone={"title": "0.1.1"}, plus=2),
            gh_tests.board_issue(8, "受注の CSV に列を足したい", labels=[{"name": "要望"}], plus=4, updated_days=3),
            gh_tests.board_issue(9, "出荷の画面が重い", labels=[{"name": "直している"}], updated_days=2),
        ],
    )
    fake.board[("closed", 1)] = gh_tests.Answer(
        200, [gh_tests.board_issue(10, "直った件", state="closed", state_reason="completed", closed_at=gh_tests.ago(2))]
    )


def board_of(html: str) -> str:
    """盤 (#issue-board) の HTML。"""
    return html[html.index('<div id="issue-board"') : html.index("</section>\n</div>", html.index('<div id="issue-board"'))]


def card_of(html: str, number: int) -> str:
    """盤の番号のカードの HTML。"""
    start = html.index(f'<span class="font-mono text-xs text-base-content/50">#{number}</span>')
    return html[start : html.index("</article>", start)]


def href(params: dict) -> str:
    """一覧の URL を HTML の属性の字 (& は &amp;) にした物。"""
    return escape("/feedback/?" + urlencode(params))


def test_issues_tab_shows_board_with_five_columns_and_cards(client, plain, manager, gh):
    board_answers(gh)
    sent = make(plain, "請求の合計がずれる")
    services.mark_sent(sent, manager, sent_url=f"{gh_tests.SEIZOU_ISSUES}7")
    client.force_login(plain)

    response = client.get("/feedback/?tab=issues")
    html = html_of(response)

    assert response.status_code == 200
    assert html.count('id="issue-board"') == 1 and 'id="issue-table"' not in html
    board = board_of(html)
    # 件数 1 以上の 4 列は左から BOARD_COLUMNS の並び、件数 0 の「見送った」は右端の箱に札だけ。
    assert board.count('<section class="w-72 shrink-0 rounded-xl border border-base-300 bg-base-200 p-3"') == 4
    heads = re.findall(r'<h3 id="issue-column-([a-z]+)" class="flex items-center">', board)
    assert heads == ["inbox", "planned", "working", "fixed"]
    for badge in (
        '<span class="badge badge-ghost">受け付けた</span>',
        '<span class="badge badge-soft badge-info">直す予定</span>',
        '<span class="badge badge-soft badge-warning">直している</span>',
        '<span class="badge badge-soft badge-success">直した</span>',
    ):
        assert badge in board
    assert board.count('<span class="badge badge-sm badge-ghost ml-2 tabular-nums">1</span>') == 4
    empty = board[board.index('id="issue-columns-empty"') :]
    assert "0 件の列" in empty and '<span class="badge badge-outline badge-error">見送った</span>' in empty
    assert 'id="issue-column-dismissed"' not in board

    # 自社が送った件のカード (#7): 題は自社の件の詳細へ、種類は本文の行、直る版、自社の印、押せない 👍。
    mine = card_of(html, 7)
    assert f'href="/feedback/{sent.pk}/">請求の合計が 1 ずれる</a>' in mine
    assert '<span class="badge badge-sm badge-ghost">不具合</span>' in mine
    assert '<span class="badge badge-sm badge-soft badge-info">0.1.1 で直る</span>' in mine
    assert '<span class="badge badge-sm badge-soft badge-primary">自社が送った</span>' in mine
    assert '<span class="btn btn-xs btn-outline border-base-300 pointer-events-none" aria-disabled="true">👍 2</span>' in mine
    assert "/me-too/" not in mine and "昨日" in mine

    # 印の無いカード (#8): 題は GitHub を新しいタブで、👍 は「うちも」の口への POST (GET に issue_q が無いので隠しの issue_q も無い)。
    other = card_of(html, 8)
    assert 'href="https://github.com/kishii-py/seizou/issues/8" target="_blank" rel="noopener">受注の CSV に列を足したい</a>' in other
    assert '<span class="badge badge-sm badge-ghost">要望</span>' in other and "3 日前" in other
    assert 'action="/feedback/issues/8/me-too/"' in other and 'name="issue_q"' not in other
    assert 'aria-label="うちも (Issue #8 に 👍 を付ける)" title="うちも (同じ困りごと)">👍 4</button>' in other
    assert "自社が送った" not in other and "うちも済み" not in other

    # 並べ替えと切り替え (初めは 👍 の多い順と盤)。
    assert f'<a class="join-item btn btn-sm btn-active" href="{href({"tab": "issues"})}" aria-current="true">👍 の多い順</a>' in html
    assert f'<a class="join-item btn btn-sm" href="{href({"tab": "issues", "sort": "new"})}">新しい順</a>' in html
    assert f'<a class="join-item btn btn-sm btn-active" href="{href({"tab": "issues"})}" aria-current="true">盤</a>' in html
    assert f'<a class="join-item btn btn-sm" href="{href({"tab": "issues", "view": "list"})}">一覧</a>' in html
    assert 'name="view"' not in html and 'name="sort"' not in html

    # タブの外 (送った一言) は盤を描かず、GitHub も読まない。
    gh.calls.clear()
    html = html_of(client.get("/feedback/"))
    assert 'id="issue-board"' not in html and 'id="issue-sorts"' not in html and not gh.calls


def test_issues_tab_list_view_and_links_keep_tab_words_and_choice(client, plain, gh):
    board_answers(gh)
    client.force_login(plain)

    html = html_of(client.get("/feedback/", {"tab": "issues", "view": "list", "sort": "new", "issue_q": "受注"}))

    assert 'id="issue-table"' in html and 'id="issue-board"' not in html
    table = html[html.index('<table id="issue-table"') : html.index("</table>")]
    assert "受注の CSV に列を足したい" in table and "出荷の画面が重い" not in table
    row = re.search(r"<tr>\s*<td[^>]*>#8</td>.*?</tr>", table, re.S).group(0)
    assert '<span class="badge badge-ghost">受け付けた</span>' in row
    assert '<span class="badge badge-sm badge-ghost">要望</span>' in row
    assert 'action="/feedback/issues/8/me-too/"' in row and '<input type="hidden" name="issue_q" value="受注">' in row
    assert "<th>列</th><th>種類</th><th>直る版</th><th>自社</th>" in table

    # 探す form は今の view と sort を隠しで保ち、link は tab と言葉ともう片方の選びを保つ。
    assert '<input type="hidden" name="view" value="list">' in html and '<input type="hidden" name="sort" value="new">' in html
    assert 'value="受注"' in html and '<input type="hidden" name="tab" value="issues">' in html
    keep = {"tab": "issues", "issue_q": "受注"}
    assert f'href="{href({**keep, "view": "list"})}">👍 の多い順</a>' in html
    assert f'href="{href({**keep, "view": "list", "sort": "new"})}" aria-current="true">新しい順</a>' in html
    assert f'href="{href({**keep, "sort": "new"})}">盤</a>' in html
    assert f'href="{href({**keep, "view": "list", "sort": "new"})}" aria-current="true">一覧</a>' in html


def test_issues_tab_says_one_line_when_github_is_unreadable_or_nothing_matches(client, plain, gh):
    gh.listing = gh_tests.Answer(403, {"message": "rate limit"})
    client.force_login(plain)

    for params in ({"tab": "issues"}, {"tab": "issues", "view": "list"}):
        response = client.get("/feedback/", params)
        html = html_of(response)
        assert response.status_code == 200
        assert "GitHub の Issue を読めませんでした (送り先 kishii-py/seizou)。" in html
        assert 'id="issue-board"' not in html and 'id="issue-table"' not in html

    cache.clear()
    board_answers(gh)
    html = html_of(client.get("/feedback/", {"tab": "issues", "issue_q": "当たらない言葉"}))
    assert "当たる Issue はありませんでした。言葉を減らすか、空にして「探す」を押してください。" in html
    assert 'id="issue-board"' not in html


def test_board_card_me_too_returns_to_board_with_mark(client, plain, gh, monkeypatch):
    board_answers(gh)
    connect_env(monkeypatch)
    client.force_login(plain)
    gh.listing = gh_tests.Answer(200, [gh_tests.board_issue(8, "受注の CSV に列を足したい", plus=4)])

    response = client.post("/feedback/issues/8/me-too/")

    assert response["Location"] == "/feedback/?tab=issues#issues"
    html = html_of(client.get(response["Location"]))
    card = card_of(html, 8)
    assert '<span class="badge badge-sm badge-soft badge-primary">うちも済み</span>' in card
    assert 'aria-disabled="true">👍' in card and "/me-too/" not in card
    row = Feedback.objects.get()
    assert f'href="/feedback/{row.pk}/">受注の CSV に列を足したい</a>' in card


def test_board_tag_without_request_reads_nothing():
    board = feedback_tags.feedback_board({})

    assert (board["view"], board["sort"], board["q"], board["keep_q"], board["board"]) == ("board", "top", "", False, None)
    assert board["columns_shown"] == [] and board["columns_empty"] == []
    assert [(link["label"], link["href"], link["selected"]) for link in board["sorts"]] == [
        ("👍 の多い順", "/feedback/?tab=issues", True),
        ("新しい順", "/feedback/?tab=issues&sort=new", False),
    ]
    assert [(link["label"], link["href"], link["selected"]) for link in board["views"]] == [
        ("盤", "/feedback/?tab=issues", True),
        ("一覧", "/feedback/?tab=issues&view=list", False),
    ]
