"""業務マニュアルの PDF (A4 の帯とページ番号)。本物の WeasyPrint は呼ばない (render_pdf を差し替える)。"""

import io

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from seizou.core import reports
from seizou.manuals import services


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


@pytest.fixture
def manual(user):
    folder = services.create_folder(None, "段取り", "blue", user)
    manual = services.create_manual(folder, "段取り替えの手順", "steps", user)
    buffer = io.BytesIO()
    Image.new("RGB", (4, 3), (30, 120, 200)).save(buffer, format="PNG")
    image = services.add_image(manual, SimpleUploadedFile("写真.png", buffer.getvalue()), user)
    return services.update_manual(manual, {"body": f"## 準備\n\n![写真](image:{image.pk})\n\n## 片付け"}, user)


def test_pdf_requires_login(client, manual):
    response = client.get(reverse("manuals:manual_pdf", args=[manual.pk]))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("core:login"))


def test_pdf_returns_pdf_when_available(client_logged_in, manual, monkeypatch):
    seen = {}

    def fake_render_pdf(template, context):
        seen["template"] = template
        seen["context"] = context
        return b"%PDF-1.4 test"

    monkeypatch.setattr(reports, "render_pdf", fake_render_pdf)
    response = client_logged_in.get(reverse("manuals:manual_pdf", args=[manual.pk]))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == f'inline; filename="manual-{manual.pk}-v1.pdf"'
    assert "no-store" in response["Cache-Control"]
    assert response.content == b"%PDF-1.4 test"
    assert seen["template"] == "manuals/manual_print.html"
    assert seen["context"]["pdf"] is True
    assert [f.name for f in seen["context"]["path"]] == ["段取り"]


def test_pdf_falls_back_to_html_when_unavailable(client_logged_in, manual, monkeypatch):
    def unavailable(template, context):
        raise reports.PdfUnavailable("PDF の部品が使えません。")

    monkeypatch.setattr(reports, "render_pdf", unavailable)
    response = client_logged_in.get(reverse("manuals:manual_pdf", args=[manual.pk]))
    assert response.status_code == 200
    html = response.content.decode()
    assert "PDF の準備がありません" in html
    assert "段取り替えの手順" in html
    assert "版 1" in html
    assert "作った日時 " + timezone.localtime(manual.created_at).strftime("%Y-%m-%d %H:%M") in html
    assert "フォルダ 段取り" in html
    image = manual.images.get()
    assert f'src="{reverse("manuals:manualimage_download", args=[image.pk])}"' in html
    assert "/media/" not in html
    assert "目次" in html


def test_print_html_has_page_counter(manual):
    html = reports.render_html("manuals/manual_print.html", {"manual": manual, "path": [], "pdf": True})
    assert "counter(page)" in html and "counter(pages)" in html
    image = manual.images.get()
    assert f'src="/media/{image.file.name}"' in html
    assert "フォルダ トップ" in html
    assert "csrf" not in html.lower()
