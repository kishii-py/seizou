"""設定の画面 (seizou.core.settings_screen。/settings/)。

見る所: ログインの確かめ、会社の選べる設定の今の値、普段は触らない物の入口 (つながり・アカウント管理)、システムの状態、
右上の利用者のメニューの「設定」、左のメニューから「つながり」が消えた事。鍵の値を出さない事。
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.urls import reverse

from seizou.core import settings_screen


@pytest.fixture
def logged_in(client, db):
    client.force_login(get_user_model().objects.create_user(username="tester", password="tests-only"))
    return client


def test_anonymous_is_sent_to_login(client, db):
    response = client.get(reverse("core:settings"))
    assert response.status_code == 302 and "/login/" in response["Location"]


def test_settings_screen_shows_company_settings_and_links(logged_in, settings, monkeypatch):
    settings.SEIZOU = {"id": "sample_co", "port": 8010, "over_shipment": "stop", "hidden_fields": ["note"]}
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SEIZOU_MCP_USER", raising=False)

    body = logged_in.get(reverse("core:settings")).content.decode()

    assert "設定" in body and "会社の選べる設定" in body
    assert "止める (stop)" in body and "知らせる (warn)" in body
    assert "note" in body
    assert "sample_co / 8010" in body
    assert reverse("connect:connection_list") in body and "取引先との接続" in body
    assert reverse("attachments:attachment_list") in body and "添付ファイルの一覧" in body
    # アカウント管理の入口: 部品 accounts (N36) が入っていれば一覧への link、無ければ「まだ入っていない」の案内。
    # 題の下の説明の 1 文は出さない (決め 11-03 14:1x) ので、字「アカウント管理」は入口の出る人の画面で見る
    if apps.is_installed("seizou.accounts"):
        # 普通の利用者には入口を出さない (アカウント管理の権限がある人と管理者だけ)
        assert reverse("accounts:access_list") not in body
        logged_in.force_login(get_user_model().objects.create_superuser(username="boss", password="tests-only"))
        boss_body = logged_in.get(reverse("core:settings")).content.decode()
        assert reverse("accounts:access_list") in boss_body and "アカウント管理" in boss_body
    else:
        assert "アカウント管理" in body and "まだ入っていない" in body
    assert "ない (読み取りとチャットは使えない)" in body
    assert "決めていない (書く道具は断る)" in body
    # データベースの名前は CI の postgres の組でも通るよう、つないでいる物から決める (sqlite と postgresql の 2 つ)
    assert {"sqlite": "SQLite", "postgresql": "PostgreSQL"}[connection.vendor] in body


def test_settings_screen_hides_key_values(logged_in, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-value-that-must-not-appear")
    monkeypatch.setenv("SEIZOU_MCP_USER", "admin")

    body = logged_in.get(reverse("core:settings")).content.decode()

    assert "fake-key-value-that-must-not-appear" not in body
    assert "AI の鍵" in body and "ある" in body
    assert "決めてある" in body


def test_display_words():
    assert settings_screen._display("order_form", "single") == "1 件 1 品 (single)"
    assert settings_screen._display("partial_shipment", False) == "許さない (False)"
    assert settings_screen._display("hidden_fields", []) == "なし"
    assert settings_screen._display("hidden_fields", ["a", "b"]) == "a・b"
    # 辞書の設定 (権限の判定。N36 が conf.DEFAULTS に足す) は「中の鍵: 値」を「・」で。知らない鍵はそのまま
    assert (
        settings_screen._display("access_control", {"department": "log", "write": "on", "money": "off"})
        == "部署: 記録だけ (log)・編集の権限: 有効 (on)・金額の閲覧: 使わない (off)"
    )
    assert settings_screen._display("access_control", {}) == "なし"
    assert settings_screen._display("unknown_dict", {"a": 1}) == "a: 1"


def test_user_menu_has_settings_and_sidebar_has_no_connections(logged_in):
    body = logged_in.get(reverse("core:home")).content.decode()

    assert reverse("core:settings") in body and ">設定<" in body.replace("\n", "")
    # つながりは左のメニューから設定の画面へ移した (普段は触らない物)
    assert reverse("connect:connection_list") not in body
    assert reverse("connect:inbox_list") in body
