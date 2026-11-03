"""フィードバックの「直した」(状態と記録・詳細の「直したと記録する」と「直しました」の節・ログインのときの 1 回の知らせ・一覧の印)。本物の API は呼ばない。題と名前は作り物。"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from seizou.assistant import services as assistant_services
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


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def manager(db):
    return get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")


@pytest.fixture
def plain(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def other(db):
    return get_user_model().objects.create_user("other", password="pass-for-tests-only")


def html_of(response) -> str:
    return response.content.decode()


def make(user, title="受注の一覧で並びが戻る", **extra):
    data = {"kind": "bug", "screen": "orders:order_list", "title": title, "problem": "並び替えた後に戻ると、並びが元に戻る。"}
    return services.create_feedback({**data, **extra.pop("data", {})}, user, **extra)


def login(client, username):
    return client.post(reverse("core:login"), {"username": username, "password": "pass-for-tests-only"})


# ---- 1. 状態「直した」と記録の services ----


def test_mark_fixed_only_manager_records_time_and_note(plain, manager):
    row = make(plain)
    with pytest.raises(ValueError, match="アカウント管理の担当者だけ"):
        services.mark_fixed(row, "版 0.4.0 の受注の一覧", plain)
    row.refresh_from_db()
    assert row.status == "draft"
    assert row.fixed_at is None

    before = timezone.now()
    done = services.mark_fixed(row, "  版 0.4.0 の受注の一覧  ", manager)
    after = timezone.now()
    assert done.pk == row.pk
    row.refresh_from_db()
    assert row.status == "fixed"
    assert row.get_status_display() == "直した"
    assert before <= row.fixed_at <= after
    assert row.fixed_note == "版 0.4.0 の受注の一覧"
    assert row.fixed_seen_at is None
    with pytest.raises(ValueError, match="既に直した"):
        services.mark_fixed(row, "", manager)

    long_row = make(plain, "長い場所の題")
    with pytest.raises(ValueError, match="200 字まで"):
        services.mark_fixed(long_row, "あ" * 201, manager)
    long_row.refresh_from_db()
    assert long_row.status == "draft"
    services.mark_fixed(long_row, "あ" * 200, manager)
    long_row.refresh_from_db()
    assert long_row.status == "fixed"
    assert len(long_row.fixed_note) == 200

    empty = make(plain, "場所の無い題")
    services.mark_fixed(empty, "", manager)
    empty.refresh_from_db()
    assert (empty.status, empty.fixed_note) == ("fixed", "")


def test_fixed_from_any_state_and_locks_author(plain, manager):
    draft = make(plain, "下書きの題")
    confirmed = make(plain, "確認済みの題")
    services.mark_confirmed(confirmed, manager)
    sent = make(plain, "送った題")
    services.mark_sent(sent, manager, sent_url="https://example.com/issues/1")

    for row in (draft, confirmed, sent):
        services.mark_fixed(row, "版 0.4.0 の受注の一覧", manager)
        row.refresh_from_db()
        assert row.status == "fixed"
    assert sent.sent_url == "https://example.com/issues/1"

    for row in (draft, confirmed, sent):
        with pytest.raises(ValueError, match="直した状態"):
            services.mark_sent(row, manager)
        with pytest.raises(ValueError, match="下書きだけ"):
            services.mark_confirmed(row, manager)
        assert not services.can_edit(row, plain)
        assert not services.can_delete(row, plain)

    with pytest.raises(ValueError, match="書いた人"):
        services.update_feedback(sent, {"title": "書いた人が直す題"}, plain)
    services.update_feedback(sent, {"title": "世話役が直した題"}, manager)
    sent.refresh_from_db()
    assert (sent.title, sent.status) == ("世話役が直した題", "fixed")
    with pytest.raises(ValueError, match="下書きだけ"):
        services.delete_feedback(sent, manager)
    assert Feedback.objects.filter(pk=sent.pk).exists()


def test_fixed_status_in_choices_and_filter():
    assert Feedback.STATUS_CHOICES[-1] == ("fixed", "直した")
    assert services.STATUSES == ("draft", "confirmed", "sent", "fixed")
    assert services.list_filter({"status": "fixed"}) == {"status": "fixed", "kind": ""}
    assert services.FIXED_NOTE_MAX_CHARS == 200
    assert Feedback.detail_hidden_fields == ("transcript", "fixed_at", "fixed_note", "fixed_seen_at")


# ---- 2. 詳細の「直したと記録する」と「直しました」の節 ----


def test_detail_fixed_form_only_for_manager(client, plain, manager):
    row = make(plain)

    client.force_login(manager)
    html = html_of(client.get(f"/feedback/{row.pk}/"))
    assert f'action="/feedback/{row.pk}/fixed/"' in html
    assert 'name="fixed_note"' in html and 'maxlength="200"' in html
    assert "直したと記録する" in html
    assert 'id="feedback-fixed"' not in html

    client.force_login(plain)
    html = html_of(client.get(f"/feedback/{row.pk}/"))
    assert '/fixed/"' not in html
    assert "直したと記録する" not in html


def test_fixed_endpoint_and_detail_section(client, plain, other, manager):
    row = make(plain)
    detail = f"/feedback/{row.pk}/"

    client.force_login(plain)
    response = client.post(f"/feedback/{row.pk}/fixed/", {"fixed_note": "版 0.4.0 の受注の一覧"})
    assert response.status_code == 302 and response["Location"] == detail
    html = html_of(client.get(detail))
    assert "bg-error" in html and "アカウント管理の担当者だけ" in html
    row.refresh_from_db()
    assert row.status == "draft"
    assert client.get(f"/feedback/{row.pk}/fixed/").status_code == 405

    client.force_login(manager)
    response = client.post(f"/feedback/{row.pk}/fixed/", {"fixed_note": "版 0.4.0 の受注の一覧"})
    assert response.status_code == 302 and response["Location"] == detail
    row.refresh_from_db()
    assert row.status == "fixed"
    html = html_of(client.get(detail))
    assert "直した状態にしました。" in html
    assert 'id="feedback-fixed"' in html and "直しました" in html
    assert "版 0.4.0 の受注の一覧" in html
    assert timezone.localtime(row.fixed_at).strftime("%Y-%m-%d %H:%M") in html
    assert "直したと記録する" not in html
    assert "送ったにする" not in html and "確認済みにする" not in html

    response = client.post(f"/feedback/{row.pk}/fixed/", {"fixed_note": ""})
    assert response.status_code == 302 and response["Location"] == detail
    html = html_of(client.get(detail))
    assert "bg-error" in html and "既に直した" in html

    client.force_login(plain)
    html = html_of(client.get(detail))
    assert 'id="feedback-fixed"' in html and "版 0.4.0 の受注の一覧" in html
    assert "直したと記録する" not in html

    client.force_login(other)
    assert client.get(detail).status_code == 404

    sent = make(plain, "送った題")
    services.mark_sent(sent, manager, sent_url="https://example.com/issues/1")
    services.mark_fixed(sent, "", manager)
    client.force_login(manager)
    html = html_of(client.get(f"/feedback/{sent.pk}/"))
    assert 'id="feedback-fixed"' in html
    assert "送ったにする" not in html
    assert "書いていません" in html


def test_list_filter_shows_fixed(client, plain, manager):
    make(plain, "下書きの題")
    fixed = make(plain, "直した題")
    services.mark_fixed(fixed, "", manager)

    client.force_login(plain)
    html = html_of(client.get("/feedback/"))
    assert '<option value="fixed"' in html and "直した" in html

    html = html_of(client.get("/feedback/?status=fixed"))
    assert "直した題" in html and "下書きの題" not in html
    assert '<option value="fixed" selected>' in html

    html = html_of(client.get("/feedback/?status=draft"))
    assert "下書きの題" in html and "直した題" not in html


# ---- 3. ログインのときの知らせ ----

NOTICE = "あなたの一言から直しました"


def test_unseen_fixed_and_notice_text(plain, other, manager):
    first = make(plain, "受注の一覧で並びが戻る")
    theirs = make(other, "ほかの人の題")
    second = make(plain, "出荷の画面で日付がずれる")
    services.mark_fixed(first, "", manager)
    services.mark_fixed(theirs, "", manager)
    services.mark_fixed(second, "", manager)

    rows = services.unseen_fixed(plain)
    assert [r.title for r in rows] == ["出荷の画面で日付がずれる", "受注の一覧で並びが戻る"]
    assert services.unseen_fixed(AnonymousUser()) == []
    assert services.fixed_notice([rows[0]]) == (
        "あなたの一言から直しました: 「出荷の画面で日付がずれる」(1 件)。"
        "詳細は左のメニューの下の「フィードバック」の一覧で見られます。"
    )
    assert "「出荷の画面で日付がずれる」ほか (2 件)" in services.fixed_notice(rows)
    assert services.fixed_notice([]) == ""

    before = {row.pk: row.updated_at for row in rows}
    assert services.mark_fixed_seen(rows, other) == 0
    assert services.mark_fixed_seen(rows, plain) == 2
    for row in rows:
        row.refresh_from_db()
        assert row.fixed_seen_at is not None
        assert row.updated_at == before[row.pk]
    assert services.mark_fixed_seen(rows, plain) == 0
    theirs.refresh_from_db()
    assert theirs.fixed_seen_at is None


def test_login_shows_fixed_notice_once(client, plain, manager):
    row = make(plain, "受注の一覧で並びが戻る")
    services.mark_fixed(row, "版 0.4.0 の受注の一覧", manager)

    assert login(client, "tester").status_code == 302
    html = html_of(client.get("/feedback/"))
    assert NOTICE in html
    assert "「受注の一覧で並びが戻る」(1 件)" in html
    row.refresh_from_db()
    assert row.fixed_seen_at is not None

    assert NOTICE not in html_of(client.get("/feedback/"))
    client.logout()
    assert login(client, "tester").status_code == 302
    assert NOTICE not in html_of(client.get("/feedback/"))


def test_notice_only_for_author(client, plain, other, manager):
    row = make(plain)
    services.mark_fixed(row, "", manager)

    assert login(client, "other").status_code == 302
    assert NOTICE not in html_of(client.get("/feedback/"))
    client.logout()
    assert login(client, "admin").status_code == 302
    assert NOTICE not in html_of(client.get("/feedback/"))
    row.refresh_from_db()
    assert row.fixed_seen_at is None

    client.logout()
    assert login(client, "tester").status_code == 302
    assert NOTICE in html_of(client.get("/feedback/"))


def test_force_login_keeps_notice_for_real_login(client, plain, manager):
    row = make(plain)
    services.mark_fixed(row, "", manager)

    client.force_login(plain)
    row.refresh_from_db()
    assert row.fixed_seen_at is None
    assert NOTICE not in html_of(client.get("/feedback/"))

    client.logout()
    assert login(client, "tester").status_code == 302
    assert NOTICE in html_of(client.get("/feedback/"))
    row.refresh_from_db()
    assert row.fixed_seen_at is not None


def test_new_fix_after_seen_notifies_again(client, plain, manager):
    first = make(plain, "受注の一覧で並びが戻る")
    second = make(plain, "出荷の画面で日付がずれる")
    services.mark_fixed(first, "", manager)

    assert login(client, "tester").status_code == 302
    assert "「受注の一覧で並びが戻る」(1 件)" in html_of(client.get("/feedback/"))
    client.logout()

    services.mark_fixed(second, "", manager)
    assert login(client, "tester").status_code == 302
    html = html_of(client.get("/feedback/"))
    assert "「出荷の画面で日付がずれる」(1 件)" in html
    assert "「受注の一覧で並びが戻る」" not in html


# ---- 4. 一覧の「直した」の印 ----


def test_list_marks_fixed_rows(client, plain, manager):
    make(plain, "下書きの題")
    fixed = make(plain, "直した題")
    services.mark_fixed(fixed, "", manager)

    client.force_login(plain)
    html = html_of(client.get("/feedback/"))
    assert html.count("badge badge-soft badge-success") == 1
    assert re.search(r'badge-success">直した<', html)
    assert html.count('<tr id="row-') == 2
    assert f'href="/feedback/{fixed.pk}/"' in html

    html = html_of(client.get("/feedback/?status=fixed", HTTP_HX_REQUEST="true"))
    assert "<html" not in html
    assert html.count('<tr id="row-') == 1
    assert html.count("badge badge-soft badge-success") == 1
