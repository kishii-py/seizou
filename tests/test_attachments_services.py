"""添付ファイルの services と、ログインした人にだけファイルを返す口 (02 の 5 節の N30 の 1 段目)。"""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from seizou.attachments import services
from seizou.attachments.models import Attachment
from seizou.masters import services as masters_services

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def partner(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


def upload(name, content=PNG_BYTES):
    return SimpleUploadedFile(name, content)


def stored_files(root):
    return sorted(p for p in root.rglob("*") if p.is_file())


def body_of(response) -> bytes:
    # 読み切ると test client が close() を呼ぶ。自分で close() を重ねると request_finished がもう 1 度飛び、
    # PostgreSQL では試験の取引の接続が閉じて次の要求が落ちる (CI の postgres の 7 本)。SQLite のメモリの DB は閉じない。
    return b"".join(response.streaming_content)


@pytest.mark.django_db
def test_add_creates_row_and_file(item, user, media_root):
    attachment = services.add_attachment(item, upload("軸の写真.png"), user, kind="photo", note="正面")

    assert Attachment.objects.count() == 1
    attachment.refresh_from_db()
    assert attachment.target == item
    assert attachment.original_name == "軸の写真.png"
    assert attachment.mime == "image/png"
    assert attachment.size == len(PNG_BYTES)
    assert attachment.kind == "photo"
    assert attachment.note == "正面"
    assert attachment.uploaded_by == user
    assert attachment.file.name.startswith("attachments/")
    assert attachment.file.name.endswith(".png")
    assert "軸の写真" not in attachment.file.name
    assert attachment.file.storage.exists(attachment.file.name)
    assert stored_files(media_root) == [media_root / attachment.file.name]


@pytest.mark.django_db
def test_add_without_user_leaves_uploaded_by_empty(item):
    attachment = services.add_attachment(item, upload("a.png"), None, kind="other")

    assert attachment.uploaded_by is None


@pytest.mark.parametrize(
    ("name", "content", "kind", "note", "message"),
    [
        ("tool.exe", PNG_BYTES, "other", "", "ファイルの形式が違います"),
        ("drawing.svg", b"<svg/>", "other", "", "ファイルの形式が違います"),
        ("page.html", b"<p>x</p>", "other", "", "ファイルの形式が違います"),
        ("noextension", PNG_BYTES, "other", "", "ファイルの形式が違います"),
        ("empty.png", b"", "other", "", "ファイルが空です"),
        ("big.png", b"x" * 65, "other", "", "ファイルが 20MB を超えています"),
        ("n" * 97 + ".png", PNG_BYTES, "other", "", "ファイル名が 100 字を超えています"),
        ("a.png", PNG_BYTES, "drawing", "", "種類は写真・検査成績書・作業手順・その他"),
        ("a.png", PNG_BYTES, "other", "あ" * 501, "備考が 500 字を超えています"),
    ],
)
@pytest.mark.django_db
def test_add_refuses_bad_files(monkeypatch, item, user, media_root, name, content, kind, note, message):
    monkeypatch.setattr(services, "MAX_BYTES", 64)

    with pytest.raises(ValueError, match=message):
        services.add_attachment(item, upload(name, content), user, kind=kind, note=note)

    assert Attachment.objects.count() == 0
    assert stored_files(media_root) == []


@pytest.mark.django_db
def test_add_refuses_unsaved_target_and_attachment_itself(item, user, media_root):
    unsaved = type(item)(part_no="T-999", name="保存前の部品", unit="個")
    with pytest.raises(ValueError, match="まだ保存されていません"):
        services.add_attachment(unsaved, upload("a.png"), user, kind="other")

    attachment = services.add_attachment(item, upload("a.png"), user, kind="other")
    with pytest.raises(ValueError, match="付ける先が添付ファイルです"):
        services.add_attachment(attachment, upload("b.png"), user, kind="other")

    assert Attachment.objects.count() == 1
    assert len(stored_files(media_root)) == 1


@pytest.mark.parametrize(
    ("name", "mime"),
    [
        ("a.png", "image/png"),
        ("a.jpg", "image/jpeg"),
        ("a.JPEG", "image/jpeg"),
        ("a.webp", "image/webp"),
        ("検査成績書.PDF", "application/pdf"),
        ("a.tif", "image/tiff"),
        ("a.tiff", "image/tiff"),
        ("a.csv", "text/csv"),
        ("a.txt", "text/plain"),
        ("a.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ],
)
@pytest.mark.django_db
def test_mime_from_extension_table(item, user, name, mime):
    attachment = services.add_attachment(item, upload(name, b"content"), user, kind="other")

    assert attachment.mime == mime
    assert attachment.original_name == name
    assert attachment.file.name.endswith(name.rsplit(".", 1)[1].lower())


@pytest.mark.django_db
def test_attachments_for_is_per_record_newest_first(item, partner, user):
    # 部品と取引先は別の表なので、同じ番号でも混ざらない (SQLite の試験では、どちらも番号 1 になる)。
    first = services.add_attachment(item, upload("1.png"), user, kind="photo")
    second = services.add_attachment(item, upload("2.pdf"), user, kind="inspection")
    other = services.add_attachment(partner, upload("3.png"), user, kind="other")

    assert list(services.attachments_for(item)) == [second, first]
    assert list(services.attachments_for(partner)) == [other]
    assert list(services.attachments_for(type(item)(part_no="T-999", name="保存前", unit="個"))) == []


@pytest.mark.django_db
def test_remove_deletes_row_and_file(item, user, media_root):
    attachment = services.add_attachment(item, upload("a.png"), user, kind="photo")
    name = attachment.file.name
    assert attachment.file.storage.exists(name)

    returned = services.remove_attachment(attachment, user)

    assert returned == item
    assert Attachment.objects.count() == 0
    assert not attachment.file.storage.exists(name)
    assert stored_files(media_root) == []
    with pytest.raises(ValueError, match="この添付ファイルは既に削除されています"):
        services.remove_attachment(attachment, user)


@pytest.mark.django_db
def test_remove_skips_missing_file(item, user):
    attachment = services.add_attachment(item, upload("a.png"), user, kind="photo")
    attachment.file.storage.delete(attachment.file.name)

    assert services.remove_attachment(attachment, user) == item
    assert Attachment.objects.count() == 0


@pytest.mark.django_db
def test_download_requires_login(client, item, user):
    attachment = services.add_attachment(item, upload("a.png"), user, kind="photo")

    response = client.get(reverse("attachments:attachment_download", args=[attachment.pk]))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("core:login"))


@pytest.mark.parametrize(
    ("name", "mime", "disposition"),
    [
        ("検査成績書.pdf", "application/pdf", "inline"),
        ("a.png", "image/png", "inline"),
        ("a.csv", "text/csv", "attachment"),
        ("a.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "attachment"),
    ],
)
@pytest.mark.django_db
def test_download_inline_and_attachment(client_logged_in, item, user, name, mime, disposition):
    attachment = services.add_attachment(item, upload(name, b"content-of-file"), user, kind="other")

    response = client_logged_in.get(reverse("attachments:attachment_download", args=[attachment.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == mime
    assert response["Content-Disposition"].startswith(disposition)
    assert "no-store" in response["Cache-Control"]
    assert body_of(response) == b"content-of-file"
    if name == "検査成績書.pdf":
        assert "filename*=utf-8''" in response["Content-Disposition"]


@pytest.mark.django_db
def test_download_missing_file_is_404(client_logged_in, item, user):
    attachment = services.add_attachment(item, upload("a.png"), user, kind="photo")
    attachment.file.storage.delete(attachment.file.name)

    response = client_logged_in.get(reverse("attachments:attachment_download", args=[attachment.pk]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_download_unknown_pk_is_404(client_logged_in):
    response = client_logged_in.get(reverse("attachments:attachment_download", args=[999]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_file_url_is_download_url(item, user):
    attachment = services.add_attachment(item, upload("a.png"), user, kind="photo")

    url = attachment.file_url("file")

    assert url == reverse("attachments:attachment_download", args=[attachment.pk])
    assert url == f"/attachments/{attachment.pk}/download/"
    assert url != attachment.file.url
    assert attachment.file_url("note") == ""
