"""画面の言葉と見た目の小さな直し (精査 A3・A1・A5 の所見)。Django の test client で HTML を見る。

設定の画面の幅・権限の選択肢の字・説明書が無い時の文・取消の受注の「編集」と R05 の文・マニュアルの最上位の文・
取消の確かめの画面の案内と釦・出荷と納品書の次の一手・AI チャットの鍵なしの文・数の字の末尾の 0。
品番と会社名は作り物。
"""

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

import seizou.core

User = get_user_model()

PASSWORD = "polish-tests-2611"
APP_CSS = Path(seizou.core.__file__).resolve().parent / "static" / "css" / "app.css"


@pytest.fixture
def user(db):
    return User.objects.create_user("tester", password=PASSWORD)


@pytest.fixture
def manager(db):
    return User.objects.create_superuser("admin", password=PASSWORD)


# ---------------------------------------------------------------- 1. 設定の画面の幅


def test_settings_system_rows_wrap_on_narrow_screen(client, manager):
    client.force_login(manager)
    html = client.get(reverse("core:settings")).content.decode()
    assert '<dd class="min-w-0 break-words">' in html
    assert '<dd class="min-w-0 break-words font-mono text-xs">' in html
    assert '<dd id="doctor-result" class="min-w-0 break-words">' in html
    assert '<dt class="whitespace-nowrap' not in html
    assert '<dt class="text-base-content/70 sm:whitespace-nowrap">' in html


def test_app_css_has_sm_nowrap():
    assert ".sm\\:whitespace-nowrap" in APP_CSS.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 7. 権限の選択肢の「つながり」


def test_kind_label_for_connections():
    from seizou.core import access

    assert access.KIND_LABELS["connections"] == "取引先との接続"


def test_access_form_shows_connections_label(client, manager):
    from django.apps import apps

    assert apps.is_installed("seizou.accounts")  # 試験の設定は全部の部品を入れる (tests/settings.py の PARTS)
    client.force_login(manager)
    response = client.get(reverse("accounts:access_create"))
    assert response.status_code == 200
    assert "取引先との接続" in response.content.decode()
