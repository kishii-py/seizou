"""2 社の主色と業種 (N77 の 10): 設定の鍵 brand_color と industry。

brand_color は base.html の <html> の style で daisyUI の主色の変数を上書きし、industry は左の上の会社の名前の下に出す。
会社の設定は settings の fixture で SEIZOU を替える (tests/test_core_settings_screen.py の形)。名前は作り物だけ。
"""

import re
from pathlib import Path

import pytest
import yaml
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou import cli
from seizou.core import conf
from seizou.core import services

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
COMPANY_IDS = ("a_machining", "b_plating")
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


def test_validate_brand_color_and_industry():
    assert services.validate_setting("brand_color", "#ABCDEF") == "#abcdef"
    assert services.validate_setting("brand_color", " #15803d ") == "#15803d"
    for bad in ("abc", "#12345", "#1234567", "", "red", None):
        with pytest.raises(ValueError, match="brand_color は # と 16 進の 6 桁"):
            services.validate_setting("brand_color", bad)
    assert services.validate_setting("industry", " 表面処理 ") == "表面処理"
    with pytest.raises(ValueError):
        services.validate_setting("industry", "   ")


def test_cli_writes_industry_into_seizou():
    lines = cli.seizou_items({"id": "x_co", "industry": "機械加工", "port": 8012}, {}).split("\n")
    assert lines == ["    'id': 'x_co',", "    'industry': '機械加工',", "    'port': 8012,"]
    # 答えの settings に industry があればそちらが勝つ。答えの頭に無ければ空の字
    given = cli.seizou_items({"id": "x_co", "industry": "機械加工", "port": 1}, {"industry": "作り物"})
    assert "    'industry': '作り物'," in given and "機械加工" not in given
    assert "    'industry': ''," in cli.seizou_items({"id": "x_co", "port": 1}, {})


def test_example_companies_have_different_brand_colors():
    colors = {}
    for company_id in COMPANY_IDS:
        with open(COMPANIES_DIR / f"{company_id}.yaml", encoding="utf-8") as f:
            answers = yaml.safe_load(f)
        colors[company_id] = answers["settings"]["brand_color"]
        assert conf.BRAND_COLOR_PATTERN.fullmatch(colors[company_id]), company_id
        assert answers["industry"]
    assert len(set(colors.values())) == len(COMPANY_IDS)
