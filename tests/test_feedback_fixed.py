"""フィードバックの「直した」(状態と記録・詳細の「直したと記録する」と「直しました」の節・ログインのときの 1 回の知らせ・一覧の印)。本物の API は呼ばない。題と名前は作り物。"""

import pytest
from django.contrib.auth import get_user_model
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
