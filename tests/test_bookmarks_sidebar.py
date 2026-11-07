"""左のメニューの「ブックマーク」の組 (bookmarks/_menu.html と tag bookmark_menu。受け渡しの seizou-部品の6案-20261107/指示書.md の 5。
本人の決め 11-07 18:2x〜18:4x = A「左のメニューにブックマークを出す」)。

画面の HTML は Django の test client で開き、左のメニューの <nav> を切り出して見る (上の帯の ☆ の小窓と「よく使う画面」にも同じ link があるため)。
登録は services で作る。名前は作り物だけ。
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.bookmarks import services
from seizou.bookmarks.models import Bookmark
from seizou.bookmarks.services import catalog
from seizou.bookmarks.templatetags import bookmarks_tags
from seizou.core import context_processors

GROUP = '<details id="menu-bookmarks" class="group mb-1" name="seizou-menu-section">'


@pytest.fixture(autouse=True)
def fresh_catalog():
    catalog.cache_clear()
    yield
    catalog.cache_clear()


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


def html_of(response) -> str:
    assert response.status_code == 200
    return response.content.decode()


def nav_of(html: str) -> str:
    return re.search(r'<nav class="flex-1 overflow-y-auto px-4 py-2" aria-label="メニュー">(.*?)</nav>', html, re.S).group(1)


def group_of(nav: str) -> str:
    found = re.search(r'<details id="menu-bookmarks".*?</details>', nav, re.S)
    assert found, "左のメニューに「ブックマーク」の組が無い"
    return found.group(0)


def links_of(group: str) -> list[tuple[str, str]]:
    """組の中の入口 (href と字) を並びの順に。"""
    return re.findall(r'<a href="([^"]+)" class="[^"]*pl-9[^"]*"[^>]*>.*?<span class="truncate">([^<]+)</span>', group, re.S)


def test_no_group_without_bookmarks(client_logged_in):
    nav = nav_of(html_of(client_logged_in.get(reverse("core:home"))))

    assert 'id="menu-bookmarks"' not in nav and "ブックマーク" not in nav
    assert nav.count('name="seizou-menu-section"') == 5  # 部品の大項目だけ


def test_group_lists_bookmarks_in_registration_order_after_sections(client_logged_in, user):
    services.add_bookmark(user, "orders:order_list")
    services.add_bookmark(user, "masters:item_list")
    html = html_of(client_logged_in.get(reverse("core:home")))
    nav = nav_of(html)
    group = group_of(nav)

    # 組は部品の大項目の後 (nav の最後の details) で、フィードバックの入口の前。初めは閉じている (同じ name の組と open を取り合わない)
    assert GROUP in nav and nav.rfind("<details") == nav.index(GROUP)
    assert nav.count('name="seizou-menu-section"') == 6
    assert html.index('id="menu-bookmarks"') < html.index('id="feedback-entry"')
    assert "data-seizou-active" not in group and " open" not in group.split("\n")[0]
    # 見出しは ☆ の絵と「ブックマーク」。入口は登録の順 (受注 → 部品マスター) で、大項目の入口と同じ形 (pl-9・絵・切り詰め)
    assert "<summary" in group and "ブックマーク" in group
    assert links_of(group) == [
        (reverse("orders:order_list"), services.screen_of("orders:order_list").name),
        (reverse("masters:item_list"), services.screen_of("masters:item_list").name),
    ]
    assert group.count("<svg") == 2 + 2  # ☆ と開け閉めの印 + 入口ごとの絵
    # 待っている数の札の入れ物は組の中に書かない (tests/test_core_menu_counts.py が数える)
    assert 'id="menu-count-' not in group
    # ダッシュボードでは、今の画面の印は無い
    assert 'aria-current="page"' not in group


def test_current_screen_is_marked_in_group(client_logged_in, user):
    services.add_bookmark(user, "orders:order_list")
    services.add_bookmark(user, "masters:item_list")

    nav = nav_of(html_of(client_logged_in.get(reverse("orders:order_list"))))
    group = group_of(nav)
    assert re.search(r'<a href="/orders/" class="[^"]*pl-9[^"]*" aria-current="page">', group)
    assert group.count('aria-current="page"') == 1
    # 大項目の入口 (受注) と組の入口の 2 つに印。組は開かない (今の画面の大項目だけが open)
    assert nav.count('aria-current="page"') == 2
    assert re.findall(r"<details[^>]*\bopen\b[^>]*>\s*<summary[^>]*>.*?<span[^>]*>([^<]+)</span>", nav, re.S) == ["生産管理部"]

    # 受注の詳細のような下の道でも、大項目の入口と同じ決め方 (URL の先頭が合う最も長い 1 つ) で受注に印
    nav = nav_of(html_of(client_logged_in.get(reverse("masters:item_list"))))
    assert links_of(group_of(nav))[1][0] == reverse("masters:item_list")
    assert re.search(r'<a href="/masters/items/" class="[^"]*pl-9[^"]*" aria-current="page">', group_of(nav))


def test_lost_screen_is_not_listed_in_group(client_logged_in, user):
    services.add_bookmark(user, "orders:order_list")
    Bookmark.objects.create(user=user, url_name="gone:nowhere", label="消えた画面", position=999)

    html = html_of(client_logged_in.get(reverse("core:home")))
    group = group_of(nav_of(html))
    assert len(links_of(group)) == 1 and "消えた画面" not in group
    # 上の帯の ☆ の小窓には今までどおり「見つかりません」で並ぶ (消す口がそこにある)
    assert "消えた画面 (見つかりません)" in html


def test_group_is_absent_when_the_part_is_missing(client_logged_in, user, monkeypatch):
    services.add_bookmark(user, "orders:order_list")
    original = context_processors.part_names
    monkeypatch.setattr(context_processors, "part_names", lambda: [name for name in original() if name != "bookmarks"])

    nav = nav_of(html_of(client_logged_in.get(reverse("core:home"))))
    assert 'id="menu-bookmarks"' not in nav


def test_bookmarks_table_is_read_once_per_screen(client_logged_in, user):
    """☆ の小窓・左のメニュー・「よく使う画面」が同じ並びを使う (登録の表の読みは 1 回。登録済みかの読みと合わせて 2 回)。"""
    services.add_bookmark(user, "orders:order_list")
    services.add_bookmark(user, "masters:item_list")
    table = Bookmark._meta.db_table

    for url in (reverse("core:home"), reverse("orders:order_list")):
        with CaptureQueriesContext(connection) as captured:
            html = html_of(client_logged_in.get(url))
        assert 'id="menu-bookmarks"' in html
        assert sum(table in query["sql"] for query in captured.captured_queries) == 2, url


def test_menu_tag_without_request_or_login_is_empty(db):
    assert bookmarks_tags.bookmark_menu({}) == []
    assert bookmarks_tags.bookmark_home({}) == []
