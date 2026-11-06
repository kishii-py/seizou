"""画面と枠の「?」が開く小窓の中身の口 (seizou.core.manual.manual_fragment)。

断片の字は作り物。試験ごとに settings.BASE_DIR を一時の置き場に替え、その下の manual/ に書く
(fixture は tests/test_core_manual.py の形を写した物。既にある試験は import しない)。
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import manual as core_manual
from seizou.core.manual import fragment_name
from seizou.core.screens import all_screens

ITEM_FRAGMENT = "<section><h2>部品マスター</h2><p>作り物の説明の字</p></section>"

FRAME_FRAGMENT = (
    '<section><h2>部品マスター</h2><p>概要</p><h3 id="basic">基本情報</h3><p>作り物の基本の字</p>'
    '<h3 id="related">関係する物</h3><p>作り物の関係の字</p></section>'
)

HTMX = {"HTTP_HX_REQUEST": "true"}


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


def fragment_url(anchor: str) -> str:
    return reverse("core:manual_fragment", args=[anchor])


def modal(client, anchor="masters_item_list", **params):
    response = client.get(fragment_url(anchor), params, **HTMX)
    assert response.status_code == 200
    return response


# ---------------------------------------------------------------- 1 段目: 口と小窓の中身


@pytest.mark.django_db
def test_fragment_requires_login(client, site):
    response = client.get(fragment_url("masters_item_list"), **HTMX)
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


def test_fragment_is_not_a_screen(client_logged_in, manual):
    screens = all_screens()
    assert not [s for s in screens if s.url_name == "core:manual_fragment"]
    anchors = [fragment_name(s.url_name) for s in screens]
    assert "core_manual_fragment" not in anchors
    assert "core_manual" in anchors
    # 使い方のページの目次の並び (tests/test_core_manual.py の test_toc_follows_screen_catalog の期待) と同じ = 目録に口が混ざっていない
    # 目次の頭は、業務の流れがあれば flows の 1 行 (置き場の manual/ は空で前書きが無い。依頼書 N265 = 候補 C48 の 1 の 2)
    html = client_logged_in.get(reverse("core:manual")).content.decode()
    nav = re.search(r'<nav aria-label="目次"[^>]*>(.*?)</nav>', html, re.S)
    assert nav, "目次の nav が無い"
    head = ["flows"] if core_manual.manual_flows() else []
    assert re.findall(r'href="#([^"]*)"', nav.group(1)) == [*head, *anchors]


def test_modal_shows_fragment_with_title_and_links(client_logged_in, manual):
    write_fragment(manual, "masters_item_list", ITEM_FRAGMENT)
    response = modal(client_logged_in)
    html = response.content.decode()
    assert '<h3 class="min-w-0 truncate text-lg font-bold" title="部品マスター">部品マスター</h3>' in html
    assert "<p>作り物の説明の字</p>" in html
    assert "<h2" not in html
    assert 'href="/manual/#masters_item_list"' in html
    assert "使い方の全部を開く" in html
    assert "閉じる" in html
    assert "btn-primary" not in html
    assert "<html" not in html
    assert "no-store" in response["Cache-Control"]


def test_missing_fragment_sentence(client_logged_in, manual):
    html = modal(client_logged_in).content.decode()
    assert core_manual.MISSING_SENTENCE in html
    assert "この画面の説明書はまだありません" in html
    assert "/seizou:manual" not in html


def test_missing_manual_folder_sentence(client_logged_in, site):
    assert not (site / "manual").exists()
    html = modal(client_logged_in).content.decode()
    assert "この画面の説明書はまだありません" in html
    assert "/seizou:manual" not in html


def test_unreadable_fragment_sentence(client_logged_in, manual):
    data = ITEM_FRAGMENT.encode("shift_jis")
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    (manual / "masters_item_list.html").write_bytes(data)
    html = modal(client_logged_in).content.decode()
    assert "UTF-8 で読めませんでした" in html
    assert "/seizou:manual" in html
    assert "この画面の説明書はまだありません" not in html


def test_non_htmx_redirects_to_manual_page(client_logged_in, manual):
    write_fragment(manual, "masters_item_list", ITEM_FRAGMENT)
    response = client_logged_in.get(fragment_url("masters_item_list"))
    assert response.status_code == 302
    assert response["Location"] == "/manual/#masters_item_list"
    response = client_logged_in.get(fragment_url("masters_item_list"), {"part": "basic"})
    assert response.status_code == 302
    assert response["Location"] == "/manual/#masters_item_list"


@pytest.mark.parametrize("anchor", ["..", "no_such_screen", "masters:item_list", "index"])
def test_unknown_anchor_is_404(client_logged_in, manual, anchor):
    write_fragment(manual, "index", "<section><h2>作り物の前書き</h2></section>")
    response = client_logged_in.get(f"/manual/fragment/{anchor}/", **HTMX)
    assert response.status_code == 404
    response = client_logged_in.get(f"/manual/fragment/{anchor}/")
    assert response.status_code == 404


def test_path_with_slash_is_404(client_logged_in, manual):
    response = client_logged_in.get("/manual/fragment/a/b/", **HTMX)
    assert response.status_code == 404


def test_images_point_to_manual_images(client_logged_in, manual):
    write_fragment(
        manual,
        "masters_item_list",
        '<section><h2>部品マスター</h2><p>作り物の説明の字</p><img src="images/masters_item_list.png" alt="部品マスター"></section>',
    )
    html = modal(client_logged_in).content.decode()
    expected = reverse("core:manual_image", args=["masters_item_list.png"])
    assert expected == "/manual/images/masters_item_list.png"
    assert f'src="{expected}"' in html
    assert 'src="images/' not in html
    assert (manual / "masters_item_list.html").read_text(encoding="utf-8").count('src="images/') == 1


# ---------------------------------------------------------------- 2 段目: ?part= の枠の切り出し


def test_part_slices_from_h3_to_next_heading(client_logged_in, manual):
    write_fragment(manual, "masters_item_detail", FRAME_FRAGMENT)
    basic = modal(client_logged_in, "masters_item_detail", part="basic").content.decode()
    assert '<h3 id="basic">基本情報</h3>' in basic
    assert "作り物の基本の字" in basic
    assert "作り物の関係の字" not in basic
    assert "概要" not in basic
    related = modal(client_logged_in, "masters_item_detail", part="related").content.decode()
    assert '<h3 id="related">関係する物</h3>' in related
    assert "作り物の関係の字" in related
    assert "作り物の基本の字" not in related
    assert "</section>" not in related
    assert "概要" not in related


@pytest.mark.parametrize("part", ["nothing", "../x", ""])
def test_part_not_found_falls_back_to_whole(client_logged_in, manual, part):
    write_fragment(manual, "masters_item_detail", FRAME_FRAGMENT)
    html = modal(client_logged_in, "masters_item_detail", part=part).content.decode()
    assert "作り物の基本の字" in html
    assert "作り物の関係の字" in html
    assert "<h2" not in html
    if part:
        assert part not in html
