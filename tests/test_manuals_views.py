"""業務マニュアルの画面 (core の土台の上)。Django の test client で HTML と表の状態を見る。"""

import io
import re

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from seizou.manuals import services
from seizou.manuals.models import Manual, ManualFolder, ManualImage

DANGEROUS = "<script>alert(1)</script>\n\n[x](javascript:alert(1))"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def png_upload(name="治具の写真.png") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 3), (30, 120, 200)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


@pytest.fixture
def manual(user):
    return services.create_manual(None, "段取り替えの手順", "steps", user)


def test_pages_require_login(client, manual):
    image = services.add_image(manual, png_upload(), None)
    login = reverse("core:login")
    for url in (
        reverse("manuals:manual_list"),
        reverse("manuals:manual_detail", args=[manual.pk]),
        reverse("manuals:manual_update", args=[manual.pk]),
        reverse("manuals:manualimage_download", args=[image.pk]),
        reverse("manuals:manualfolder_list"),
    ):
        response = client.get(url)
        assert response.status_code == 302, url
        assert response["Location"].startswith(login)
    for url in (
        reverse("manuals:manual_preview", args=[manual.pk]),
        reverse("manuals:manualimage_upload", args=[manual.pk]),
    ):
        response = client.post(url, {"body": "## 手順"})
        assert response.status_code == 302, url
        assert response["Location"].startswith(login)
    assert ManualImage.objects.count() == 1


def test_list_root_and_folder(client_logged_in, user):
    top = services.create_folder(None, "段取り", "blue", user)
    child = services.create_folder(top, "旋盤", "red", user)
    services.protect_folder(child, True, user)
    services.create_manual(None, "ルートの手順", "steps", user)
    services.create_manual(top, "段取りの手順", "steps", user)
    root = client_logged_in.get(reverse("manuals:manual_list"))
    assert root.status_code == 200
    html = root.content.decode()
    assert "段取り" in html and "ルートの手順" in html
    assert "段取りの手順" not in html
    assert reverse("manuals:manualfolder_detail", args=[top.pk]) in html
    assert 'href="/manuals/"' in html and "マニュアル" in html  # 左のメニュー
    inside = client_logged_in.get(reverse("manuals:manual_list"), {"folder": top.pk}).content.decode()
    assert "段取りの手順" in inside and "ルートの手順" not in inside
    assert '<nav aria-label="フォルダ"' in inside and "旋盤" in inside and "保護中" in inside
    assert 'class="status status-info"' in inside and 'class="status status-error"' in inside
    assert f"?parent={top.pk}" in inside and f"?folder={top.pk}" in inside
    assert client_logged_in.get(reverse("manuals:manual_list"), {"folder": "999999"}).status_code == 404
    assert client_logged_in.get(reverse("manuals:manual_list"), {"folder": "abc"}).status_code == 404
    opened = client_logged_in.get(reverse("manuals:manualfolder_detail", args=[top.pk]))
    assert opened.status_code == 302 and opened["Location"] == f"/manuals/?folder={top.pk}"
    assert client_logged_in.get(reverse("manuals:manualfolder_detail", args=[999999])).status_code == 404
    back = client_logged_in.get(reverse("manuals:manualfolder_list"))
    assert back.status_code == 302 and back["Location"] == "/manuals/"
    empty = client_logged_in.get(reverse("manuals:manual_list"), {"folder": child.pk}).content.decode()
    assert "フォルダはありません。" in empty


def test_list_search_across_folders(client_logged_in, user):
    top = services.create_folder(None, "出荷", "green", user)
    deep = services.create_folder(services.create_folder(top, "梱包", "gray", user), "箱", "gray", user)
    found = services.create_manual(deep, "箱詰めの手順", "steps", user)
    services.update_manual(found, {"body": "緩衝材を入れる"}, user)
    by_title = client_logged_in.get(reverse("manuals:manual_list"), {"q": "箱詰め"}).content.decode()
    assert "箱詰めの手順" in by_title and "出荷 / 梱包 / 箱" in by_title
    by_body = client_logged_in.get(reverse("manuals:manual_list"), {"q": "緩衝材"}).content.decode()
    assert "箱詰めの手順" in by_body


def test_create_folder_then_manual(client_logged_in, user):
    top = services.create_folder(None, "段取り", "blue", user)
    form = client_logged_in.get(reverse("manuals:manualfolder_create"), {"parent": top.pk})
    assert form.status_code == 200 and f'value="{top.pk}"' in form.content.decode()
    response = client_logged_in.post(
        reverse("manuals:manualfolder_create"), {"parent": top.pk, "name": "旋盤", "color": "red"}
    )
    child = ManualFolder.objects.get(name="旋盤")
    assert child.parent == top and child.color == "red"
    assert response.status_code == 302
    assert response["Location"] == reverse("manuals:manualfolder_detail", args=[child.pk])
    missing = client_logged_in.post(
        reverse("manuals:manualfolder_create"), {"parent": 999999, "name": "迷子", "color": "gray"}
    )
    assert missing.status_code == 200
    assert "フォルダが見つかりません。一覧から開き直してください。" in missing.content.decode()
    assert "置き場" not in missing.content.decode()
    page = client_logged_in.get(reverse("manuals:manual_create"), {"folder": child.pk}).content.decode()
    assert re.search(rf'<option value="{child.pk}" selected>段取り / 旋盤</option>', page)
    response = client_logged_in.post(
        reverse("manuals:manual_create"), {"folder": child.pk, "title": "刃物の交換", "format": "steps"}
    )
    manual = Manual.objects.get(title="刃物の交換")
    assert manual.folder == child and manual.created_by == user and manual.version == 1
    assert response["Location"] == reverse("manuals:manual_detail", args=[manual.pk])


def test_edit_save_and_preview(client_logged_in, manual):
    url = reverse("manuals:manual_update", args=[manual.pk])
    page = client_logged_in.get(url)
    assert page.status_code == 200
    html = page.content.decode()
    assert html.count("data-insert=") == 11
    assert reverse("manuals:manual_preview", args=[manual.pk]) in html
    assert f'formaction="{url}"' in html
    response = client_logged_in.post(
        url, {"title": "段取り替えの手順", "format": "steps", "folder": "", "body": "## 準備\n\n結果は {OK}"}
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("manuals:manual_detail", args=[manual.pk])
    manual.refresh_from_db()
    assert manual.body == "## 準備\n\n結果は {OK}"
    preview_url = reverse("manuals:manual_preview", args=[manual.pk])
    preview = client_logged_in.post(preview_url, {"body": "{NG} の時", "format": "steps"}, HTTP_HX_REQUEST="true")
    assert preview.status_code == 200
    assert '<span class="badge badge-sm badge-error whitespace-nowrap">NG</span>' in preview.content.decode()
    manual.refresh_from_db()
    assert manual.body == "## 準備\n\n結果は {OK}"
    too_long = client_logged_in.post(preview_url, {"body": "あ" * 100_001, "format": "steps"}, HTTP_HX_REQUEST="true")
    assert too_long.status_code == 200
    text = too_long.content.decode()
    assert "100,000 字を超えています" in text and 'role="alert"' in text and "badge" not in text
    refused = client_logged_in.post(url, {"title": "題", "format": "steps", "folder": "", "body": "あ" * 100_001})
    assert refused.status_code == 200 and "100,000 字を超えています" in refused.content.decode()


def test_detail_shows_numbered_steps_and_toc(client_logged_in, manual, user):
    services.update_manual(manual, {"body": "## 準備\n\n手順の字\n\n## 片付け\n\n" + DANGEROUS}, user)
    html = client_logged_in.get(reverse("manuals:manual_detail", args=[manual.pk])).content.decode()
    assert '<span class="mr-2 badge badge-primary">1</span>準備' in html
    assert 'href="#step-1"' in html and 'href="#step-2"' in html
    assert 'id="step-1"' in html
    assert "## " not in html
    assert "<script>alert" not in html and "javascript:" not in html
    assert "版 1" in html and "ステップ形式" in html
    empty = services.create_manual(None, "空の手順", "page", user)
    blank = client_logged_in.get(reverse("manuals:manual_detail", args=[empty.pk])).content.decode()
    assert "まだ本文がありません。" in blank and 'aria-label="目次"' not in blank


def test_image_upload_download_delete(client, client_logged_in, manual, media_root):
    upload_url = reverse("manuals:manualimage_upload", args=[manual.pk])
    response = client_logged_in.post(upload_url, {"file": png_upload()}, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    image = ManualImage.objects.get()
    html = response.content.decode()
    assert f"image:{image.pk}" in html and "本文に入れる" in html
    assert 'id="manual-images"' in html
    download_url = reverse("manuals:manualimage_download", args=[image.pk])
    got = client_logged_in.get(download_url)
    assert got.status_code == 200
    assert got["Content-Type"] == "image/png"
    assert got["Content-Disposition"].startswith("inline")
    assert "no-store" in got["Cache-Control"]
    b"".join(got.streaming_content)  # 読み切って閉じる (close() を自分で呼ばない。PostgreSQL で接続が閉じるため)
    for name in ("manual_update", "manual_detail"):
        page = client_logged_in.get(reverse(f"manuals:{name}", args=[manual.pk])).content.decode()
        assert "/media/" not in page
    client_logged_in.logout()
    assert client.get(download_url).status_code == 302
    client.force_login(manual.created_by)
    image.file.storage.delete(image.file.name)
    assert client.get(download_url).status_code == 404
    assert client.get(reverse("manuals:manualimage_download", args=[999999])).status_code == 404
    second = services.add_image(manual, png_upload("二枚目.png"), None)
    path = media_root / second.file.name
    assert path.exists()
    deleted = client.post(reverse("manuals:manualimage_delete", args=[second.pk]), HTTP_HX_REQUEST="true")
    assert deleted.status_code == 200
    assert not ManualImage.objects.filter(pk=second.pk).exists()
    assert not path.exists()


def test_upload_errors_are_shown(client_logged_in, manual):
    upload_url = reverse("manuals:manualimage_upload", args=[manual.pk])
    svg = client_logged_in.post(
        upload_url, {"file": SimpleUploadedFile("drawing.svg", b"<svg></svg>")}, HTTP_HX_REQUEST="true"
    )
    assert svg.status_code == 200
    assert "画像の形式が違います" in svg.content.decode()
    empty = client_logged_in.post(upload_url, {}, HTTP_HX_REQUEST="true")
    assert empty.status_code == 200
    assert "画像を選んでください。" in empty.content.decode()
    assert ManualImage.objects.count() == 0
