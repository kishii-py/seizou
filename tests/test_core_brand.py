"""2 社の主色と業種 (N77 の 10 を N92 で入れ直す): 設定の鍵 brand_color と industry。

brand_color は base.html の <html> の style で daisyUI の主色の変数を上書きし、industry は左の上の会社の名前の下に出す。
会社の設定は settings の fixture で SEIZOU を替える (tests/test_core_settings_screen.py の形)。名前は作り物だけ。
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import conf
from seizou.core import services

SUBTITLE = '<span class="block text-xs text-base-content/50">'


@pytest.fixture
def logged_in(client, db):
    client.force_login(get_user_model().objects.create_user("tester", password="tests-only-2611"))
    return client


def html_tag(client) -> str:
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    return re.search(r"<html[^>]*>", response.content.decode()).group(0)


def subtitle(client) -> str:
    body = client.get(reverse("core:home")).content.decode()
    start = body.index(SUBTITLE) + len(SUBTITLE)
    return body[start : body.index("</span>", start)]


def test_brand_color_sets_primary_variables(settings, logged_in):
    settings.SEIZOU = {"brand_color": "#15803D"}
    assert html_tag(logged_in) == (
        '<html lang="ja" data-theme="light" style="--color-primary: #15803d; --color-primary-content: #ffffff">'
    )


@pytest.mark.parametrize("value", ["red; background: url(x)", "", "#12345", "15803d", None, 123])
def test_bad_or_empty_brand_color_keeps_base_color(settings, logged_in, value):
    settings.SEIZOU = {"brand_color": value}
    tag = html_tag(logged_in)
    assert "style=" not in tag
    assert tag == '<html lang="ja" data-theme="light">'


def test_no_brand_color_key_keeps_base_color(logged_in):
    assert "style=" not in html_tag(logged_in)
    assert conf.brand_color() == ""


def test_industry_is_the_sidebar_subtitle(settings, logged_in):
    settings.SEIZOU = {"industry": "作り物の業種"}
    assert subtitle(logged_in) == "作り物の業種"

    settings.SEIZOU = {"industry": ""}
    assert subtitle(logged_in) == "seizou"


def test_brand_color_shape_passes():
    # 形の合う主色は小文字にそろえ、空の字 (空白だけを含む) は既定 (設定なし = 土台の色) として通す
    assert services.validate_setting("brand_color", "#12ab34") == "#12ab34"
    assert services.validate_setting("brand_color", "#ABCDEF") == "#abcdef"
    assert services.validate_setting("brand_color", " #15803d ") == "#15803d"
    assert services.validate_setting("brand_color", "") == ""
    assert services.validate_setting("brand_color", "   ") == ""


def test_brand_color_shape_refuses():
    # 字でない値 (答えの YAML で値を書き忘れた None など) も誤りにして、点検の「直す」で知らせる
    for bad in ("red", "abc", "#12345", "#1234567", None, 123):
        with pytest.raises(ValueError, match="brand_color は # と 16 進の 6 桁"):
            services.validate_setting("brand_color", bad)


def test_validate_industry():
    assert services.validate_setting("industry", " 表面処理 ") == "表面処理"
    assert services.validate_setting("industry", "") == ""
    assert services.validate_setting("industry", "   ") == ""
    assert services.validate_setting("industry", "業" * 40) == "業" * 40
    for bad in ("業" * 41, None):
        with pytest.raises(ValueError, match="industry \\(業種\\) は 40 字まで"):
            services.validate_setting("industry", bad)
