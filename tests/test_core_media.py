"""上げたファイルをログインした人にだけ返す口 (seizou.core.media。/media/<path>)。

見る所: ログインの確かめ、MEDIA_ROOT の外と無い物の 404、危ない型のダウンロード、日本語の名前、nosniff と保存させない
ヘッダー、配り方 (django / nginx) の違い、nginx の印が無いときの 503。
"""

from __future__ import annotations

import logging

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import media

PNG = b"\x89PNG\r\n\x1a\n"
PDF_NAME = "図面.pdf"


@pytest.fixture
def root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    drawings = settings.MEDIA_ROOT / "drawings"
    drawings.mkdir(parents=True)
    (drawings / "a.png").write_bytes(PNG)
    (drawings / "page.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (drawings / PDF_NAME).write_bytes(b"%PDF-1.4\n")
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    return settings.MEDIA_ROOT


@pytest.fixture
def logged_in(client, db):
    client.force_login(get_user_model().objects.create_user(username="tester", password="tests-only"))
    return client


@pytest.fixture
def nginx(settings):
    settings.SEIZOU_FILE_DELIVERY = "nginx"
    return {"HTTP_X_SEIZOU_PROXY": "nginx"}


def body_of(response) -> bytes:
    return b"".join(response.streaming_content) if response.streaming else response.content


# ---------------------------------------------------------------- 道と、ログイン


def test_url_is_under_media():
    assert reverse("core:file", args=["drawings/a.png"]) == "/media/drawings/a.png"


def test_anonymous_is_sent_to_login(client, db, root):
    response = client.get("/media/drawings/a.png")
    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_anonymous_is_sent_to_login_in_nginx_mode_too(client, db, root, nginx):
    response = client.get("/media/drawings/a.png", **nginx)
    assert response.status_code == 302


# ---------------------------------------------------------------- Django が送る (既定)


def test_logged_in_user_gets_the_file(logged_in, root):
    response = logged_in.get("/media/drawings/a.png")
    assert response.status_code == 200
    assert body_of(response) == PNG
    assert response["Content-Type"] == "image/png"
    assert response["Content-Disposition"] == 'inline; filename="a.png"'
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response["Cache-Control"]


def test_head_is_allowed(logged_in, root):
    assert logged_in.head("/media/drawings/a.png").status_code == 200


def test_post_is_refused(logged_in, root):
    assert logged_in.post("/media/drawings/a.png").status_code == 405


def test_missing_file_and_folder_are_404(logged_in, root):
    assert logged_in.get("/media/drawings/none.png").status_code == 404
    assert logged_in.get("/media/drawings").status_code == 404


def test_outside_media_root_is_404(logged_in, root):
    assert logged_in.get("/media/../outside.txt").status_code == 404
    assert logged_in.get("/media/drawings/../../outside.txt").status_code == 404


@pytest.mark.parametrize("name", ["", "/etc/passwd", "../x.png", "a/../../x.png", "a\\b.png", "a\x00b.png", "..", "a/.."])
def test_dangerous_paths_are_refused(name):
    assert media.relative_path(name) is None


@pytest.mark.parametrize(
    ("name", "kept"),
    [("drawings/a.png", "drawings/a.png"), ("a//b.png", "a/b.png"), ("./a.png", "a.png"), ("a/./b.png", "a/b.png")],
)
def test_plain_paths_are_kept(name, kept):
    # 「.」と重なった / は、害が無いので整えるだけ (MEDIA_ROOT の中に収まる)。
    assert media.relative_path(name).as_posix() == kept


def test_script_like_types_are_always_downloads(logged_in, root):
    response = logged_in.get("/media/drawings/page.html")
    assert response.status_code == 200
    assert response["Content-Disposition"] == 'attachment; filename="page.html"'
    assert response["Content-Type"] == "text/html"


@pytest.mark.parametrize(
    ("name", "content_type", "download"),
    [
        ("a.svg", "image/svg+xml", True),
        ("a.SVG", "image/svg+xml", True),
        ("a.xml", "application/xml", True),
        ("a.js", "text/javascript", True),
        ("a.bin", "text/html; charset=utf-8", True),
        ("a.png", "image/png", False),
        ("a.pdf", "application/pdf", False),
    ],
)
def test_must_download_table(name, content_type, download):
    assert media.must_download(name, content_type) is download


def test_japanese_file_name_is_encoded(logged_in, root):
    response = logged_in.get(f"/media/drawings/{PDF_NAME}")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == "inline; filename*=utf-8''%E5%9B%B3%E9%9D%A2.pdf"


# ---------------------------------------------------------------- nginx に渡す


def test_nginx_mode_without_proxy_mark_is_503(logged_in, root, nginx, caplog):
    with caplog.at_level(logging.ERROR, logger="seizou.media"):
        response = logged_in.get("/media/drawings/a.png")
    assert response.status_code == 503
    page = response.content.decode()
    assert "ファイルを返せません" in page
    assert "deploy/nginx-files.conf" in page
    assert "proxy_set_header X-Seizou-Proxy nginx;" in page
    # Django の django.request も「Service Unavailable」を 1 行出すので、この口の記録だけを見る。
    ours = [record for record in caplog.records if record.name == "seizou.media"]
    assert [record.levelname for record in ours] == ["ERROR"]
    assert "X-Seizou-Proxy" in ours[0].getMessage()


def test_nginx_mode_hands_the_file_to_nginx(logged_in, root, nginx):
    response = logged_in.get("/media/drawings/a.png", **nginx)
    assert response.status_code == 200
    assert response.content == b""
    assert response["X-Accel-Redirect"] == "/seizou-files/drawings/a.png"
    assert response["Content-Type"] == "image/png"
    assert response["Content-Disposition"] == 'inline; filename="a.png"'
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response["Cache-Control"]


def test_nginx_mode_encodes_japanese_path(logged_in, root, nginx):
    response = logged_in.get(f"/media/drawings/{PDF_NAME}", **nginx)
    assert response["X-Accel-Redirect"] == "/seizou-files/drawings/%E5%9B%B3%E9%9D%A2.pdf"


def test_nginx_mode_downloads_script_like_types(logged_in, root, nginx):
    response = logged_in.get("/media/drawings/page.html", **nginx)
    assert response["Content-Disposition"] == 'attachment; filename="page.html"'


def test_nginx_mode_checks_the_file_before_handing_over(logged_in, root, nginx):
    assert logged_in.get("/media/drawings/none.png", **nginx).status_code == 404
    assert logged_in.get("/media/../outside.txt", **nginx).status_code == 404


def test_unknown_delivery_value_is_503(logged_in, root, settings):
    settings.SEIZOU_FILE_DELIVERY = "cdn"
    response = logged_in.get("/media/drawings/a.png")
    assert response.status_code == 503
    assert "django か nginx のどちらか" in response.content.decode()


def test_delivery_defaults_to_django(settings):
    settings.SEIZOU_FILE_DELIVERY = ""
    assert media.delivery() == "django"
    settings.SEIZOU_FILE_DELIVERY = " NGINX "
    assert media.delivery() == "nginx"
