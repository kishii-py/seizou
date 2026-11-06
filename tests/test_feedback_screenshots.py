"""フィードバックの画面の絵 (入力の形の欄・添付ファイルの部品での保存・詳細の小さい絵・Issue の文の枚数の 1 行・AI の下書きの知らせ)。

本物の API は呼ばない。storage は tmp_path の下。題と名前は作り物。
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.datastructures import MultiValueDict

from seizou.assistant import services as assistant_services
from seizou.attachments import services as attachments_services
from seizou.feedback import chat, services
from seizou.feedback.forms import FeedbackForm, ScreenshotsField
from seizou.feedback.models import Feedback
from seizou.masters import services as masters_services
from tests.test_assistant_services import refuse_api

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(assistant_services, "_ask_claude", refuse_api)


@pytest.fixture(autouse=True)
def fresh_catalog():
    services.screen_catalog.cache_clear()
    yield
    services.screen_catalog.cache_clear()


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


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


def html_of(response) -> str:
    return response.content.decode()


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


def png(name, content=PNG_BYTES):
    return SimpleUploadedFile(name, content)


def stored_files(root):
    return sorted(p for p in root.rglob("*") if p.is_file())


def attached(row) -> list:
    return list(attachments_services.attachments_for(row))


# ---- 1. 入力の形の「画面の絵」の欄と保存 ----


def test_create_with_screenshots_attaches_images(plain, media_root):
    row = services.create_feedback(form_data(), plain, screenshots=[png("a.png"), png("b.jpg")])

    rows = attached(row)
    assert {r.original_name for r in rows} == {"a.png", "b.jpg"}
    for r in rows:
        assert r.kind == "other"
        assert r.note == "画面の絵"
        assert r.uploaded_by == plain
        assert r.target == row
    assert len(stored_files(media_root)) == 2

    bare = services.create_feedback(form_data(title="絵の無い題"), plain)
    assert attached(bare) == []
    assert len(stored_files(media_root)) == 2


def test_screenshots_are_checked_before_any_write(plain, media_root):
    cases = [
        ([png(f"{n}.png") for n in range(4)], "3 枚まで"),
        ([png("a.png"), png("memo.pdf")], "PNG・JPEG・WebP"),
        ([png("a.gif")], "PNG・JPEG・WebP"),
        ([png("big.png", b"\x00" * (5 * 1024 * 1024 + 1))], "5MB まで"),
        ([png("e.png", b"")], "空です"),
        ([png("a" * 97 + ".png")], "100 字"),
        (png("a.png"), "並び"),
    ]
    for screenshots, words in cases:
        with pytest.raises(ValueError, match=words):
            services.create_feedback(form_data(), plain, screenshots=screenshots)
        assert Feedback.objects.count() == 0
        assert stored_files(media_root) == []

    row = services.create_feedback(form_data(), plain, screenshots=[png("just.png", b"\x00" * (5 * 1024 * 1024))])
    assert len(attached(row)) == 1


def test_update_with_screenshots_follows_can_edit(plain, other, manager, media_root):
    row = services.create_feedback(form_data(), plain)

    with pytest.raises(ValueError, match="書いた人"):
        services.update_feedback(row, {"title": "直した題"}, other, screenshots=[png("x.png")])
    assert attached(row) == []
    assert stored_files(media_root) == []

    services.update_feedback(row, {}, plain, screenshots=[png("x.png")])
    assert len(attached(row)) == 1

    services.mark_confirmed(row, manager)
    with pytest.raises(ValueError, match="書いた人"):
        services.update_feedback(row, {}, plain, screenshots=[png("y.png")])
    assert len(attached(row)) == 1

    services.update_feedback(row, {}, manager, screenshots=[png("z.png")])
    assert len(attached(row)) == 2
    assert len(stored_files(media_root)) == 2


def test_screenshots_of_lists_images_in_attached_order(plain):
    row = services.create_feedback(form_data(), plain, screenshots=[png("1.png"), png("2.webp")])
    attachments_services.add_attachment(row, png("memo.pdf", b"%PDF-1.4"), plain, kind="other")
    attachments_services.add_attachment(row, png("3.jpeg"), plain, kind="photo")

    assert [r.original_name for r in services.screenshots_of(row)] == ["1.png", "2.webp", "3.jpeg"]


def test_screenshots_need_attachments_part(plain, monkeypatch, media_root):
    monkeypatch.setattr(services, "screenshots_enabled", lambda: False)

    assert "screenshots" not in FeedbackForm().fields
    with pytest.raises(ValueError, match="添付ファイルの部品"):
        services.create_feedback(form_data(), plain, screenshots=[png("a.png")])
    assert Feedback.objects.count() == 0
    assert stored_files(media_root) == []

    row = services.create_feedback(form_data(), plain, screenshots=[])
    services.create_feedback(form_data(title="渡さない形"), plain)
    assert Feedback.objects.count() == 2
    assert services.screenshots_of(row) == []


def test_form_field_shape_and_service_kwargs(db):
    form = FeedbackForm()
    assert isinstance(form.fields["screenshots"], ScreenshotsField)
    assert form.fields["screenshots"].required is False
    assert form["screenshots"].widget_type == "file"
    assert form.is_multipart()

    form = FeedbackForm(data=form_data(), files=MultiValueDict({"screenshots": [png("a.png")]}))
    assert form.is_valid(), form.errors
    kwargs = form.service_kwargs()
    assert set(kwargs) == {"data", "screenshots"}
    assert set(kwargs["data"]) == set(services.FIELD_KEYS)
    assert [f.name for f in kwargs["screenshots"]] == ["a.png"]

    form = FeedbackForm(data=form_data())
    assert form.is_valid(), form.errors
    assert set(form.service_kwargs()) == {"data"}


def test_create_screen_field_and_save_with_screenshots(client_logged_in, plain):
    response = client_logged_in.get("/feedback/new/")
    html = html_of(response)

    assert response.status_code == 200
    for words in (
        'name="screenshots"',
        " multiple",
        'accept="image/png,image/jpeg,image/webp"',
        'enctype="multipart/form-data"',
        "画面の絵",
        "ドラッグ",
        "(ファイルは選び直します)",
    ):
        assert words in html
    assert html.count("btn-primary") == 1

    response = client_logged_in.post("/feedback/new/", {**form_data(), "screenshots": [png("a.png"), png("b.webp")]})
    row = Feedback.objects.get()
    assert response.status_code == 302
    assert response["Location"] == f"/feedback/{row.pk}/"
    rows = attached(row)
    assert len(rows) == 2
    for r in rows:
        assert (r.kind, r.note, r.uploaded_by) == ("other", "画面の絵", plain)


def test_create_refuses_bad_screenshots_with_red_notice(client_logged_in, media_root):
    response = client_logged_in.post("/feedback/new/", {**form_data(), "screenshots": [png(f"{n}.png") for n in range(4)]})
    html = html_of(response)

    assert response.status_code == 200
    assert 'data-notice="stop"' in html
    assert "3 枚まで" in html
    assert "ファイルは選び直してください" in html
    assert Feedback.objects.count() == 0
    assert stored_files(media_root) == []

    response = client_logged_in.post("/feedback/new/", {**form_data(), "screenshots": [png("memo.pdf")]})
    assert response.status_code == 200
    assert "PNG・JPEG・WebP" in html_of(response)
    assert Feedback.objects.count() == 0


def test_edit_screen_adds_screenshots(client_logged_in, plain):
    row = services.create_feedback(form_data(), plain)
    edit = f"/feedback/{row.pk}/edit/"

    html = html_of(client_logged_in.get(edit))
    assert 'name="screenshots"' in html
    assert 'value="受注の一覧で並びが戻る"' in html

    response = client_logged_in.post(edit, {**form_data(title="直した題"), "screenshots": [png("a.png")]})
    assert response.status_code == 302
    assert response["Location"] == f"/feedback/{row.pk}/"
    row.refresh_from_db()
    assert row.title == "直した題"
    assert len(attached(row)) == 1


# ---- 2. 詳細の「画面の絵」の小さい絵 ----


def test_detail_thumbnails_images_only_and_panel_once(client_logged_in, plain):
    row = services.create_feedback(form_data(), plain, screenshots=[png("a.png")])
    shot = attached(row)[0]
    pdf = attachments_services.add_attachment(row, png("memo.pdf", b"%PDF-1.4"), plain, kind="other")

    html = html_of(client_logged_in.get(f"/feedback/{row.pk}/"))
    assert 'id="feedback-screenshots"' in html
    assert "画面の絵" in html
    assert f'<img src="/attachments/{shot.pk}/download/"' in html
    assert html.count(f"/attachments/{shot.pk}/download/") == 3
    assert html.count(f"/attachments/{pdf.pk}/download/") == 1
    assert html.count('id="attachments"') == 1

    bare = services.create_feedback(form_data(title="絵の無い題"), plain)
    html = html_of(client_logged_in.get(f"/feedback/{bare.pk}/"))
    assert 'id="feedback-screenshots"' not in html
    assert 'id="attachments"' in html


def test_panel_add_returns_to_feedback_detail(client_logged_in, plain):
    row = services.create_feedback(form_data(), plain)
    ct = ContentType.objects.get_for_model(Feedback).pk
    detail = f"/feedback/{row.pk}/"

    assert f"/attachments/new/?content_type={ct}&amp;object_id={row.pk}" in html_of(client_logged_in.get(detail))

    response = client_logged_in.post(
        "/attachments/new/", {"content_type": ct, "object_id": row.pk, "file": png("c.png"), "kind": "photo"}
    )
    shot = attached(row)[0]
    assert response.status_code == 302
    assert response["Location"] == f"/attachments/{shot.pk}/"
    response = client_logged_in.get(response["Location"])
    assert response.status_code == 302
    assert response["Location"] == detail

    html = html_of(client_logged_in.get(detail))
    section = html[html.index('id="feedback-screenshots"') :]
    assert f'<img src="/attachments/{shot.pk}/download/"' in section
    assert "c.png" in section


# ---- 3. Issue の文の末尾の 1 行 ----


def test_export_markdown_screenshot_line(client_logged_in, plain):
    masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, plain)
    row = services.create_feedback(form_data(), plain)
    before = services.export_markdown(row)
    assert "画面の絵" not in before

    attachments_services.add_attachment(row, png("memo.pdf", b"%PDF-1.4"), plain, kind="other")
    assert services.export_markdown(row) == before

    shot_row = services.create_feedback(
        form_data(title="絵のある題"), plain, screenshots=[png("作り物の得意先_5,000円.png"), png("b.png")]
    )
    text = services.export_markdown(shot_row)
    line = "画面の絵: 2 枚 (〇〇_〇〇.png・b.png。絵は世話役が Issue に手で付ける)"
    assert text.splitlines()[-1] == line
    assert "作り物の得意先" not in text
    assert "5,000" not in text

    html = html_of(client_logged_in.get(f"/feedback/{shot_row.pk}/"))
    issue = re.search(r'<textarea id="feedback-issue"[^>]*>(.*?)</textarea>', html, re.S).group(1)
    assert line in issue


# ---- 4. AI の下書きの知らせの 1 文 ----


def test_draft_ready_message_mentions_screenshots():
    assert chat.DRAFT_READY_MESSAGE.startswith("AI の下書きを作りました。内容を直して「保存」を押してください。")
    assert "画面の絵" in chat.DRAFT_READY_MESSAGE
