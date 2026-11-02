"""マニュアルのページ (seizou.core.manual) と、会社の置き場の manual/ の断片の約束。

断片の字は作り物。試験ごとに settings.BASE_DIR を一時の置き場に替え、その下の manual/ に書く。
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core.manual import fragment_name
from seizou.core.screens import all_screens

ITEM_FRAGMENT = "<section><h2>部品マスター</h2><p>作り物の説明の字</p></section>"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def site(settings, tmp_path):
    """会社の置き場 (一時の置き場)。manual/ はまだ作らない。"""
    settings.BASE_DIR = tmp_path
    return tmp_path


@pytest.fixture
def manual(site):
    """会社の置き場の manual/ (空)。"""
    folder = site / "manual"
    folder.mkdir()
    return folder


def write_fragment(folder, name, text):
    (folder / f"{name}.html").write_text(text, encoding="utf-8")


def manual_page(client, **params):
    return client.get(reverse("core:manual"), params)


def toc_anchors(html: str) -> list[str]:
    """目次 (nav aria-label="目次") の中の link の飛び先の並び。"""
    nav = re.search(r'<nav aria-label="目次"[^>]*>(.*?)</nav>', html, re.S)
    assert nav, "目次の nav が無い"
    return re.findall(r'href="#([^"]*)"', nav.group(1))


def section_box(html: str, anchor: str) -> str:
    """本文の 1 画面の箱 (id="<anchor>" の div) の中身。"""
    box = re.search(rf'<div id="{re.escape(anchor)}"[^>]*>(.*?)</div>', html, re.S)
    assert box, f"{anchor} の箱が無い"
    return box.group(1)


def toc_line(html: str, anchor: str) -> str:
    nav = re.search(r'<nav aria-label="目次"[^>]*>(.*?)</nav>', html, re.S)
    line = re.search(rf'<a [^>]*href="#{re.escape(anchor)}"[^>]*>(.*?)</a>', nav.group(1), re.S)
    assert line, f"目次に {anchor} の行が無い"
    return line.group(1)


@pytest.mark.django_db
def test_manual_requires_login(client, site):
    response = manual_page(client)
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


def test_topbar_has_manual_button(client_logged_in, site):
    html = client_logged_in.get(reverse("core:home")).content.decode()
    assert 'href="/manual/"' in html
    assert 'aria-label="マニュアル"' in html


def test_manual_in_screen_catalog():
    rows = [s for s in all_screens() if s.url_name == "core:manual"]
    assert len(rows) == 1
    assert rows[0].name == "マニュアル"
    assert rows[0].path == "/manual/"


def test_toc_follows_screen_catalog(client_logged_in, manual):
    response = manual_page(client_logged_in)
    assert response.status_code == 200
    expected = [fragment_name(s.url_name) for s in all_screens()]
    assert toc_anchors(response.content.decode()) == expected
    assert "core_manual" in expected


def test_fragment_is_shown(client_logged_in, manual):
    write_fragment(manual, "masters_item_list", ITEM_FRAGMENT)
    html = manual_page(client_logged_in).content.decode()
    assert ITEM_FRAGMENT in section_box(html, "masters_item_list")
    assert "&lt;h2&gt;" not in html
    assert "(まだ)" not in toc_line(html, "masters_item_list")


def test_missing_fragment_shows_placeholder(client_logged_in, manual):
    write_fragment(manual, "masters_item_list", ITEM_FRAGMENT)
    html = manual_page(client_logged_in).content.decode()
    box = section_box(html, "masters_partner_list")
    assert "<h2>取引先</h2>" in box
    assert "この画面の説明はまだありません" in box
    assert "/seizou:manual" in box
    assert "(まだ)" in toc_line(html, "masters_partner_list")


def test_index_fragment_comes_first(client_logged_in, manual):
    write_fragment(manual, "index", "<section><h2>作り物の前書き</h2><p>読み方の字</p></section>")
    html = manual_page(client_logged_in).content.decode()
    first = fragment_name(all_screens()[0].url_name)
    intro_at = html.index("作り物の前書き")
    assert intro_at < html.index(f'<div id="{first}"')
    assert intro_at > html.index('<nav aria-label="目次"')


def test_no_manual_folder_is_not_error(client_logged_in, site):
    assert not (site / "manual").exists()
    response = manual_page(client_logged_in)
    assert response.status_code == 200
    html = response.content.decode()
    assert "この会社の説明書はまだありません" in html
    assert "この画面の説明はまだありません" in section_box(html, "core_home")


def test_unreadable_fragment_is_reported(client_logged_in, manual):
    data = "<section><h2>部品マスター</h2><p>作り物の説明の字</p></section>".encode("shift_jis")
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    (manual / "masters_item_list.html").write_bytes(data)
    response = manual_page(client_logged_in)
    assert response.status_code == 200
    box = section_box(response.content.decode(), "masters_item_list")
    assert "UTF-8 で読めませんでした" in box
    assert "/seizou:manual" in box
