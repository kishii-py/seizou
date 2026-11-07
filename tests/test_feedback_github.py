"""フィードバックの GitHub の口 (依頼書 N185。決め 11-06 21:2x) の試験。

本物の GitHub へは送らない (requests.get・requests.post を差し替える)。トークンは作り物の短い字 (ghp_test…)。
.env は試験ごとの一時の置き場 (settings.SEIZOU_ENV_FILE)。os.environ の 3 つの鍵と django の cache は試験の前後で戻す。
題と名前は作り物。
"""

import os
import stat
import sys

import pytest
import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache

from seizou.feedback import github, services
from seizou.feedback.models import Feedback

TOKEN = "ghp_test0000fake"
KEYS = (github.TOKEN_ENV, github.REPO_ENV, github.LOGIN_ENV)
ISSUE_URL = "https://github.com/example-owner/example-repo/issues/7"
#: デバイスの認証の作り物の値 (依頼書 N320)
OAUTH_TOKEN = "gho_test0000fake"
CLIENT_ID = "client-id-test0000fake"
DEVICE_CODE = "device-code-test0000fake"
USER_CODE = "WDJB-MJHT"
DEVICE_ANSWER = {
    "device_code": DEVICE_CODE,
    "user_code": USER_CODE,
    "verification_uri": "https://github.com/login/device",
    "expires_in": 900,
    "interval": 5,
}


class Answer:
    """requests の返事の代わり (status_code と json だけ)。"""

    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data

    def json(self):
        if self._data is None:
            raise ValueError("JSON でない")
        return self._data


class FakeGithub:
    """requests.get・requests.post の代わり。呼ばれた (method, url, kwargs) を控え、答えの表から返す。"""

    def __init__(self):
        self.calls = []
        self.user = Answer(200, {"login": "example-feedback-bot"})
        self.created = Answer(201, {"number": 7, "html_url": ISSUE_URL})
        self.issues = {}
        self.listing = Answer(200, [])
        self.device = Answer(200, dict(DEVICE_ANSWER))
        self.token = Answer(200, {"error": "authorization_pending"})
        #: Issue の返事 (依頼書 N324): 読む並びと、書いたときの返事
        self.comments = Answer(200, [])
        self.comment_created = Answer(
            201,
            {"id": 9001, "html_url": f"{ISSUE_URL}#issuecomment-9001", "created_at": "2026-11-07T01:00:00Z", "user": {"login": "example-feedback-bot"}},
        )
        self.reaction = Answer(201, {"id": 1, "content": "+1"})
        #: みんなの Issue の盤 (依頼書 N325): Issue の並びの params の (state, page) → 答え。無い組は listing を返す (既にある試験の形のまま)
        self.board = {}

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if url.endswith("/user"):
            return self.user
        if url.endswith("/issues"):
            params = kwargs.get("params") or {}
            board = self.board.get((params.get("state"), params.get("page", 1)))
            if board is not None:
                return board
            return self.listing
        if url.endswith("/comments"):
            return self.comments
        if "/issues/" in url:
            return self.issues.get(int(url.rsplit("/", 1)[1]), Answer(404, {}))
        raise AssertionError(f"知らない URL: {url}")

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url == github.DEVICE_CODE_URL:
            return self.device
        if url == github.DEVICE_TOKEN_URL:
            return self.token
        if url.endswith("/comments"):
            return self.comment_created
        if url.endswith("/reactions"):
            return self.reaction
        return self.created


@pytest.fixture(autouse=True)
def env_file(settings, tmp_path):
    path = tmp_path / ".env"
    settings.SEIZOU_ENV_FILE = path
    saved = {key: os.environ.pop(key) for key in KEYS if key in os.environ}
    cache.clear()
    yield path
    cache.clear()
    for key in KEYS:
        os.environ.pop(key, None)
    os.environ.update(saved)


@pytest.fixture
def fake(monkeypatch):
    fake = FakeGithub()
    monkeypatch.setattr(requests, "get", fake.get)
    monkeypatch.setattr(requests, "post", fake.post)
    return fake


@pytest.fixture
def no_http(monkeypatch):
    """呼ばれたら落とす requests (HTTP を呼ばないことを見る)。"""

    def refuse(*args, **kwargs):
        raise AssertionError("HTTP を呼んだ")

    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests, "post", refuse)


@pytest.fixture
def manager(db):
    return get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")


@pytest.fixture
def plain(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


def make(user, title="受注の一覧で並びが戻る", status="draft", sent_url=""):
    row = services.create_feedback(
        {"kind": "bug", "screen": "", "title": title, "problem": "並び替えた後に戻ると、並びが元に戻る。"}, user
    )
    if status != "draft":
        Feedback.objects.filter(pk=row.pk).update(status=status, sent_url=sent_url)
        row.refresh_from_db()
    return row


def connect(manager, fake, repo="example-owner/example-repo"):
    return services.connect_github({"token": TOKEN, "repo": repo}, manager)


# ---------------------------------------------------------------- .env の読み書き


def test_write_env_value_replaces_and_keeps_other_lines(env_file):
    env_file.write_text("# 鍵\nSECRET_KEY=plain-words\nSEIZOU_GITHUB_LOGIN=old\nDEBUG=1\n", encoding="utf-8")

    github.write_env_value(github.LOGIN_ENV, "new-name")
    github.write_env_value(github.REPO_ENV, "example-owner/example-repo")

    assert env_file.read_text(encoding="utf-8").splitlines() == [
        "# 鍵",
        "SECRET_KEY=plain-words",
        "SEIZOU_GITHUB_LOGIN=new-name",
        "DEBUG=1",
        "SEIZOU_GITHUB_ISSUES_REPO=example-owner/example-repo",
    ]
    assert os.environ[github.LOGIN_ENV] == "new-name"
    assert github.read_env_value(github.LOGIN_ENV) == "new-name"
    if sys.platform != "win32":
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    github.remove_env_value(github.LOGIN_ENV)
    assert "SEIZOU_GITHUB_LOGIN" not in env_file.read_text(encoding="utf-8")
    assert github.LOGIN_ENV not in os.environ
    assert github.read_env_value(github.LOGIN_ENV) == ""


def test_read_env_value_falls_back_to_environ(env_file, monkeypatch):
    monkeypatch.setenv(github.REPO_ENV, "example-owner/from-environ")
    assert github.read_env_value(github.REPO_ENV) == "example-owner/from-environ"
    env_file.write_text("SEIZOU_GITHUB_ISSUES_REPO=example-owner/from-file\n", encoding="utf-8")
    assert github.read_env_value(github.REPO_ENV) == "example-owner/from-file"


@pytest.mark.skipif(sys.platform == "win32", reason="フォルダの権限は Windows では見ない")
def test_write_env_value_refuses_unwritable_folder(tmp_path):
    folder = tmp_path / "locked"
    folder.mkdir()
    folder.chmod(0o500)
    try:
        with pytest.raises(github.GithubError) as caught:
            github.write_env_value(github.TOKEN_ENV, TOKEN, path=folder / ".env")
    finally:
        folder.chmod(0o700)
    assert "seizou github-token" in str(caught.value)
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize("token", ["", "abc", "ghp_ test", " ghp_test0000fake", None, 123])
def test_check_token_refuses_bad_shapes(token):
    with pytest.raises(github.GithubError, match="トークンの形が違います"):
        github.check_token(token)


def test_check_token_and_repo_pass_good_shapes():
    assert github.check_token(TOKEN) == TOKEN
    assert github.check_token("github_pat_test0000fake") == "github_pat_test0000fake"
    assert github.check_repo(" example-owner/example.repo_1 ") == "example-owner/example.repo_1"
    for repo in ("example-owner", "a/b/c", "owner/na me", "https://github.com/a/b", ""):
        with pytest.raises(github.GithubError):
            github.check_repo(repo)


def test_api_sends_headers_and_timeout(fake):
    assert github.whoami(TOKEN) == "example-feedback-bot"
    method, url, kwargs = fake.calls[-1]
    assert (method, url) == ("GET", "https://api.github.com/user")
    assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert kwargs["headers"]["Accept"] == "application/vnd.github+json"
    assert kwargs["headers"]["X-GitHub-Api-Version"] == "2022-11-28"
    assert kwargs["timeout"] == 10

    fake.issues[3] = Answer(200, {"state": "open", "closed_at": None, "html_url": "x"})
    assert github.issue_state("example-owner/example-repo", 3)["state"] == "open"
    assert "Authorization" not in fake.calls[-1][2]["headers"]


@pytest.mark.parametrize(
    ("status", "words"),
    [(401, "トークンが違う"), (403, "権限がありません"), (404, "見つかりません"), (422, "受け付けませんでした"), (500, "HTTP 500")],
)
def test_api_errors_are_japanese_without_token(fake, status, words):
    fake.user = Answer(status, {"message": "x"})
    with pytest.raises(github.GithubError) as caught:
        github.whoami(TOKEN)
    assert words in str(caught.value)
    assert TOKEN not in str(caught.value)


def test_unreachable_is_japanese(monkeypatch):
    def broken(*args, **kwargs):
        raise requests.ConnectionError(f"失敗 {TOKEN}")

    monkeypatch.setattr(requests, "get", broken)
    with pytest.raises(github.GithubError) as caught:
        github.whoami(TOKEN)
    assert "つながりませんでした" in str(caught.value)
    assert TOKEN not in str(caught.value)


# ---------------------------------------------------------------- つなぐ・外す


def test_connect_writes_three_lines(manager, fake, env_file):
    result = connect(manager, fake)

    assert result == {"login": "example-feedback-bot", "repo": "example-owner/example-repo"}
    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines == [
        f"SEIZOU_GITHUB_TOKEN={TOKEN}",
        "SEIZOU_GITHUB_ISSUES_REPO=example-owner/example-repo",
        "SEIZOU_GITHUB_LOGIN=example-feedback-bot",
    ]
    if sys.platform != "win32":
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert os.environ[github.TOKEN_ENV] == TOKEN
    status = services.github_status()
    assert status == {"connected": True, "login": "example-feedback-bot", "repo": "example-owner/example-repo"}
    assert TOKEN not in str(status)


def test_connect_empty_repo_is_default(manager, fake):
    result = services.connect_github({"token": TOKEN, "repo": ""}, manager)
    assert result["repo"] == github.DEFAULT_REPO == "kishii-py/seizou"


def test_connect_refuses_non_manager(plain, no_http, env_file):
    with pytest.raises(ValueError, match="アカウント管理の担当者だけ"):
        services.connect_github({"token": TOKEN, "repo": ""}, plain)
    assert not env_file.exists()


def test_connect_refuses_bad_token_without_http(manager, no_http, env_file):
    with pytest.raises(ValueError, match="トークンの形が違います"):
        services.connect_github({"token": "not-a-token", "repo": ""}, manager)
    with pytest.raises(ValueError, match="owner/name"):
        services.connect_github({"token": TOKEN, "repo": "only-owner"}, manager)
    assert not env_file.exists()


def test_connect_401_writes_nothing(manager, fake, env_file):
    fake.user = Answer(401, {"message": "Bad credentials"})
    with pytest.raises(ValueError) as caught:
        connect(manager, fake)
    assert "トークンが違う" in str(caught.value)
    assert TOKEN not in str(caught.value)
    assert not env_file.exists()
    assert github.TOKEN_ENV not in os.environ


def test_disconnect_removes_three_lines(manager, plain, fake, env_file):
    env_file.write_text("SECRET_KEY=plain-words\n", encoding="utf-8")
    connect(manager, fake)
    with pytest.raises(ValueError, match="アカウント管理の担当者だけ"):
        services.disconnect_github(plain)

    services.disconnect_github(manager)

    assert env_file.read_text(encoding="utf-8") == "SECRET_KEY=plain-words\n"
    assert not any(key in os.environ for key in KEYS)
    assert services.github_status() == {"connected": False, "login": "", "repo": github.DEFAULT_REPO}


# ---------------------------------------------------------------- 送る


def test_send_confirmed_creates_issue_and_marks_sent(manager, plain, fake):
    connect(manager, fake)
    row = make(plain, status="confirmed")

    url = services.send_to_github(row, manager)

    assert url == ISSUE_URL
    method, api, kwargs = fake.calls[-1]
    assert (method, api) == ("POST", "https://api.github.com/repos/example-owner/example-repo/issues")
    body = kwargs["json"]
    assert body["title"] == "受注の一覧で並びが戻る"
    assert body["body"] == services.export_markdown(row)
    assert body["labels"] == ["from-seizou", "不具合"]
    assert TOKEN not in str(body)
    row.refresh_from_db()
    assert (row.status, row.sent_url) == ("sent", ISSUE_URL)


def test_send_refuses_draft_without_http(manager, plain, fake, monkeypatch):
    connect(manager, fake)
    row = make(plain)
    calls = len(fake.calls)
    with pytest.raises(ValueError, match="先に「確認済みにする」を押してください。"):
        services.send_to_github(row, manager)
    assert len(fake.calls) == calls
    row.refresh_from_db()
    assert row.status == "draft"


def test_send_refuses_without_connection_and_non_manager(manager, plain, no_http):
    row = make(plain, status="confirmed")
    with pytest.raises(ValueError, match="GitHub とまだつないでいません"):
        services.send_to_github(row, manager)
    with pytest.raises(ValueError, match="アカウント管理の担当者だけ"):
        services.send_to_github(row, plain)
    row.refresh_from_db()
    assert row.status == "confirmed"


def test_send_422_keeps_status(manager, plain, fake):
    connect(manager, fake)
    fake.created = Answer(422, {"message": "Validation Failed"})
    row = make(plain, status="confirmed")
    with pytest.raises(ValueError) as caught:
        services.send_to_github(row, manager)
    assert "受け付けませんでした" in str(caught.value)
    assert TOKEN not in str(caught.value)
    row.refresh_from_db()
    assert (row.status, row.sent_url) == ("confirmed", "")


# ---------------------------------------------------------------- 取り込む


def test_sync_marks_closed_issue_fixed(manager, plain, fake):
    closed = make(plain, "閉じた件", status="sent", sent_url=ISSUE_URL)
    still = make(plain, "開いた件", status="sent", sent_url="https://github.com/example-owner/example-repo/issues/8")
    other = make(plain, "ほかの場所", status="sent", sent_url="https://example.com/issues/9")
    fake.issues[7] = Answer(200, {"state": "closed", "closed_at": "2026-11-06T03:00:00Z", "html_url": ISSUE_URL})
    fake.issues[8] = Answer(200, {"state": "open", "closed_at": None, "html_url": "x"})

    counts = services.sync_github(manager)

    assert counts == {"見た": 2, "直した": 1, "見送った": 0, "版待ち": 0, "版の印なし": 0, "読めなかった": 0, "返事": 0}
    closed.refresh_from_db()
    still.refresh_from_db()
    other.refresh_from_db()
    assert closed.status == "fixed"
    assert closed.fixed_note == "GitHub の Issue #7 (閉じた日 2026-11-06)"
    assert closed.fixed_at is not None and closed.fixed_seen_at is None
    assert still.status == "sent" and other.status == "sent"
    assert all("example.com" not in url for _, url, _ in fake.calls)


def test_sync_counts_unreadable_and_refuses_non_manager(manager, plain, fake):
    make(plain, status="sent", sent_url="https://github.com/example-owner/example-repo/issues/99")
    with pytest.raises(ValueError, match="アカウント管理の担当者だけ"):
        services.sync_github(plain)
    assert services.sync_github(manager) == {"見た": 1, "直した": 0, "見送った": 0, "版待ち": 0, "版の印なし": 0, "読めなかった": 1, "返事": 0}


# ---------------------------------------------------------------- 画面とボタン


def html_of(response) -> str:
    return response.content.decode()


def test_setup_screen_connects_and_never_shows_token(client, manager, fake, env_file):
    client.force_login(manager)
    html = html_of(client.get("/feedback/github/"))
    assert "まだつないでいません" in html
    assert 'type="password"' in html and 'name="token"' in html
    assert 'value="kishii-py/seizou"' in html
    assert "Generate new token (classic)" in html and "public_repo" in html
    assert 'name="next" value="/feedback/github/"' in html

    response = client.post("/feedback/github/", {"token": TOKEN, "repo": "example-owner/example-repo", "next": "/feedback/github/"})

    assert response.status_code == 302 and response["Location"] == "/feedback/github/"
    assert TOKEN in env_file.read_text(encoding="utf-8")
    html = html_of(client.get("/feedback/github/"))
    assert "example-feedback-bot の口座としてつながっています" in html
    assert "example-owner/example-repo" in html
    assert 'action="/feedback/github/disconnect/"' in html
    assert TOKEN not in html


def test_setup_screen_bad_token_is_red_and_hidden(client, manager, fake, env_file):
    client.force_login(manager)
    fake.user = Answer(401, {"message": "Bad credentials"})
    response = client.post("/feedback/github/", {"token": TOKEN, "repo": "", "next": "/feedback/github/"})
    html = html_of(response)
    assert response.status_code == 200
    assert "トークンが違う" in html
    assert TOKEN not in html
    assert not env_file.exists()


def test_setup_screen_is_closed_to_non_manager(client, plain, no_http, env_file):
    client.force_login(plain)
    html = html_of(client.get("/feedback/github/"))
    assert "この画面は開けません" in html
    assert 'name="token"' not in html and "取得の手順" not in html

    response = client.post("/feedback/github/", {"token": TOKEN, "repo": "", "next": "/feedback/github/"})
    assert response.status_code == 200
    assert TOKEN not in html_of(response)
    assert not env_file.exists()

    client.post("/feedback/github/disconnect/")
    client.post("/feedback/github/sync/")


def test_disconnect_endpoint(client, manager, fake, env_file):
    connect(manager, fake)
    client.force_login(manager)
    response = client.post("/feedback/github/disconnect/")
    assert response.status_code == 302 and response["Location"] == "/feedback/github/"
    assert TOKEN not in env_file.read_text(encoding="utf-8")
    assert "GitHub から外しました" in html_of(client.get("/feedback/github/"))
    assert client.get("/feedback/github/disconnect/").status_code == 405


def test_detail_buttons_depend_on_manager_connection_and_status(client, manager, plain, fake):
    row = make(plain, status="confirmed")
    detail = f"/feedback/{row.pk}/"

    client.force_login(manager)
    html = html_of(client.get(detail))
    assert "GitHub に送る</button>" not in html
    assert 'id="feedback-github-setup"' in html and 'href="/feedback/github/"' in html

    connect(manager, fake)
    html = html_of(client.get(detail))
    assert "GitHub に送る</button>" in html and f'action="/feedback/{row.pk}/github/"' in html
    assert html.index("GitHub に送る</button>") < html.index("送ったにする</button>")
    assert 'id="feedback-github-setup"' not in html

    draft = make(plain, "下書きのまま")
    assert "GitHub に送る</button>" not in html_of(client.get(f"/feedback/{draft.pk}/"))

    client.force_login(plain)
    html = html_of(client.get(detail))
    assert "GitHub に送る" not in html and 'href="/feedback/github/"' not in html


def test_send_endpoint(client, manager, plain, fake):
    connect(manager, fake)
    row = make(plain, status="confirmed")
    detail = f"/feedback/{row.pk}/"

    client.force_login(plain)
    response = client.post(f"/feedback/{row.pk}/github/")
    assert response.status_code == 302 and response["Location"] == detail
    row.refresh_from_db()
    assert row.status == "confirmed"

    client.force_login(manager)
    response = client.post(f"/feedback/{row.pk}/github/")
    assert response.status_code == 302 and response["Location"] == detail
    row.refresh_from_db()
    assert (row.status, row.sent_url) == ("sent", ISSUE_URL)
    html = html_of(client.get(detail))
    assert f"GitHub に送りました: {ISSUE_URL}" in html
    assert "GitHub に送る</button>" not in html
    assert client.get(f"/feedback/{row.pk}/github/").status_code == 405


def test_list_buttons_and_sync_endpoint(client, manager, plain, fake):
    client.force_login(plain)
    html = html_of(client.get("/feedback/"))
    assert "GitHub の設定" not in html and "GitHub の状態を取り込む" not in html
    assert "GitHub の Issue を探す" in html and 'name="issue_q"' in html

    client.force_login(manager)
    html = html_of(client.get("/feedback/"))
    assert "GitHub の設定" in html and "GitHub の状態を取り込む" not in html

    connect(manager, fake)
    make(plain, status="sent", sent_url=ISSUE_URL)
    fake.issues[7] = Answer(200, {"state": "closed", "closed_at": "2026-11-06T03:00:00Z", "html_url": ISSUE_URL})
    html = html_of(client.get("/feedback/"))
    assert "GitHub の状態を取り込む" in html and 'action="/feedback/github/sync/"' in html

    response = client.post("/feedback/github/sync/")
    assert response.status_code == 302 and response["Location"] == "/feedback/"
    assert "GitHub を見ました: 見た 1 件・直した 1 件・見送った 0 件・版待ち 0 件・版の印なし 0 件・読めなかった 0 件" in html_of(client.get("/feedback/"))


def test_form_screen_links_to_issue_search(client, plain):
    client.force_login(plain)
    html = html_of(client.get("/feedback/new/"))
    assert 'href="/feedback/?tab=issues#issues"' in html
    assert "似た困りごとが GitHub に出ていないか探す" in html


# ---------------------------------------------------------------- 皆の Issue を探す


def issue(number, title, body="", state="open", updated="2026-11-01T00:00:00Z", plus=0, **extra):
    return {
        "number": number,
        "title": title,
        "body": body,
        "state": state,
        "html_url": f"https://github.com/kishii-py/seizou/issues/{number}",
        "updated_at": updated,
        "comments": 0,
        "reactions": {"+1": plus},
        **extra,
    }


@pytest.fixture
def listing(fake):
    fake.listing = Answer(
        200,
        [
            issue(1, "受注の一覧で並びが戻る", "並び替えた後に戻ると元に戻る", updated="2026-11-02T00:00:00Z", plus=3),
            issue(2, "出荷の画面が重い", "受注を開くと待つ", state="closed", updated="2026-11-05T00:00:00Z"),
            issue(3, "受注の一覧の CSV", "列を足したい", updated="2026-11-04T00:00:00Z", pull_request={"url": "x"}),
            issue(4, "Search words", "Upper Case", updated="2026-11-03T00:00:00Z"),
        ],
    )
    return fake


def test_search_issues_filters_by_all_words_and_drops_pull_requests(listing):
    result = services.search_issues("受注 戻る")
    assert result["error"] == ""
    assert [row["number"] for row in result["issues"]] == [1]
    assert result["issues"][0]["thumbs_up"] == 3

    everything = services.search_issues("")
    assert [row["number"] for row in everything["issues"]] == [2, 4, 1]
    assert [row["number"] for row in services.search_issues("受注")["issues"]] == [2, 1]
    assert [row["number"] for row in services.search_issues("upper")["issues"]] == [4]


def test_search_issues_is_cached_and_needs_no_token(listing):
    services.search_issues("受注")
    gets = [call for call in listing.calls if call[0] == "GET"]
    assert len(gets) == 1
    method, url, kwargs = gets[0]
    assert url == "https://api.github.com/repos/kishii-py/seizou/issues"
    assert kwargs["params"] == {"state": "all", "per_page": 100, "sort": "updated"}
    assert "Authorization" not in kwargs["headers"]

    services.search_issues("出荷")
    assert len([call for call in listing.calls if call[0] == "GET"]) == 1


def test_search_issues_uses_token_when_connected(manager, listing):
    connect(manager, listing)
    services.search_issues("")
    method, url, kwargs = listing.calls[-1]
    assert url == "https://api.github.com/repos/example-owner/example-repo/issues"
    assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_search_issues_error_does_not_stop(fake):
    fake.listing = Answer(403, {"message": "rate limit"})
    result = services.search_issues("受注")
    assert result["issues"] == []
    assert "GitHub の Issue を読めませんでした" in result["error"]
    fake.listing = Answer(200, [issue(1, "受注")])
    assert [row["number"] for row in services.search_issues("受注")["issues"]] == [1]


def test_list_screen_searches_only_when_asked(client, plain, listing):
    client.force_login(plain)
    html = html_of(client.get("/feedback/"))
    assert not listing.calls
    assert "issues-form" in html

    html = html_of(client.get("/feedback/", {"issue_q": "受注 戻る"}))
    assert 'href="https://github.com/kishii-py/seizou/issues/1"' in html
    assert "受注の一覧で並びが戻る" in html and "出荷の画面が重い" not in html
    assert 'value="受注 戻る"' in html
    assert "badge badge-soft badge-success" in html

    html = html_of(client.get("/feedback/", {"issue_q": ""}))
    assert "badge badge-ghost" in html and "受注の一覧の CSV" not in html


def test_list_screen_opens_when_github_is_unreadable(client, plain, monkeypatch):
    def broken(*args, **kwargs):
        raise requests.ConnectionError("つながらない")

    monkeypatch.setattr(requests, "get", broken)
    client.force_login(plain)
    response = client.get("/feedback/", {"issue_q": "受注"})
    assert response.status_code == 200
    assert "GitHub の Issue を読めませんでした" in html_of(response)


# ---------------------------------------------------------------- 管理の命令


def test_sync_command_needs_no_manager(plain, fake):
    from io import StringIO

    from django.core.management import call_command

    row = make(plain, status="sent", sent_url=ISSUE_URL)
    fake.issues[7] = Answer(200, {"state": "closed", "closed_at": "2026-11-05T15:30:00Z", "html_url": ISSUE_URL})
    out = StringIO()

    call_command("seizou_feedback_sync", stdout=out)

    assert out.getvalue().strip() == "GitHub を見ました: 見た 1 件・直した 1 件・見送った 0 件・版待ち 0 件・版の印なし 0 件・読めなかった 0 件・返事 0 件"
    row.refresh_from_db()
    assert row.status == "fixed"
    assert row.fixed_note == "GitHub の Issue #7 (閉じた日 2026-11-06)"


# ---------------------------------------------------------------- 端末の seizou github-token


def test_cli_github_token_writes_env_without_showing_token(tmp_path, fake, monkeypatch, capsys):
    from seizou import cli

    site = tmp_path / "site"
    site.mkdir()
    (site / "manage.py").write_text("", encoding="utf-8")
    (site / "answers.yaml").write_text("id: example\nname: 作り物の会社\nport: 8009\n", encoding="utf-8")
    (site / ".env").write_text("SECRET_KEY=plain-words\n", encoding="utf-8")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": TOKEN + "\n")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    code = cli.main(["github-token", str(site)])

    out, err = capsys.readouterr()
    assert code == 0, err
    assert out.strip() == "example-feedback-bot の口座としてつながりました。"
    assert TOKEN not in out + err
    assert (site / ".env").read_text(encoding="utf-8").splitlines() == [
        "SECRET_KEY=plain-words",
        f"SEIZOU_GITHUB_TOKEN={TOKEN}",
        "SEIZOU_GITHUB_ISSUES_REPO=kishii-py/seizou",
        "SEIZOU_GITHUB_LOGIN=example-feedback-bot",
    ]
    if sys.platform != "win32":
        assert stat.S_IMODE((site / ".env").stat().st_mode) == 0o600


def test_cli_github_token_refuses_bad_token_and_non_site(tmp_path, no_http, monkeypatch, capsys):
    from seizou import cli

    assert cli.main(["github-token", str(tmp_path)]) == 2
    assert "置き場ではありません" in capsys.readouterr().err

    (tmp_path / "manage.py").write_text("", encoding="utf-8")
    (tmp_path / "answers.yaml").write_text("id: example\n", encoding="utf-8")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "not-a-token")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert cli.main(["github-token", str(tmp_path)]) == 2
    out, err = capsys.readouterr()
    assert "トークンの形が違います" in err
    assert not (tmp_path / ".env").exists()


# ---------------------------------------------------------------- GitHub でログインしてつなぐ (依頼書 N320)

SETUP = "/feedback/github/"
START = "/feedback/github/device/"
POLL = "/feedback/github/device/poll/"
CANCEL = "/feedback/github/device/cancel/"
HTMX = {"HTTP_HX_REQUEST": "true"}


class Session(dict):
    """利用者の session の代わり (services の試験用。modified だけを持つ)。"""

    modified = False


@pytest.fixture
def client_id(env_file, monkeypatch):
    """作品の Client ID を置き場の .env に置く (os.environ と既定には頼らない)。"""
    monkeypatch.delenv(github.CLIENT_ID_ENV, raising=False)
    monkeypatch.setattr(github, "DEFAULT_CLIENT_ID", "")
    env_file.write_text(f"{github.CLIENT_ID_ENV}={CLIENT_ID}\n", encoding="utf-8")
    return CLIENT_ID


@pytest.fixture
def no_client_id(monkeypatch):
    monkeypatch.delenv(github.CLIENT_ID_ENV, raising=False)
    monkeypatch.setattr(github, "DEFAULT_CLIENT_ID", "")


def device_calls(fake, url):
    return [call for call in fake.calls if call[1] == url]


def start(client, **extra):
    return client.post(START, {"repo": "example-owner/example-repo", **extra})


def test_check_token_passes_oauth_app_token():
    assert github.check_token(OAUTH_TOKEN) == OAUTH_TOKEN


def test_client_id_reads_env_then_default(env_file, no_client_id, monkeypatch):
    assert github.client_id() == ""
    monkeypatch.setattr(github, "DEFAULT_CLIENT_ID", "client-id-default-fake")
    assert github.client_id() == "client-id-default-fake"
    env_file.write_text(f"{github.CLIENT_ID_ENV}={CLIENT_ID}\n", encoding="utf-8")
    assert github.client_id() == CLIENT_ID


def test_device_start_shows_code_and_link_without_device_code(client, manager, fake, client_id):
    client.force_login(manager)
    html = html_of(client.get(SETUP))
    assert "GitHub でログインしてつなぐ</button>" in html and f'action="{START}"' in html
    assert 'id="id_device_repo"' in html and 'name="private"' in html
    assert html.index("GitHub でログインしてつなぐ</button>") < html.index("トークンを貼ってつなぐ (別の方法)")

    response = start(client)

    assert response.status_code == 302 and response["Location"] == SETUP
    method, url, kwargs = fake.calls[-1]
    assert (method, url) == ("POST", "https://github.com/login/device/code")
    assert kwargs["data"] == {"client_id": CLIENT_ID, "scope": "public_repo"}
    assert kwargs["headers"] == {"Accept": "application/json"}
    assert kwargs["timeout"] == 10
    stored = client.session[services.DEVICE_KEY]
    assert stored["device_code"] == DEVICE_CODE and stored["repo"] == "example-owner/example-repo"
    assert stored["scope"] == "public_repo" and stored["interval"] == 5

    html = html_of(client.get(SETUP))
    assert USER_CODE in html
    assert 'href="https://github.com/login/device"' in html and 'target="_blank"' in html
    assert f'hx-post="{POLL}"' in html and 'hx-trigger="every 5s"' in html
    assert f'action="{CANCEL}"' in html
    assert "GitHub でログインしてつなぐ</button>" not in html
    assert DEVICE_CODE not in html


def test_device_start_private_asks_repo_scope(client, manager, fake, client_id):
    client.force_login(manager)
    start(client, private="on")
    assert device_calls(fake, github.DEVICE_CODE_URL)[-1][2]["data"]["scope"] == "repo"
    assert client.session[services.DEVICE_KEY]["scope"] == "repo"


def test_device_start_without_client_id_is_red_without_http(client, manager, no_http, no_client_id):
    client.force_login(manager)
    html = html_of(client.get(SETUP))
    assert "この置き場には作品の Client ID が入っていないので" in html
    assert "GitHub でログインしてつなぐ</button>" not in html

    response = start(client)

    assert response.status_code == 302 and response["Location"] == SETUP
    assert services.DEVICE_KEY not in client.session
    assert "Client ID が入っていません" in html_of(client.get(SETUP))


def test_device_start_refuses_non_manager_without_http(client, plain, no_http, client_id):
    client.force_login(plain)
    start(client)
    assert services.DEVICE_KEY not in client.session
    assert "アカウント管理の担当者だけ" in html_of(client.get("/feedback/"))
    with pytest.raises(ValueError, match="アカウント管理の担当者だけ"):
        services.start_github_device(Session(), {"repo": ""}, plain)


def test_device_start_reports_disabled_device_flow(client, manager, fake, client_id):
    fake.device = Answer(400, {"error": "device_flow_disabled"})
    client.force_login(manager)
    start(client)
    assert services.DEVICE_KEY not in client.session
    assert "作品の Client ID が GitHub の OAuth App と合いません" in html_of(client.get(SETUP))


def test_device_start_service_returns_no_device_code(manager, fake, client_id):
    session = Session()
    result = services.start_github_device(session, {"repo": "", "private": False}, manager)
    assert set(result) == {"user_code", "verification_uri", "interval", "expires_at"}
    assert DEVICE_CODE not in str(result)
    assert session.modified and session[services.DEVICE_KEY]["repo"] == github.DEFAULT_REPO
    state = services.github_device_state(session)
    assert set(state) == {"user_code", "verification_uri", "interval", "expires_at", "repo"}
    assert DEVICE_CODE not in str(state)


def test_device_poll_pending_keeps_card(client, manager, fake, client_id):
    client.force_login(manager)
    start(client)

    response = client.post(POLL, **HTMX)

    html = html_of(response)
    assert response.status_code == 200
    assert USER_CODE in html and 'hx-trigger="every 5s"' in html
    assert DEVICE_CODE not in html
    method, url, kwargs = fake.calls[-1]
    assert (method, url) == ("POST", "https://github.com/login/oauth/access_token")
    assert kwargs["data"] == {
        "client_id": CLIENT_ID,
        "device_code": DEVICE_CODE,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }
    assert kwargs["headers"] == {"Accept": "application/json"}
    assert services.DEVICE_KEY in client.session


def test_device_poll_slow_down_widens_interval(client, manager, fake, client_id):
    client.force_login(manager)
    start(client)

    fake.token = Answer(200, {"error": "slow_down", "interval": 10})
    html = html_of(client.post(POLL, **HTMX))
    assert client.session[services.DEVICE_KEY]["interval"] == 10
    assert 'hx-trigger="every 10s"' in html

    fake.token = Answer(200, {"error": "slow_down"})
    html = html_of(client.post(POLL, **HTMX))
    assert client.session[services.DEVICE_KEY]["interval"] == 15
    assert 'hx-trigger="every 15s"' in html


def test_device_poll_ok_writes_env_and_redirects(client, manager, fake, client_id, env_file):
    client.force_login(manager)
    start(client)
    fake.token = Answer(200, {"access_token": OAUTH_TOKEN, "token_type": "bearer", "scope": "public_repo"})

    response = client.post(POLL, **HTMX)

    assert response.status_code == 200
    assert response["HX-Redirect"] == SETUP
    assert response.content == b""
    assert OAUTH_TOKEN not in str(response.headers)
    assert env_file.read_text(encoding="utf-8").splitlines() == [
        f"{github.CLIENT_ID_ENV}={CLIENT_ID}",
        f"SEIZOU_GITHUB_TOKEN={OAUTH_TOKEN}",
        "SEIZOU_GITHUB_ISSUES_REPO=example-owner/example-repo",
        "SEIZOU_GITHUB_LOGIN=example-feedback-bot",
    ]
    assert fake.calls[-1][0] == "GET" and fake.calls[-1][2]["headers"]["Authorization"] == f"Bearer {OAUTH_TOKEN}"
    assert services.DEVICE_KEY not in client.session

    html = html_of(client.get(SETUP))
    assert "example-feedback-bot の口座としてつながりました。" in html
    assert "example-feedback-bot の口座としてつながっています" in html
    assert 'id="github-device"' not in html
    assert OAUTH_TOKEN not in html


@pytest.mark.parametrize(
    ("answer", "words"),
    [
        ({"error": "expired_token"}, "符号の期限が切れました。"),
        ({"error": "access_denied"}, "GitHub で承認されませんでした。"),
        ({"error": "incorrect_device_code"}, "符号が GitHub と合いません。"),
        ({"error": "incorrect_client_credentials"}, "作品の Client ID が GitHub の OAuth App と合いません。"),
    ],
)
def test_device_poll_end_states_drop_session_and_stop(client, manager, fake, client_id, answer, words):
    client.force_login(manager)
    start(client)
    fake.token = Answer(200, answer)

    html = html_of(client.post(POLL, **HTMX))

    assert words in html and "text-error" in html
    assert "hx-trigger" not in html and USER_CODE not in html
    assert services.DEVICE_KEY not in client.session
    assert DEVICE_CODE not in html


def test_device_poll_refuses_non_manager_without_http(client, plain, no_http):
    client.force_login(plain)
    response = client.post(POLL, **HTMX)
    html = html_of(response)
    assert response.status_code == 200
    assert "アカウント管理の担当者だけ" in html and "hx-trigger" not in html


def test_device_poll_needs_htmx(client, manager, no_http):
    client.force_login(manager)
    assert client.post(POLL).status_code == 400
    assert client.get(POLL, **HTMX).status_code == 405


def test_device_poll_without_wait_reloads_screen(client, manager, no_http):
    client.force_login(manager)
    response = client.post(POLL, **HTMX)
    assert response.status_code == 200 and response["HX-Redirect"] == SETUP


def test_device_cancel_drops_session(client, manager, plain, fake, client_id):
    client.force_login(manager)
    start(client)
    calls = len(fake.calls)

    response = client.post(CANCEL)

    assert response.status_code == 302 and response["Location"] == SETUP
    assert services.DEVICE_KEY not in client.session
    assert len(fake.calls) == calls
    html = html_of(client.get(SETUP))
    assert USER_CODE not in html and "GitHub でログインしてつなぐ</button>" in html
    with pytest.raises(ValueError, match="アカウント管理の担当者だけ"):
        services.cancel_github_device(Session(), plain)


def test_device_wait_expires(manager, no_http, client_id):
    session = Session({services.DEVICE_KEY: {
        "device_code": DEVICE_CODE,
        "user_code": USER_CODE,
        "verification_uri": "https://github.com/login/device",
        "interval": 5,
        "expires_at": 0,
        "repo": github.DEFAULT_REPO,
        "scope": "public_repo",
    }})
    assert services.poll_github_device(Session(session), manager) == {"state": "expired"}
    assert services.github_device_state(session) is None
    assert services.DEVICE_KEY not in session and session.modified


def test_device_screen_hides_section_when_connected(client, manager, fake, client_id):
    connect(manager, fake)
    client.force_login(manager)
    html = html_of(client.get(SETUP))
    assert 'id="github-device"' not in html
    assert "Authorized OAuth Apps" in html and "Revoke" in html
# ---------------------------------------------------------------- 閉じた理由と直る版 (依頼書 N322。決め 11-07 09:0x〜09:4x)

#: 取り込みの返す物の鍵 (順もこれ)
SYNC_KEYS = ["見た", "直した", "見送った", "版待ち", "版の印なし", "読めなかった", "返事"]


def closed_issue(reason, milestone=None, closed_at="2026-11-06T03:00:00Z", url=ISSUE_URL):
    """閉じた Issue の GitHub の返事 (state_reason と milestone つき)。milestone は名前の字か None。"""
    return Answer(
        200,
        {
            "state": "closed",
            "state_reason": reason,
            "closed_at": closed_at,
            "html_url": url,
            "milestone": {"title": milestone, "number": 3} if milestone else None,
        },
    )


def sync_counts(**change):
    """取り込みの返す物の期待 (書かない鍵は 0)。"""
    return {key: change.get(key, 0) for key in SYNC_KEYS}


@pytest.fixture
def version(monkeypatch):
    """会社が入れている版を差し替える (既定 0.1.0)。返す関数で版を変える。"""
    from seizou.core import manual

    current = {"value": "0.1.0"}
    monkeypatch.setattr(manual, "package_version", lambda: current["value"])

    def set_version(value):
        current["value"] = value

    return set_version


def test_issue_state_returns_reason_and_milestone(fake):
    fake.issues[7] = closed_issue("not_planned", milestone="0.1.1")
    fake.issues[8] = Answer(200, {"state": "open", "closed_at": None, "html_url": "x"})

    assert github.issue_state("example-owner/example-repo", 7) == {
        "state": "closed",
        "closed_at": "2026-11-06T03:00:00Z",
        "html_url": ISSUE_URL,
        "state_reason": "not_planned",
        "milestone": "0.1.1",
    }
    state = github.issue_state("example-owner/example-repo", 8)
    assert (state["state_reason"], state["milestone"]) == ("", "")


@pytest.mark.parametrize(("reason", "words"), [("not_planned", "対応しない"), ("duplicate", "重複")])
def test_sync_not_planned_and_duplicate_are_dismissed(manager, plain, fake, version, reason, words):
    row = make(plain, "見送られる件", status="sent", sent_url=ISSUE_URL)
    fake.issues[7] = closed_issue(reason, milestone="0.1.1")

    counts = services.sync_github(manager)

    assert counts == sync_counts(見た=1, 見送った=1)
    assert list(counts) == SYNC_KEYS
    row.refresh_from_db()
    assert row.status == "dismissed"
    assert row.dismissed_note == f"GitHub の Issue #7 (閉じた日 2026-11-06。理由 {words})"
    assert row.dismissed_at is not None and row.dismissed_seen_at is None
    assert row.fixed_at is None and row.fixed_note == "" and row.fix_version == ""
    assert services.unseen_fixed(plain) == []
    assert [r.pk for r in services.unseen_dismissed(plain)] == [row.pk]

    # 見送った件は送ったでないので、次の取り込みでは見ない
    assert services.sync_github(manager) == sync_counts()


def test_sync_completed_waits_for_installed_version(manager, plain, fake, version):
    row = make(plain, "直る版を待つ件", status="sent", sent_url=ISSUE_URL)
    fake.issues[7] = closed_issue("completed", milestone="0.1.1")

    assert services.sync_github(manager) == sync_counts(見た=1, 版待ち=1)
    row.refresh_from_db()
    assert row.status == "sent"
    assert row.fix_version == "0.1.1"
    assert row.fixed_at is None and row.dismissed_at is None
    assert services.unseen_fixed(plain) == [] and services.unseen_dismissed(plain) == []

    # 会社がその版を入れた後の取り込みで直したになる
    version("0.1.1")
    assert services.sync_github(manager) == sync_counts(見た=1, 直した=1)
    row.refresh_from_db()
    assert row.status == "fixed"
    assert row.fixed_note == "GitHub の Issue #7 (閉じた日 2026-11-06。直る版 0.1.1)"
    assert [r.pk for r in services.unseen_fixed(plain)] == [row.pk]


@pytest.mark.parametrize("installed", ["0.1.1", "0.2.0"])
def test_sync_completed_with_installed_version_is_fixed(manager, plain, fake, version, installed):
    row = make(plain, "直った件", status="sent", sent_url=ISSUE_URL)
    fake.issues[7] = closed_issue("completed", milestone="0.1.1")
    version(installed)

    assert services.sync_github(manager) == sync_counts(見た=1, 直した=1)
    row.refresh_from_db()
    assert row.status == "fixed"
    assert row.fixed_note == "GitHub の Issue #7 (閉じた日 2026-11-06。直る版 0.1.1)"
    assert row.fixed_at is not None and row.dismissed_at is None


def test_sync_completed_without_milestone_stays_sent(manager, plain, fake, version):
    row = make(plain, "版の印の無い件", status="sent", sent_url=ISSUE_URL)
    fake.issues[7] = closed_issue("completed")

    assert services.sync_github(manager) == sync_counts(見た=1, 版の印なし=1)
    row.refresh_from_db()
    assert row.status == "sent"
    assert row.fix_version == ""
    assert row.fixed_at is None and row.dismissed_at is None


def test_sync_without_reason_is_fixed_as_before(manager, plain, fake, version):
    row = make(plain, "理由の無い閉じ方の件", status="sent", sent_url=ISSUE_URL)
    fake.issues[7] = closed_issue(None, milestone="9.9.9")

    assert services.sync_github(manager) == sync_counts(見た=1, 直した=1)
    row.refresh_from_db()
    assert row.status == "fixed"
    assert row.fixed_note == "GitHub の Issue #7 (閉じた日 2026-11-06)"


@pytest.mark.parametrize(
    ("current", "needed", "expected"),
    [
        ("0.1.1", "0.1.1", True),
        ("0.1.0", "0.1.1", False),
        ("0.2.0", "0.1.9", True),
        ("", "0.1.1", False),
        ("0.1.1", "", True),
        ("0.1.1rc1", "0.1.1", True),
    ],
)
def test_version_at_least(current, needed, expected):
    assert services.version_at_least(current, needed) is expected


def test_write_dismissed_refuses_fixed_and_dismissed(manager, plain):
    fixed = make(plain, "直した件", status="sent", sent_url=ISSUE_URL)
    services.mark_fixed(fixed, "", manager)
    with pytest.raises(ValueError, match="既に直した"):
        services._write_dismissed(fixed, "GitHub の Issue #7")

    row = make(plain, "見送る件", status="sent", sent_url=ISSUE_URL)
    services._write_dismissed(row, "GitHub の Issue #7")
    with pytest.raises(ValueError, match="既に見送った"):
        services._write_dismissed(row, "GitHub の Issue #7")


def six_kinds(plain, fake):
    """取り込みの 6 つの数が全部 1 以上になる送った件 (#7 直した・#8 見送った・#9 版待ち・#10 版の印なし・#11 読めなかった・#12 開いている)。"""
    base = "https://github.com/example-owner/example-repo/issues/"
    for number in range(7, 13):
        make(plain, f"件 {number}", status="sent", sent_url=f"{base}{number}")
    fake.issues[7] = closed_issue(None, url=f"{base}7")
    fake.issues[8] = closed_issue("not_planned", url=f"{base}8")
    fake.issues[9] = closed_issue("completed", milestone="0.1.1", url=f"{base}9")
    fake.issues[10] = closed_issue("completed", url=f"{base}10")
    fake.issues[12] = Answer(200, {"state": "open", "closed_at": None, "html_url": f"{base}12"})


SIX_LINE = "GitHub を見ました: 見た 6 件・直した 1 件・見送った 1 件・版待ち 1 件・版の印なし 1 件・読めなかった 1 件・返事 0 件"


def test_sync_command_writes_six_numbers(plain, fake, version):
    from io import StringIO

    from django.core.management import call_command

    six_kinds(plain, fake)
    out = StringIO()

    call_command("seizou_feedback_sync", stdout=out)

    assert out.getvalue().strip() == SIX_LINE
    assert Feedback.objects.filter(status="dismissed").count() == 1
    assert Feedback.objects.filter(status="sent", fix_version="0.1.1").count() == 1


def test_sync_endpoint_shows_six_numbers(client, manager, plain, fake, version):
    connect(manager, fake)
    six_kinds(plain, fake)
    client.force_login(manager)

    response = client.post("/feedback/github/sync/")

    assert response.status_code == 302 and response["Location"] == "/feedback/"
    assert SIX_LINE in html_of(client.get("/feedback/"))


# ---------------------------------------------------------------- Issue の返事 (依頼書 N324。決め 11-07 09:1x〜09:4x)


def comment(number, login, body, created_at="2026-11-06T04:00:00Z"):
    """GitHub の Issue の返事の 1 件 (作り物)。"""
    return {
        "id": number,
        "user": {"login": login},
        "body": body,
        "created_at": created_at,
        "updated_at": created_at,
        "html_url": f"{ISSUE_URL}#issuecomment-{number}",
    }


def comments_calls(fake):
    return [call for call in fake.calls if call[1].endswith("/comments")]


def test_list_comments_shape_and_errors(fake):
    fake.comments = Answer(200, [comment(101, "example-author", "どの画面で起きましたか"), {"id": "x"}, "x"])

    rows = github.list_comments("example-owner/example-repo", 7)

    assert rows == [
        {
            "id": 101,
            "login": "example-author",
            "body": "どの画面で起きましたか",
            "created_at": "2026-11-06T04:00:00Z",
            "html_url": f"{ISSUE_URL}#issuecomment-101",
        }
    ]
    method, url, kwargs = fake.calls[-1]
    assert (method, url) == ("GET", "https://api.github.com/repos/example-owner/example-repo/issues/7/comments")
    assert kwargs["params"] == {"per_page": 100}
    assert kwargs["timeout"] == 10
    assert "Authorization" not in kwargs["headers"]

    github.list_comments("example-owner/example-repo", 7, token=TOKEN)
    assert fake.calls[-1][2]["headers"]["Authorization"] == f"Bearer {TOKEN}"

    fake.comments = Answer(200, {"message": "並びでない"})
    with pytest.raises(github.GithubError, match="読めませんでした"):
        github.list_comments("example-owner/example-repo", 7)
    fake.comments = Answer(404, {})
    with pytest.raises(github.GithubError, match="見つかりません"):
        github.list_comments("example-owner/example-repo", 7)


def test_create_comment_posts_body_and_returns_shape(fake):
    answer = github.create_comment(TOKEN, "example-owner/example-repo", 7, "受注の一覧の画面です。")

    assert answer == {
        "id": 9001,
        "html_url": f"{ISSUE_URL}#issuecomment-9001",
        "created_at": "2026-11-07T01:00:00Z",
        "login": "example-feedback-bot",
    }
    method, url, kwargs = fake.calls[-1]
    assert (method, url) == ("POST", "https://api.github.com/repos/example-owner/example-repo/issues/7/comments")
    assert kwargs["json"] == {"body": "受注の一覧の画面です。"}
    assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"

    fake.comment_created = Answer(403, {})
    with pytest.raises(github.GithubError, match="権限がありません") as caught:
        github.create_comment(TOKEN, "example-owner/example-repo", 7, "文")
    assert TOKEN not in str(caught.value)


def test_sync_imports_replies_once(manager, plain, fake):
    from datetime import UTC, datetime

    from seizou.feedback.models import FeedbackReply

    row = make(plain, "返事の来る件", status="sent", sent_url=ISSUE_URL)
    fake.issues[7] = Answer(200, {"state": "open", "closed_at": None, "html_url": ISSUE_URL})
    fake.comments = Answer(200, [comment(101, "example-author", "どの画面で起きましたか")])

    counts = services.sync_github(manager)

    assert counts["返事"] == 1 and list(counts)[-1] == "返事"
    reply = FeedbackReply.objects.get()
    assert reply.feedback_id == row.pk
    assert (reply.github_id, reply.login, reply.body) == (101, "example-author", "どの画面で起きましたか")
    assert reply.posted_at == datetime(2026, 11, 6, 4, 0, tzinfo=UTC)
    assert reply.sent_at == reply.posted_at
    assert reply.seen_at is None and reply.created_by is None

    # 同じ id は二重に入らない。新しい返事だけが入る
    fake.comments = Answer(200, [comment(101, "example-author", "どの画面で起きましたか"), comment(102, "example-author", "版を教えてください")])
    assert services.sync_github(manager)["返事"] == 1
    assert list(FeedbackReply.objects.values_list("github_id", flat=True)) == [101, 102]
    assert services.sync_github(manager)["返事"] == 0


def test_sync_reads_replies_of_fixed_and_dismissed_but_not_drafts(manager, plain, fake):
    base = "https://github.com/example-owner/example-repo/issues/"
    make(plain, "直した件", status="fixed", sent_url=f"{base}21")
    make(plain, "見送った件", status="dismissed", sent_url=f"{base}22")
    make(plain, "確認済みの件", status="confirmed", sent_url=f"{base}23")
    make(plain, "ほかの場所の件", status="sent", sent_url="https://example.com/issues/24")

    services.sync_github(manager)

    assert [url.rsplit("/", 2)[1] for _, url, _ in comments_calls(fake)] == ["21", "22"]


def test_sync_marks_company_replies_seen(manager, plain, fake):
    from seizou.feedback.models import FeedbackReply

    connect(manager, fake)  # 会社の口座 = example-feedback-bot
    make(plain, status="sent", sent_url=ISSUE_URL)
    fake.issues[7] = Answer(200, {"state": "open", "closed_at": None, "html_url": ISSUE_URL})
    fake.comments = Answer(200, [comment(201, "example-feedback-bot", "会社から"), comment(202, "example-author", "作者から")])

    assert services.sync_github(manager)["返事"] == 2
    own = FeedbackReply.objects.get(github_id=201)
    theirs = FeedbackReply.objects.get(github_id=202)
    assert own.seen_at is not None and theirs.seen_at is None
    assert [reply.pk for reply in services.unseen_replies(plain)] == [theirs.pk]
    # トークンがあれば返事の読みにも使う
    assert comments_calls(fake)[-1][2]["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_sync_counts_unreadable_replies_and_goes_on(manager, plain, fake):
    from seizou.feedback.models import FeedbackReply

    make(plain, status="sent", sent_url=ISSUE_URL)
    fake.issues[7] = Answer(200, {"state": "open", "closed_at": None, "html_url": ISSUE_URL})
    fake.comments = Answer(500, {})

    counts = services.sync_github(manager)

    assert (counts["見た"], counts["読めなかった"], counts["返事"]) == (1, 1, 0)
    assert FeedbackReply.objects.count() == 0


def test_sync_line_ends_with_replies(client, manager, plain, fake):
    from io import StringIO

    from django.core.management import call_command

    make(plain, status="sent", sent_url=ISSUE_URL)
    fake.issues[7] = Answer(200, {"state": "open", "closed_at": None, "html_url": ISSUE_URL})
    fake.comments = Answer(200, [comment(301, "example-author", "どの画面で起きましたか")])
    out = StringIO()

    call_command("seizou_feedback_sync", stdout=out)

    assert out.getvalue().strip().endswith("・読めなかった 0 件・返事 1 件")

    connect(manager, fake)
    client.force_login(manager)
    fake.comments = Answer(200, [comment(301, "example-author", "どの画面で起きましたか"), comment(302, "example-author", "版は")])
    client.post("/feedback/github/sync/")
    assert "読めなかった 0 件・返事 1 件" in html_of(client.get("/feedback/"))


# ---- 書く (返事の下書き)


def test_add_reply_by_author_and_manager(manager, plain, no_http):
    other = get_user_model().objects.create_user("other", password="pass-for-tests-only")
    row = make(plain, status="sent", sent_url=ISSUE_URL)

    reply = services.add_reply(row, "  画面は受注の一覧です。\n並びが戻ります。 ", plain)

    assert reply.body == "画面は受注の一覧です。\n並びが戻ります。"
    assert reply.created_by == plain and reply.feedback_id == row.pk
    assert reply.sent_at is None and reply.github_id is None and reply.login == ""
    assert reply.seen_at is not None
    assert services.unseen_replies(plain) == []
    assert services.add_reply(row, "世話役から", manager).created_by == manager
    with pytest.raises(ValueError, match="書いた人かアカウント管理の担当者だけ"):
        services.add_reply(row, "ほかの人から", other)


@pytest.mark.parametrize("status", ["draft", "confirmed"])
def test_add_reply_refuses_unsent_feedback(plain, status):
    row = make(plain, status=status)
    with pytest.raises(ValueError, match="^送れません。先に GitHub に送るか、送ったにしてください。$"):
        services.add_reply(row, "返事", plain)


def test_add_reply_refuses_empty_and_long(plain):
    row = make(plain, status="fixed", sent_url=ISSUE_URL)
    with pytest.raises(ValueError, match="^保存できません。返事を入力してください。$"):
        services.add_reply(row, "  \n ", plain)
    with pytest.raises(ValueError, match="^保存できません。返事は 2000 字までです。短くしてください。$"):
        services.add_reply(row, "あ" * 2001, plain)
    assert len(services.add_reply(row, "あ" * 2000, plain).body) == 2000


def test_reply_add_endpoint_and_404_for_others(client, plain, fake):
    from seizou.feedback.models import FeedbackReply

    other = get_user_model().objects.create_user("other", password="pass-for-tests-only")
    mine = make(plain, "自分の件", status="sent", sent_url=ISSUE_URL)
    theirs = make(other, "ほかの人の件", status="sent", sent_url=ISSUE_URL)
    draft = make(plain, "下書きの件")
    client.force_login(plain)

    response = client.post(f"/feedback/{mine.pk}/replies/", {"body": "受注の一覧の画面です。"})
    assert response.status_code == 302 and response["Location"] == f"/feedback/{mine.pk}/"
    assert "返事を書きました。" in html_of(client.get(f"/feedback/{mine.pk}/"))
    assert client.post(f"/feedback/{theirs.pk}/replies/", {"body": "返事"}).status_code == 404
    assert client.get(f"/feedback/{mine.pk}/replies/").status_code == 405

    client.post(f"/feedback/{draft.pk}/replies/", {"body": "返事"})
    html = html_of(client.get(f"/feedback/{draft.pk}/"))
    assert "bg-error" in html and "先に GitHub に送るか" in html
    assert FeedbackReply.objects.count() == 1
    assert fake.calls == []


# ---- 送る (世話役が直して会社のトークンで)


def test_send_reply_posts_edited_text(manager, plain, fake):
    connect(manager, fake)
    row = make(plain, status="sent", sent_url=ISSUE_URL)
    reply = services.add_reply(row, "作り物の文", plain)

    url = services.send_reply(reply, "  世話役が直した文  ", manager)

    assert url == f"{ISSUE_URL}#issuecomment-9001"
    method, api, kwargs = fake.calls[-1]
    assert (method, api) == ("POST", "https://api.github.com/repos/example-owner/example-repo/issues/7/comments")
    assert kwargs["json"] == {"body": "世話役が直した文"}
    assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
    reply.refresh_from_db()
    assert reply.body == "世話役が直した文"
    assert reply.sent_at is not None and reply.github_id == 9001
    assert reply.login == "example-feedback-bot"
    assert reply.posted_at is not None and reply.created_by == plain

    with pytest.raises(ValueError, match="^送れません。この返事は送った状態です。$"):
        services.send_reply(reply, "もう一度", manager)

    # 送った返事は、次の取り込みで GitHub から読んでも二重に入らない
    from seizou.feedback.models import FeedbackReply

    fake.issues[7] = Answer(200, {"state": "open", "closed_at": None, "html_url": ISSUE_URL})
    fake.comments = Answer(200, [comment(9001, "example-feedback-bot", "世話役が直した文")])
    assert services.sync_github(manager)["返事"] == 0
    assert FeedbackReply.objects.count() == 1


def test_send_reply_refusals_without_http(manager, plain, no_http):
    row = make(plain, status="sent", sent_url=ISSUE_URL)
    reply = services.add_reply(row, "作り物の文", plain)

    with pytest.raises(ValueError, match="アカウント管理の担当者だけ"):
        services.send_reply(reply, "文", plain)
    with pytest.raises(ValueError, match="^送れません。GitHub とまだつないでいません。"):
        services.send_reply(reply, "文", manager)

    github.write_env_value(github.TOKEN_ENV, TOKEN)
    with pytest.raises(ValueError, match="^送れません。返事を入力してください。$"):
        services.send_reply(reply, " ", manager)
    other_place = make(plain, "ほかの場所", status="sent", sent_url="https://example.com/issues/9")
    with pytest.raises(ValueError, match="Issue に送った物ではありません"):
        services.send_reply(services.add_reply(other_place, "文", plain), "文", manager)
    reply.refresh_from_db()
    assert reply.sent_at is None


def test_send_reply_github_refusal_keeps_draft(manager, plain, fake):
    connect(manager, fake)
    reply = services.add_reply(make(plain, status="sent", sent_url=ISSUE_URL), "作り物の文", plain)
    fake.comment_created = Answer(422, {})

    with pytest.raises(ValueError, match="^送れません。GitHub が内容を受け付けませんでした"):
        services.send_reply(reply, "文", manager)
    reply.refresh_from_db()
    assert reply.sent_at is None and reply.github_id is None and reply.body == "作り物の文"


def test_reply_send_endpoint(client, manager, plain, fake):
    connect(manager, fake)
    other = get_user_model().objects.create_user("other", password="pass-for-tests-only")
    row = make(plain, status="sent", sent_url=ISSUE_URL)
    reply = services.add_reply(row, "作り物の文", plain)
    theirs = services.add_reply(make(other, "ほかの人の件", status="sent", sent_url=ISSUE_URL), "文", other)

    client.force_login(plain)
    response = client.post(f"/feedback/replies/{reply.pk}/send/", {"body": "文"})
    assert response.status_code == 302 and response["Location"] == f"/feedback/{row.pk}/"
    assert "アカウント管理の担当者だけ" in html_of(client.get(f"/feedback/{row.pk}/"))
    assert client.post(f"/feedback/replies/{theirs.pk}/send/", {"body": "文"}).status_code == 404
    assert comments_calls(fake) == []

    client.force_login(manager)
    response = client.post(f"/feedback/replies/{reply.pk}/send/", {"body": "世話役が直した文"})
    assert response["Location"] == f"/feedback/{row.pk}/"
    assert f"返事を GitHub に送りました: {ISSUE_URL}#issuecomment-9001" in html_of(client.get(f"/feedback/{row.pk}/"))
    assert fake.calls[-1][2]["json"] == {"body": "世話役が直した文"}


# ---- 手で写した印 (つないでいない会社)


def test_mark_reply_sent_sets_only_sent_at(client, manager, plain, no_http):
    row = make(plain, status="sent", sent_url=ISSUE_URL)
    reply = services.add_reply(row, "作り物の文", plain)

    with pytest.raises(ValueError, match="アカウント管理の担当者だけ"):
        services.mark_reply_sent(reply, plain)
    services.mark_reply_sent(reply, manager)
    reply.refresh_from_db()
    assert reply.sent_at is not None
    assert (reply.github_id, reply.login, reply.posted_at, reply.body) == (None, "", None, "作り物の文")
    with pytest.raises(ValueError, match="^保存できません。この返事は送った状態です。$"):
        services.mark_reply_sent(reply, manager)

    second = services.add_reply(row, "二つ目", plain)
    client.force_login(manager)
    response = client.post(f"/feedback/replies/{second.pk}/sent/")
    assert response.status_code == 302 and response["Location"] == f"/feedback/{row.pk}/"
    assert "返事を送った状態にしました。" in html_of(client.get(f"/feedback/{row.pk}/"))
    second.refresh_from_db()
    assert second.sent_at is not None and second.github_id is None


# ---------------------------------------------------------------- 送る前に直せる (依頼書 N323)

#: 世話役が伏せ字の「〇〇」のうち「1円」だけを戻した Issue の文 (単価は伏せたまま)
EDITED = "# 合計が 1円 ずれる\n\n- 種類: 不具合\n\n## 実際の動き\n請求の合計が 1円 ずれる。単価は 〇〇。\n"


def test_send_edited_text_as_is(manager, plain, fake):
    connect(manager, fake)
    row = make(plain, status="confirmed")

    url = services.send_to_github(row, manager, text=f"  {EDITED}  ")

    assert url == ISSUE_URL
    method, api, kwargs = fake.calls[-1]
    assert (method, api) == ("POST", "https://api.github.com/repos/example-owner/example-repo/issues")
    assert kwargs["json"]["title"] == "合計が 1円 ずれる"
    assert kwargs["json"]["body"] == EDITED.strip()
    assert "請求の合計が 1円 ずれる。" in kwargs["json"]["body"]
    assert kwargs["json"]["labels"] == ["from-seizou", "不具合"]
    row.refresh_from_db()
    assert (row.status, row.sent_url) == ("sent", ISSUE_URL)


@pytest.mark.parametrize(
    "text, words",
    [("", "Issue の文が空です。"), (" \n \n", "Issue の文が空です。"), ("# \n本文だけ", "1 行目に、題を")],
)
def test_send_refuses_empty_or_titleless_text_without_http(manager, plain, fake, text, words):
    connect(manager, fake)
    row = make(plain, status="confirmed")
    calls = len(fake.calls)

    with pytest.raises(ValueError, match=words) as caught:
        services.send_to_github(row, manager, text=text)

    assert str(caught.value).startswith("送れません。")
    assert len(fake.calls) == calls
    row.refresh_from_db()
    assert (row.status, row.sent_url) == ("confirmed", "")


def test_send_endpoint_sends_edited_text(client, manager, plain, fake):
    connect(manager, fake)
    row = make(plain, status="confirmed")
    client.force_login(manager)

    response = client.post(f"/feedback/{row.pk}/github/", {"issue_text": EDITED})

    assert response.status_code == 302 and response["Location"] == f"/feedback/{row.pk}/"
    assert fake.calls[-1][2]["json"]["body"] == EDITED.strip()
    row.refresh_from_db()
    assert row.status == "sent"

    empty = make(plain, "空の文", status="confirmed")
    calls = len(fake.calls)
    response = client.post(f"/feedback/{empty.pk}/github/", {"issue_text": " "})
    assert response.status_code == 302 and len(fake.calls) == calls
    assert "送れません。Issue の文が空です。" in html_of(client.get(f"/feedback/{empty.pk}/"))
    empty.refresh_from_db()
    assert empty.status == "confirmed"


# ---------------------------------------------------------------- うちもと自社の印 (依頼書 N323)

#: みんなの Issue の作り物の本文 (export_markdown の形。種類は不具合)
ME_TOO_BODY = "- 種類: 不具合\n- 画面: 受注 (orders:order_list)\n\n## 実際の動き\n請求の合計が 〇〇 ずれる。"
#: 既定の送り先 (kishii-py/seizou) の Issue の URL の頭
SEIZOU_ISSUES = "https://github.com/kishii-py/seizou/issues/"


@pytest.fixture
def uchimo(fake):
    """みんなの Issue の作り物 (送り先は既定の kishii-py/seizou)。

    #1 は不具合、#2 は種類の行が無い物、#10〜#30 は古い物 (#10〜#12 は新しい順の 20 件の外)。
    """
    rows = [
        issue(1, "請求の合計が 1 ずれる", ME_TOO_BODY, updated="2026-11-06T00:00:00Z", plus=2),
        issue(2, "受注の CSV に列を足したい", "列を足したい", updated="2026-11-05T00:00:00Z"),
    ]
    rows += [issue(number, f"古い件 {number}", updated=f"2026-10-{number - 9:02d}T00:00:00Z") for number in range(10, 31)]
    fake.listing = Answer(200, rows)
    return fake


def reactions(fake):
    """👍 を付けた POST の控え。"""
    return [call for call in fake.calls if call[0] == "POST" and call[1].endswith("/reactions")]


def listings(fake):
    """Issue の並びを読んだ GET の控え。"""
    return [call for call in fake.calls if call[0] == "GET" and call[1].endswith("/issues")]


def test_add_thumbs_up_posts_plus_one(fake):
    assert github.add_thumbs_up(TOKEN, "example-owner/example-repo", 7) == {"created": True}
    method, url, kwargs = fake.calls[-1]
    assert (method, url) == ("POST", "https://api.github.com/repos/example-owner/example-repo/issues/7/reactions")
    assert kwargs["json"] == {"content": "+1"}
    assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert kwargs["timeout"] == github.TIMEOUT

    fake.reaction = Answer(200, {"id": 1, "content": "+1"})
    assert github.add_thumbs_up(TOKEN, "example-owner/example-repo", 7) == {"created": False}

    fake.reaction = Answer(403, {"message": "Forbidden"})
    with pytest.raises(github.GithubError) as caught:
        github.add_thumbs_up(TOKEN, "example-owner/example-repo", 7)
    assert str(caught.value) == github.MESSAGE_FORBIDDEN
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize(
    "body, kind",
    [
        ("- 種類: 不具合\n- 画面: 指定なし", "bug"),
        ("前の行\r\n- 種類: 決まりの反例\r\n", "counter"),
        ("- 種類: 知らない種類", "common"),
        ("種類の行が無い", "common"),
        (None, "common"),
    ],
)
def test_issue_kind(body, kind):
    assert services.issue_kind(body) == kind


def test_me_too_first_person_adds_thumbs_up_and_sent_row(manager, plain, uchimo):
    connect(manager, uchimo, repo="kishii-py/seizou")
    services.search_issues("")
    assert len(listings(uchimo)) == 1

    result = services.me_too(1, plain)

    assert (result["thumbs_up"], result["connected"]) == (True, True)
    assert [call[1] for call in reactions(uchimo)] == ["https://api.github.com/repos/kishii-py/seizou/issues/1/reactions"]
    assert len(listings(uchimo)) == 1
    row = result["feedback"]
    row.refresh_from_db()
    assert (row.status, row.sent_url, row.kind, row.title) == ("sent", f"{SEIZOU_ISSUES}1", "bug", "請求の合計が 1 ずれる")
    assert row.problem == "同じ困りごとの Issue #1 に「うちも」を付けた"
    assert row.created_by == plain
    # 👍 を付けた後は控えを消す (次に探すと GitHub を読み直す)。
    services.search_issues("")
    assert len(listings(uchimo)) == 2


def test_me_too_second_person_in_company_keeps_one_thumbs_up(manager, plain, uchimo):
    connect(manager, uchimo, repo="kishii-py/seizou")
    services.me_too(1, plain)
    other = get_user_model().objects.create_user("other", password="pass-for-tests-only")
    thumbs = len(reactions(uchimo))

    result = services.me_too(1, other)

    assert len(reactions(uchimo)) == thumbs == 1
    assert (result["thumbs_up"], result["connected"]) == (False, True)
    assert (result["feedback"].status, result["feedback"].created_by) == ("sent", other)

    # 自社が送った件のある Issue も、👍 を付けずに件だけ残す。種類の行が無い本文は要望。
    make(plain, "自社が送った件", status="sent", sent_url=f"{SEIZOU_ISSUES}2")
    result = services.me_too(2, other)
    assert len(reactions(uchimo)) == thumbs and result["thumbs_up"] is False
    assert (result["feedback"].kind, result["feedback"].sent_url) == ("common", f"{SEIZOU_ISSUES}2")


def test_me_too_same_person_twice_is_refused(manager, plain, uchimo):
    connect(manager, uchimo, repo="kishii-py/seizou")
    services.me_too(1, plain)
    thumbs = len(reactions(uchimo))

    with pytest.raises(ValueError, match="保存できません。この Issue には、あなたの「うちも」が既にあります。"):
        services.me_too(1, plain)

    assert len(reactions(uchimo)) == thumbs == 1
    assert Feedback.objects.count() == 1


def test_me_too_without_connection_leaves_draft_without_thumbs_up(plain, uchimo):
    result = services.me_too(10, plain)

    assert not reactions(uchimo)
    assert (result["thumbs_up"], result["connected"]) == (False, False)
    row = result["feedback"]
    assert (row.status, row.sent_url, row.title) == ("draft", "", "古い件 10")
    assert row.problem == (
        f"同じ困りごとの Issue #10 がある ({SEIZOU_ISSUES}10)。GitHub とつないでいないので 👍 は付けていない。世話役がつないだ後に付ける"
    )
    with pytest.raises(ValueError, match="既にあります"):
        services.me_too(10, plain)
    assert services.our_issue_marks() == {}


def test_me_too_refuses_unknown_number_and_anonymous(plain, uchimo):
    from django.contrib.auth.models import AnonymousUser

    with pytest.raises(ValueError, match="保存できません。ログインしてから押してください。"):
        services.me_too(1, AnonymousUser())
    with pytest.raises(ValueError, match="保存できません。Issue #99 が送り先に見つかりません。"):
        services.me_too(99, plain)
    for bad in (0, -1, "1", True, None):
        with pytest.raises(ValueError, match="保存できません。Issue の番号は 1 以上の整数です。"):
            services.me_too(bad, plain)
    assert Feedback.objects.count() == 0
    assert not reactions(uchimo)


def test_me_too_github_refusal_and_unreadable_list_make_no_row(manager, plain, uchimo):
    connect(manager, uchimo, repo="kishii-py/seizou")
    uchimo.reaction = Answer(403, {"message": "Forbidden"})
    with pytest.raises(ValueError) as caught:
        services.me_too(1, plain)
    assert str(caught.value) == "送れません。" + github.MESSAGE_FORBIDDEN
    assert TOKEN not in str(caught.value)

    cache.clear()
    uchimo.listing = Answer(500, {})
    with pytest.raises(ValueError, match="保存できません。GitHub の Issue を読めませんでした"):
        services.me_too(2, plain)
    assert Feedback.objects.count() == 0


def test_our_issue_marks_shape(manager, plain, uchimo):
    connect(manager, uchimo, repo="kishii-py/seizou")
    sent = make(plain, "自社が送った件", status="sent", sent_url=f"{SEIZOU_ISSUES}1")
    fixed = make(plain, "直した件", status="fixed", sent_url=f"{SEIZOU_ISSUES}2")
    first = services.me_too(1, manager)["feedback"]
    services.me_too(1, get_user_model().objects.create_user("other", password="pass-for-tests-only"))
    only = services.me_too(10, plain)["feedback"]
    make(plain, "ほかの送り先", status="sent", sent_url="https://github.com/example-owner/example-repo/issues/1")
    make(plain, "下書き")

    assert services.our_issue_marks() == {
        1: {"sent": sent.pk, "me_too": first.pk},
        2: {"sent": fixed.pk, "me_too": None},
        10: {"sent": None, "me_too": only.pk},
    }


# ---------------------------------------------------------------- 閉じた理由 (依頼書 N323)


def test_list_issues_returns_reason_closed_at_labels_and_milestone(fake):
    fake.listing = Answer(
        200,
        [
            issue(
                5,
                "直った件",
                state="closed",
                state_reason="completed",
                closed_at="2026-11-06T03:00:00Z",
                labels=[{"name": "from-seizou"}, {"name": "不具合"}, "形の違う印"],
                milestone={"title": "0.1.1"},
            ),
            issue(6, "開いた件", state_reason=None, closed_at=None, labels=[], milestone=None),
            issue(7, "鍵の無い件"),
        ],
    )

    rows = github.list_issues("example-owner/example-repo")

    keys = {"number", "title", "body", "state", "html_url", "updated_at", "comments", "thumbs_up"}
    assert all(set(row) == keys | {"state_reason", "closed_at", "labels", "milestone"} for row in rows)
    first = rows[0]
    assert (first["state_reason"], first["closed_at"], first["labels"], first["milestone"]) == (
        "completed",
        "2026-11-06T03:00:00Z",
        ["from-seizou", "不具合"],
        "0.1.1",
    )
    for row in rows[1:]:
        assert (row["state_reason"], row["closed_at"], row["labels"], row["milestone"]) == ("", "", [], "")


# ---------------------------------------------------------------- Issue の並びのページ送り (依頼書 N325)


def test_list_issues_reads_pages_until_short_page(fake):
    # 1 ページ目は Issue 99 件と pull request 1 件 (外す前で 100 件なので続きを読む)、2 ページ目は 3 件で終わり。
    first = [issue(number, f"件 {number}") for number in range(1, 100)] + [issue(100, "PR", pull_request={"url": "x"})]
    fake.board[("open", 1)] = Answer(200, first)
    fake.board[("open", 2)] = Answer(200, [issue(number, f"件 {number}") for number in range(101, 104)])

    rows = github.list_issues("example-owner/example-repo", state="open", pages=github.BOARD_PAGES)

    assert [row["number"] for row in rows] == [*range(1, 100), 101, 102, 103]
    gets = listings(fake)
    assert len(gets) == 2
    assert gets[0][2]["params"] == {"state": "open", "per_page": 100, "sort": "updated"}
    assert gets[1][2]["params"] == {"state": "open", "per_page": 100, "sort": "updated", "page": 2}

    fake.calls.clear()
    assert len(github.list_issues("example-owner/example-repo", state="open")) == 99
    assert len(listings(fake)) == 1


def test_list_issues_sends_state_and_since_and_stops_at_board_pages(fake):
    github.list_issues("example-owner/example-repo", state="closed", since="2026-10-08T00:00:00Z")
    assert listings(fake)[-1][2]["params"] == {"state": "closed", "per_page": 100, "sort": "updated", "since": "2026-10-08T00:00:00Z"}

    # どのページも 100 件なら、pages が大きくても BOARD_PAGES (10) ページで止める。
    fake.calls.clear()
    fake.listing = Answer(200, [issue(number, f"件 {number}") for number in range(1, 101)])
    assert len(github.list_issues("example-owner/example-repo", pages=50)) == 100 * github.BOARD_PAGES
    assert [call[2]["params"].get("page", 1) for call in listings(fake)] == list(range(1, github.BOARD_PAGES + 1))

    fake.board[("all", 3)] = Answer(500, {})
    with pytest.raises(github.GithubError):
        github.list_issues("example-owner/example-repo", pages=5)
