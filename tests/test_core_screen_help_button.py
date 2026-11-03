"""画面の題の横の「?」(core/_screen_help.html) が、全部の画面に出ること。

Django の test client で HTML を見る (本物のブラウザは使わない)。データは作り物だけ。
"""

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

import seizou.core
from seizou.core.manual import fragment_name
from seizou.core.screens import all_screens
from seizou.core.views import BaseListView
from seizou.masters.models import Item

HELP_TEMPLATE = Path(seizou.core.__file__).parent / "templates" / "core" / "_screen_help.html"

HELP_CLASS = "btn btn-ghost btn-square btn-sm text-base-content/50 hover:text-base-content"

SCREEN_LABEL = 'aria-label="この画面の説明"'


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def site(settings, tmp_path):
    """会社の置き場 (一時の置き場)。manual/ は作らない (「?」は断片の有無で変わらない)。"""
    settings.BASE_DIR = tmp_path
    return tmp_path


@pytest.fixture
def item(db):
    return Item.objects.create(part_no="P-1001", name="作り物のブラケット")


def page(client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200, url
    return response.content.decode()


def help_anchors(html: str) -> list[str]:
    """「?」の hx-get が指す断片の名前の並び (?part= の有無を問わない)。"""
    return re.findall(r'hx-get="/manual/fragment/([^/"]+)/', html)


def assert_screen_button(html: str, anchor: str) -> None:
    assert html.count(SCREEN_LABEL) == 1, anchor
    assert f'hx-get="{reverse("core:manual_fragment", args=[anchor])}"' in html, anchor
    assert f'href="/manual/#{anchor}"' in html, anchor
    assert 'hx-target="#seizou-modal-box"' in html, anchor


# ---------------------------------------------------------------- 1 段目: 画面の「?」


@pytest.mark.parametrize(
    "url_name",
    ["masters:item_list", "masters:item_create", "core:home", "core:search", "core:settings", "core:manual"],
)
def test_screens_have_help_button(client_logged_in, site, url_name):
    html = page(client_logged_in, reverse(url_name))
    assert_screen_button(html, fragment_name(url_name))


def test_detail_has_help_button(client_logged_in, site, item):
    html = page(client_logged_in, reverse("masters:item_detail", args=[item.pk]))
    assert_screen_button(html, "masters_item_detail")


def test_help_button_is_not_primary_and_has_fixed_class(client_logged_in, site, item):
    body = page(client_logged_in, reverse("masters:item_list"))
    assert body.count("btn-primary") == 1
    assert f'<a class="{HELP_CLASS}"' in body
    source = HELP_TEMPLATE.read_text(encoding="utf-8")
    classes = re.findall(r'class="([^"]*)"', source)
    assert classes == [HELP_CLASS]
    for value in classes:
        assert "{{" not in value and "{%" not in value
    assert "btn-primary" not in source


def test_anchor_matches_fragment_name_for_catalog(client_logged_in, site):
    seen = 0
    for screen in all_screens():
        if "<" in screen.path:
            continue
        response = client_logged_in.get(screen.path)
        if response.status_code != 200:
            continue
        anchors = set(help_anchors(response.content.decode()))
        assert anchors == {fragment_name(screen.url_name)}, screen.url_name
        seen += 1
    assert seen >= 5


class _HelpItemList(BaseListView):
    model = Item
    columns = ("part_no", "name")
    template_name = "core/list.html"


@pytest.mark.django_db
def test_request_factory_view_has_no_button(user):
    Item.objects.create(part_no="P-1001", name="作り物のブラケット")
    request = RequestFactory().get("/")
    request.user = user
    response = _HelpItemList.as_view()(request)
    response.render()
    assert response.status_code == 200
    body = response.content.decode()
    assert "P-1001" in body
    assert SCREEN_LABEL not in body
    assert "/manual/fragment/" not in body


def test_topbar_manual_button_stays(client_logged_in, site):
    html = page(client_logged_in, reverse("core:home"))
    assert 'aria-label="使い方"' in html
    assert 'href="/manual/"' in html
    assert html.count(SCREEN_LABEL) == 1
