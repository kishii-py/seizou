"""人ごとの表示の設定 (上の帯の色) の試験 (決め 11-05 21:2x。N167)。名前は作り物だけ。

services の set_user_preference が名前の表 (core.display.HEADER_COLORS) の値だけを本人の行に書き、白 (標準) で行を消すこと、
土台の <html> に本人の色だけが data-seizou-header で出ること、右上の利用者のメニューの入口と小窓の見本 8 つと印、
小窓で保存すると 204 と HX-Refresh (画面の全体を読み直す) を返すこと、色の決まりが scripts/css/input.css にあることを見る。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from seizou.core import display, services
from seizou.core.models import UserPreference

ROOT = Path(__file__).resolve().parents[1]
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
SWATCH = re.compile(r'data-seizou-swatch="([a-z]+)">(.*?)</span>', re.S)


def html_tag(client) -> str:
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    return re.search(r"<html[^>]*>", response.content.decode()).group(0)


def test_set_and_clear_header_color(user):
    assert services.user_preference(user, display.HEADER_COLOR, "white") == "white"
    row = services.set_user_preference(user, display.HEADER_COLOR, "blue")
    assert (row.user, row.key, row.value) == (user, "header_color", "blue")
    services.set_user_preference(user, display.HEADER_COLOR, "sea")
    assert UserPreference.objects.get(user=user).value == "sea"  # 1 人 1 鍵 1 行 (書き換える)
    assert services.user_preference(user, display.HEADER_COLOR) == "sea"

    assert services.set_user_preference(user, display.HEADER_COLOR, "white") is None  # 白 (標準) は行を消す
    assert not UserPreference.objects.exists()
    assert services.user_preference(user, display.HEADER_COLOR, "white") == "white"


@pytest.mark.parametrize("value", ["pink", "", "Blue", None, 1])
def test_bad_value_is_refused(user, value):
    with pytest.raises(ValueError, match=r"^保存できません。ヘッダーの色は white / blue / "):
        services.set_user_preference(user, display.HEADER_COLOR, value)
    with pytest.raises(KeyError):
        services.set_user_preference(user, "font_size", "blue")
    assert not UserPreference.objects.exists()


def test_html_shows_only_own_color(client, user):
    other = get_user_model().objects.create_user("other", password="pass-for-tests-only")
    services.set_user_preference(user, display.HEADER_COLOR, "violet")

    client.force_login(user)
    assert html_tag(client) == '<html lang="ja" data-theme="light" data-seizou-header="violet">'
    body = client.get(reverse("core:home")).content.decode()
    assert '<header class="seizou-header sticky top-0 ' in body

    other_client = Client()
    other_client.force_login(other)
    assert html_tag(other_client) == '<html lang="ja" data-theme="light">'  # 未設定の人には出さない
    login_page = Client().get(reverse("core:login")).content.decode()
    assert "data-seizou-header" not in login_page  # ログインの前にも出さない


def test_menu_entry_and_modal_swatches(client_logged_in, user):
    url = reverse("core:display_settings")
    body = client_logged_in.get(reverse("core:home")).content.decode()
    assert f'<a href="{url}" data-modal-form="{url}" class="flex items-center gap-2">' in body
    assert "表示の設定</a>" in body

    services.set_user_preference(user, display.HEADER_COLOR, "green")
    modal = client_logged_in.get(url, **MODAL).content.decode()
    assert "<html" not in modal and "ヘッダーの色" in modal and "見た目" in modal
    swatches = SWATCH.findall(modal)
    assert [name for name, _ in swatches] == list(display.HEADER_COLORS)  # 8 つ (並びは表の順)
    assert [name for name, inner in swatches if "<svg" in inner] == ["green"]  # 印は今の色だけ
    assert modal.count(" checked>") == 1 and 'value="green" checked>' in modal
    assert f'hx-post="{url}" hx-trigger="change" hx-target="#seizou-modal-box"' in modal


def test_modal_post_saves_and_reloads_page(client_logged_in, user):
    url = reverse("core:display_settings")
    response = client_logged_in.post(url, {"header_color": "sunset"}, **MODAL)
    assert response.status_code == 204
    assert response["HX-Refresh"] == "true" and "HX-Trigger" not in response
    assert services.user_preference(user, display.HEADER_COLOR) == "sunset"

    refused = client_logged_in.post(url, {"header_color": "pink"}, **MODAL)
    assert refused.status_code == 200 and "<html" not in refused.content.decode()
    assert services.user_preference(user, display.HEADER_COLOR) == "sunset"  # 外れた値は書かない


def test_full_page_and_login(client, user):
    url = reverse("core:display_settings")
    assert client.get(url).status_code == 302  # ログインの前は開けない

    client.force_login(user)
    page = client.get(url)
    assert page.status_code == 200 and 'name="header_color"' in page.content.decode()
    response = client.post(url, {"header_color": "black"})
    assert response.status_code == 302 and response["Location"] == url
    assert html_tag(client) == '<html lang="ja" data-theme="light" data-seizou-header="black">'


def test_full_page_uses_swatch_card_and_save_button(client_logged_in, user):
    """全体の画面 (新しいタブ・URL の直打ち) も見本のカードで描く (C53)。htmx は付けず、見本を選んで「保存」のボタンでふつうに送る。"""
    url = reverse("core:display_settings")
    page = client_logged_in.get(url).content.decode()
    assert "<html" in page and "見た目" in page
    assert [name for name, _ in SWATCH.findall(page)] == list(display.HEADER_COLORS)  # 見本 8 つ (並びは表の順)
    assert '<div id="id_header_color" class="radio">' not in page  # 土台の widget の外の箱 (24px の空の丸) は出ない
    assert f'<form method="post" action="{url}" data-enter-guard class="min-w-0">' in page
    assert f'hx-post="{url}"' not in page and 'hx-trigger="change"' not in page
    assert page.count('class="btn btn-primary" type="submit"') == 1  # 「保存」だけ (上の帯と左の欄の submit は数えない)
    assert len(re.findall(r'<button class="btn btn-primary" type="submit">.*?保存</button>', page, re.S)) == 1
    assert "見本を選んで「保存」を押します。" in page and "画面を読み直します。" not in page

    modal = client_logged_in.get(url, **MODAL).content.decode()
    assert "data-enter-guard" not in modal and "保存</button>" not in modal  # 小窓は今のまま (見本を押すとすぐ保存)
    assert "data-save-bar" not in modal and "seizou-save-row" not in modal  # 小窓には下の行も書きかけの字も無い (C21)
    assert "見本を押すとすぐ保存し、画面を読み直します。" in modal

    response = client_logged_in.post(url, {"header_color": "violet"})  # 全体の画面の保存はふつうの POST で同じ画面へ
    assert response.status_code == 302 and response["Location"] == url
    assert services.user_preference(user, display.HEADER_COLOR) == "violet"


def test_fixed_rules_in_input_css():
    css = (ROOT / "scripts" / "css" / "input.css").read_text(encoding="utf-8")
    for name in display.HEADER_COLORS:
        rule = f'[data-seizou-header="{name}"] .seizou-header,\n[data-seizou-swatch="{name}"] {{'
        assert (rule in css) == (name != display.HEADER_COLOR_DEFAULT), name  # 白 (標準) は決まりを持たない
    assert "[data-seizou-header] .seizou-header .dropdown-content {" in css
    base = (ROOT / "src" / "seizou" / "core" / "templates" / "core" / "base.html").read_text(encoding="utf-8")
    assert '{% if seizou.header_color %} data-seizou-header="{{ seizou.header_color }}"{% endif %}>' in base
