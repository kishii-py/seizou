"""N173 の試験 (決め 11-06 00:0x)。作り物の絵と作り物の置き場だけ。

- 命令 seizou_company_icons がロゴから印の 2 つを作り直して表を書かないこと。ロゴが無いか開けないときは 1 行と終わりの番号 2。
- 土台の head の印の URL に ?v=<32 の印の更新時刻 (秒) の 16 進> が付き、作り直すと字が変わること (/favicon.ico は付けない)。
- 点検 (seizou_doctor) の「AI が守る決まり」の行は、CLAUDE.md と AGENTS.md が無くても良いの行のまま (止めない) こと。
置き場 (MEDIA_ROOT と BASE_DIR) は試験ごとの一時のフォルダ。
"""

from __future__ import annotations

import io
import os

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from PIL import Image

from seizou.core import doctor, services
from seizou.core.management.commands.seizou_company_icons import NO_LOGO
from seizou.core.models import Company
from tests.test_core_company_favicon import ICON, TOUCH, png_upload

#: 表に書く SQL の頭 (命令は 1 つも打たない)
WRITES = ("INSERT", "UPDATE", "DELETE")
#: 作り物の古い時刻 (2023-11-14) と、その 16 進の字
OLD_TIME = 1_700_000_000
OLD_VERSION = "?v=6553f100"


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def run_icons() -> tuple[int, str, list[str]]:
    """命令を打ち、終わりの番号と出力と、その間に打った表に書く SQL を返す。"""
    out = io.StringIO()
    with CaptureQueriesContext(connection) as queries:
        try:
            call_command("seizou_company_icons", stdout=out)
            code = 0
        except CommandError as exc:
            out.write(str(exc))
            code = exc.returncode
    writes = [query["sql"] for query in queries.captured_queries if query["sql"].lstrip().upper().startswith(WRITES)]
    return code, out.getvalue().strip(), writes


@pytest.mark.django_db
def test_command_remakes_icons_from_logo_without_writing_rows(media):
    services.update_company(services.company(), {"logo": png_upload()}, None)
    for name in (ICON, TOUCH):
        (media / name).unlink()  # N166 より前にロゴを上げた置き場 (印が無い)

    code, out, writes = run_icons()

    assert code == 0, out
    assert out == "印を作りました: company/favicon.png・company/apple-touch-icon.png"
    assert writes == []
    for name, side in ((ICON, 32), (TOUCH, 180)):
        with Image.open(media / name) as icon:
            assert icon.size == (side, side), name
    assert Company.objects.get().logo.name == "company/logo.png"  # ロゴはそのまま


@pytest.mark.django_db
def test_command_without_logo_says_so_and_exits_two(media):
    code, out, writes = run_icons()  # 会社の行が無い (作らない)
    assert (code, out) == (2, NO_LOGO)
    assert writes == [] and not Company.objects.exists()

    services.company()  # 会社の行はあるが、ロゴが無い
    code, out, writes = run_icons()
    assert (code, out) == (2, NO_LOGO)
    assert writes == [] and not (media / "company").exists()


@pytest.mark.django_db
def test_command_with_unreadable_logo_exits_two(media):
    svg = SimpleUploadedFile("ロゴ.svg", b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="3"></svg>')
    services.update_company(services.company(), {"logo": svg}, None)

    code, out, writes = run_icons()

    assert code == 2
    assert out.startswith("印を作れません。") and "company/logo.svg" in out
    assert writes == []
    assert not (media / ICON).exists() and not (media / TOUCH).exists()


@pytest.mark.django_db
def test_icon_urls_carry_version_and_remaking_changes_it(media):
    services.update_company(services.company(), {"logo": png_upload()}, None)
    os.utime(media / ICON, (OLD_TIME, OLD_TIME))  # 前に作った印 (作り物の古い時刻)

    icons = services.company_icons(services.company())
    assert icons == {"icon": "/media/company/favicon.png" + OLD_VERSION, "touch": "/media/company/apple-touch-icon.png" + OLD_VERSION}
    assert services.company_icons(services.company()) == icons  # 同じファイルなら同じ字
    anonymous = Client()
    body = anonymous.get(reverse("core:login")).content.decode()
    assert f'<link rel="icon" href="/media/company/favicon.png{OLD_VERSION}" type="image/png">' in body
    assert f'<link rel="apple-touch-icon" href="/media/company/apple-touch-icon.png{OLD_VERSION}">' in body
    assert anonymous.get("/media/" + ICON + OLD_VERSION).status_code == 200  # ?v= が付いても、ログインの前に返す
    assert anonymous.get("/favicon.ico")["Location"] == "/media/company/favicon.png"  # /favicon.ico の口は変えない

    assert run_icons()[0] == 0  # 作り直すと、字が今の更新時刻に変わる
    version = f"?v={int((media / ICON).stat().st_mtime):x}"
    assert version != OLD_VERSION
    assert services.company_icons(services.company())["touch"] == "/media/company/apple-touch-icon.png" + version


@pytest.mark.django_db
def test_doctor_ai_docs_row_advises_but_never_stops(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    [row] = doctor.check_ai_docs()
    assert (row.ok, row.label) == (True, "AI が守る決まり")
    assert row.detail == f"AI が守る決まり (CLAUDE.md・AGENTS.md) がありません。seizou ai-docs {tmp_path} で置けます。"
    assert any(row.label == "AI が守る決まり" and row.ok for row in doctor.run(production=True))  # 本番の条件でも止めない

    (tmp_path / "CLAUDE.md").write_text("# 作り物の決まり\n", encoding="utf-8")
    [row] = doctor.check_ai_docs()
    assert row.ok and row.detail.startswith("AGENTS.md がありません。") and "--force" in row.detail

    (tmp_path / "AGENTS.md").write_text("# 作り物の決まりの短い版\n", encoding="utf-8")
    assert [(row.ok, row.detail) for row in doctor.check_ai_docs()] == [(True, "CLAUDE.md と AGENTS.md がある")]
