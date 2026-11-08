"""上の帯の知らせの鈴 (受け渡しの seizou-部品の6案-20261107/指示書.md の 4。本人の決め 11-07 18:2x〜18:4x)。

鈴は「知っておいた方がいいこと」の印: 鈴を開いた日時 (core.models.BellOpened。無ければ登録した日時) より後に起きた知らせ
(受信した注文・自分のフィードバックの直した・見送った・返事) だけを赤い数で数え、開くと印が消える。
HTML は Django の test client で見る (本物のブラウザは使わない)。本物の GitHub と API は呼ばない。名前は作り物だけ。
"""

import re
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.core import bell, context_processors
from seizou.core import services as core_services
from seizou.core.models import BellOpened
from seizou.feedback import services as feedback_services
from seizou.feedback.models import FeedbackReply
from seizou.masters import services as masters_services

HTMX = {"HTTP_HX_REQUEST": "true"}
BELL_URL = "/bell/"
ISSUE_URL = "https://github.com/example-owner/example-repo/issues/7"
BADGE_SHOWN = '<span id="seizou-bell-badge" class="badge badge-error'
BADGE_HIDDEN = '<span id="seizou-bell-badge" hidden></span>'
BADGE_CLEARED = '<span id="seizou-bell-badge" hx-swap-oob="true" hidden></span>'


@pytest.fixture
def manager(db):
    return get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")


@pytest.fixture
def other(db):
    return get_user_model().objects.create_user("other", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


def received(customer, **extra):
    """受信した注文を 1 件 (状態は「届いた」)。"""
    return Inbox.objects.create(message_id=uuid.uuid4(), partner=customer, kind="order", payload={}, **extra)


def feedback_of(user, title="受注の一覧で並びが戻る"):
    return feedback_services.create_feedback(
        {"kind": "bug", "screen": "orders:order_list", "title": title, "problem": "並び替えた後に戻ると、並びが元に戻る。"}, user
    )


def dismissed(user, manager, title="出荷の画面で日付がずれる"):
    row = feedback_of(user, title)
    feedback_services.mark_sent(row, manager, sent_url=ISSUE_URL)
    return feedback_services._write_dismissed(row, "GitHub の Issue #7 (閉じた日 2026-11-06。理由 対応しない)")


def reply_of(row, login="example-author", when=None, github_id=None):
    """GitHub から来た返事 (書いた人が空。取り込みが書く形)。"""
    return FeedbackReply.objects.create(
        feedback=row,
        github_id=github_id or int(timezone.now().timestamp() * 1000) % 10**9 + row.pk,
        login=login,
        body="どの画面で起きましたか",
        posted_at=when or timezone.now(),
        sent_at=when or timezone.now(),
    )


def header_of(body: str) -> str:
    start = body.index('<header class="seizou-header')
    return body[start : body.index("</header>", start)]


def badge_of(html: str) -> str:
    return re.search(r'<span id="seizou-bell-badge"[^>]*>(?:[^<]*</span>)?', html).group(0)


# ---- 1. 数え方 ----


def test_count_is_zero_for_new_user_and_counts_every_kind(user, manager, customer):
    # 登録より前に届いた物は数えない (行が無い人は登録した日時から数える)。
    received(customer, received_at=user.date_joined - timedelta(days=1))
    assert bell.unread_count(user) == 0
    assert bell.unread_count(AnonymousUser()) == 0

    received(customer)
    fixed = feedback_of(user, "直った題")
    feedback_services.mark_fixed(fixed, "", manager)
    dismissed(user, manager)
    reply_of(feedback_of(user, "返事の題"))
    assert bell.unread_count(user) == 4
    assert bell.badge_text(4) == "4" and bell.badge_text(0) == "" and bell.badge_text(100) == "99+"

    # ほかの人のフィードバックは数えない (受信した注文は会社の物なので数える)。
    assert bell.unread_count(manager) == 1


def test_company_own_reply_and_drafts_are_not_counted(user, monkeypatch):
    monkeypatch.setattr(feedback_services, "company_login", lambda: "example-feedback-bot")
    row = feedback_of(user)
    reply_of(row, login="example-feedback-bot", github_id=5001)
    FeedbackReply.objects.create(feedback=row, body="会社の中の下書き", created_by=user)
    assert bell.unread_count(user) == 0

    reply_of(row, login="example-author", github_id=5002)
    assert bell.unread_count(user) == 1
    assert [item["title"] for item in bell.notices(user)] == [feedback_services.BELL_REPLY]


def test_inbox_is_not_counted_for_people_who_cannot_open_connect(user, customer, monkeypatch):
    received(customer)
    assert bell.unread_count(user) == 1
    monkeypatch.setattr(connect_services.access, "can_open", lambda user, app, **kwargs: app != "connect")
    assert bell.unread_count(user) == 0
    assert bell.notices(user) == []


def test_missing_part_is_skipped(user, manager, customer, monkeypatch):
    received(customer)
    fixed = feedback_of(user)
    feedback_services.mark_fixed(fixed, "", manager)
    assert bell.unread_count(user) == 2

    monkeypatch.setattr(bell.apps, "is_installed", lambda name: name != "seizou.connect")
    assert bell.unread_count(user) == 1
    assert [item["title"] for item in bell.notices(user)] == [feedback_services.BELL_FIXED]


def test_count_uses_a_few_count_queries(user, customer):
    received(customer)
    with CaptureQueriesContext(connection) as captured:
        assert bell.unread_count(user) == 1
    # 知らせの表は件数だけ読む (行は読まない): 受信した注文 1 + フィードバック 1 (直した・見送った・返事を 1 つの集計で)。
    # ほかは開いた日時 1 と、部署の判定 (access.can_open) の設定と権限の行 (要求の中では控えが効く)。
    # 上の帯は全画面で描くので、画面ごとの問い合わせの上限 (tests/test_core_screen_queries.py の BELL_QUERIES = 3) に収める。
    notice_queries = [query["sql"] for query in captured if "connect_inbox" in query["sql"] or "feedback_" in query["sql"]]
    assert len(notice_queries) == 2
    assert all("COUNT(" in sql for sql in notice_queries)
    assert len(captured) <= 7


# ---- 2. 開くと印が消える ----


def test_open_bell_lists_notices_marks_new_and_clears_badge(client_logged_in, user, manager, customer):
    inbox = received(customer)
    fixed = feedback_of(user, "直った題")
    feedback_services.mark_fixed(fixed, "", manager)
    gone = dismissed(user, manager, "見送りの題")
    replied = feedback_of(user, "返事の題")
    reply_of(replied)

    header = header_of(client_logged_in.get(reverse("core:home")).content.decode())
    assert 'id="seizou-bell"' in header and 'hx-get="/bell/"' in header and 'hx-target="#seizou-modal-box"' in header
    assert header.index('id="seizou-bell"') < header.index('aria-label="ブックマーク"')
    assert badge_of(header).startswith(BADGE_SHOWN) and ">4</span>" in badge_of(header)

    response = client_logged_in.get(BELL_URL, **HTMX)
    html = response.content.decode()
    assert response.status_code == 200
    assert "<html" not in html
    assert html.lstrip().startswith(BADGE_CLEARED)  # 印の箱が先頭 (hx-swap-oob は応答の最上位の要素だけに効く)
    assert 'id="seizou-bell-list"' in html and html.count("data-new") == 4 and html.count(">新</span>") == 4
    for word in (connect_services.BELL_TITLE, feedback_services.BELL_FIXED, feedback_services.BELL_DISMISSED, feedback_services.BELL_REPLY):
        assert word in html, word
    for url in (
        reverse("connect:inbox_detail", args=[inbox.pk]),
        reverse("feedback:feedback_detail", args=[fixed.pk]),
        reverse("feedback:feedback_detail", args=[gone.pk]),
        reverse("feedback:feedback_detail", args=[replied.pk]),
    ):
        assert f'href="{url}"' in html, url
    assert "作り物の得意先 の注文" in html and "直った題" in html and "新しい知らせはありません" not in html

    assert BellOpened.objects.get(user=user).opened_at >= inbox.received_at
    assert bell.unread_count(user) == 0
    header = header_of(client_logged_in.get(reverse("core:home")).content.decode())
    assert badge_of(header) == BADGE_HIDDEN

    # 2 回目は並びは残り、「新」は付かない。
    html = client_logged_in.get(BELL_URL, **HTMX).content.decode()
    assert 'id="seizou-bell-list"' in html and "data-new" not in html and ">新</span>" not in html

    # 新しく届くとまた数える。
    # Windows の時計の粒度では opened_at と同じ時刻になりうる (CI で 0 == 1 で落ちた) ので、届いた時刻を明示的に 1 ms 後にする。
    received(customer, received_at=BellOpened.objects.get(user=user).opened_at + timedelta(milliseconds=1))
    assert bell.unread_count(user) == 1


def test_empty_box_and_limit(client_logged_in, user, customer):
    html = client_logged_in.get(BELL_URL, **HTMX).content.decode()
    assert "新しい知らせはありません" in html and 'id="seizou-bell-list"' not in html

    for _ in range(12):
        received(customer)
    html = client_logged_in.get(BELL_URL, **HTMX).content.decode()
    assert html.count("data-new") == bell.LIMIT == 10


def test_badge_shows_99_plus(client_logged_in, monkeypatch):
    monkeypatch.setattr(context_processors, "_bell_count", lambda user: 120)
    header = header_of(client_logged_in.get(reverse("core:home")).content.decode())
    assert ">99+</span>" in badge_of(header)


def test_open_bell_writes_only_own_row(user, other):
    before = timezone.now()
    row = core_services.open_bell(user)
    assert row.user == user and row.opened_at >= before
    assert BellOpened.objects.count() == 1
    assert core_services.bell_opened_at(other) == other.date_joined
    assert core_services.bell_opened_at(AnonymousUser()) is None

    again = core_services.open_bell(user)
    assert again.pk == row.pk and again.opened_at >= row.opened_at


# ---- 3. 口の門 ----


def test_bell_guards(client, user):
    response = client.get(BELL_URL, **HTMX)
    assert response.status_code == 302 and "/login/" in response["Location"]

    client.force_login(user)
    assert client.get(BELL_URL).status_code == 400
    assert client.post(BELL_URL, **HTMX).status_code == 405
    assert not BellOpened.objects.filter(user=user).exists()


def test_bell_is_not_shown_before_login(client, db):
    html = client.get(reverse("core:login")).content.decode()
    assert 'id="seizou-bell"' not in html


# ---- 4. migration ----


def test_migration_exists_and_matches_model(db):
    from pathlib import Path

    import seizou.core

    files = sorted(path.name for path in (Path(seizou.core.__file__).parent / "migrations").glob("0*.py"))
    assert files[-1] == "0008_bellopened.py"
    # 表と migration が合っている (変更があれば SystemExit で落ちる)。
    call_command("makemigrations", "core", check=True, dry_run=True, verbosity=0)
