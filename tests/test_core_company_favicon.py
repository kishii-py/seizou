"""会社のロゴから作る印 (favicon) の試験 (決め 11-05 20:1x。N166)。作り物の絵だけ。

services の update_company がロゴから company/favicon.png (32) と company/apple-touch-icon.png (180) を作り、クリアで消すこと、
Pillow で開けないロゴでは作らずロゴは保存すること、土台の head の印の行と /favicon.ico の口が
会社の印があればそれを、無ければ static の favicon.svg を指すことを見る。
置き場 (MEDIA_ROOT) は試験ごとの一時のフォルダ (tests/settings.py の置き場は試験の間で共有なので使わない)。
"""

from __future__ import annotations

import io
import logging

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from seizou.core import services
from seizou.core.models import Company
from seizou.core.templatetags.seizou_static import stamped_static

ICON = "company/favicon.png"
TOUCH = "company/apple-touch-icon.png"


def svg_line():
    """static の favicon.svg を指す head の行 (URL には中身の印 ?v= が付く。N442)。"""
    return f'<link rel="icon" href="{stamped_static("favicon.svg")}" type="image/svg+xml">'


@pytest.fixture
def admin(db):
    return get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def png_upload(size=(40, 20), mode="RGB", color=(30, 120, 200), name="ロゴ.png") -> SimpleUploadedFile:
    """試験用の横長の PNG (作り物)。"""
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def settings_messages(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "seizou.settings"]


def test_logo_makes_square_icons_and_clear_removes_them(admin, media):
    services.update_company(services.company(), {"logo": png_upload()}, admin)
    for name, side in ((ICON, 32), (TOUCH, 180)):
        with Image.open(media / name) as icon:
            assert icon.format == "PNG" and icon.size == (side, side), name
            assert icon.convert("RGBA").getpixel((0, 0)) == (255, 255, 255, 255), name  # 透明の無いロゴの余白は白
    first = (media / ICON).read_bytes()

    # 上げ直すと同じ名前で上書きする (名前に印の字が付かない)
    services.update_company(services.company(), {"logo": png_upload(mode="RGBA", color=(30, 120, 200, 255))}, admin)
    assert sorted(path.name for path in (media / "company").glob("*icon*")) == ["apple-touch-icon.png", "favicon.png"]
    with Image.open(media / ICON) as icon:
        assert icon.getpixel((0, 0))[3] == 0  # 透明を持つロゴの余白は透明
    assert (media / ICON).read_bytes() != first

    services.update_company(services.company(), {"logo": False}, admin)  # 「クリア」
    assert not (media / ICON).exists() and not (media / TOUCH).exists()
    assert services.company_icons(services.company()) is None


def test_unreadable_logo_is_saved_without_icons(admin, media, caplog):
    caplog.set_level(logging.INFO, logger="seizou.settings")
    services.update_company(services.company(), {"logo": png_upload()}, admin)
    svg = SimpleUploadedFile("ロゴ.svg", b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="3"></svg>')

    services.update_company(services.company(), {"logo": svg}, admin)
    assert Company.objects.get().logo.name.endswith(".svg")  # ロゴは今のまま保存する
    assert not (media / ICON).exists() and not (media / TOUCH).exists()  # 前のロゴの印も残さない
    messages = settings_messages(caplog)
    assert any("印は作らなかった" in message for message in messages)
    assert "会社の情報を変えた: ロゴ" in messages[-1]


def test_head_points_to_company_icons_or_svg(admin, media):
    anonymous = Client()
    body = anonymous.get(reverse("core:login")).content.decode()
    assert svg_line() in body and 'rel="apple-touch-icon"' not in body
    assert 'href="/static/favicon.svg?v=' in body

    services.update_company(services.company(), {"logo": png_upload()}, admin)
    body = anonymous.get(reverse("core:login")).content.decode()
    assert '<link rel="icon" href="/media/company/favicon.png?v=' in body  # 字は test_core_company_icons.py が見る (N173)
    assert '<link rel="apple-touch-icon" href="/media/company/apple-touch-icon.png?v=' in body
    assert body.count('rel="icon"') == 1 and svg_line() not in body and "/static/favicon.svg" not in body
    # ログインの前のタブにも出すので、印の 2 つはログインしていなくても返す
    for name in (ICON, TOUCH):
        response = anonymous.get("/media/" + name)
        assert response.status_code == 200 and response["Content-Type"] == "image/png", name

    manager = Client()
    manager.force_login(admin)
    printed = manager.get(reverse("core:manual") + "?print=1").content.decode()
    assert '<link rel="icon" href="/media/company/favicon.png?v=' in printed


def test_favicon_ico_follows_company_icon(admin, media):
    assert Client().get("/favicon.ico")["Location"] == "/static/favicon.svg"
    services.update_company(services.company(), {"logo": png_upload()}, admin)
    response = Client().get("/favicon.ico")
    assert response.status_code == 302 and response["Location"] == "/media/company/favicon.png"
    services.update_company(services.company(), {"logo": False}, admin)
    assert Client().get("/favicon.ico")["Location"] == "/static/favicon.svg"
