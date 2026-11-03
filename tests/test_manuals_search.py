"""業務マニュアルの横断の検索 (core の検索の画面の約束に乗る)。"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.manuals import search, services


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


def test_core_search_has_manuals_section(client_logged_in, user):
    folder = services.create_folder(services.create_folder(None, "段取り", "blue", user), "旋盤", "gray", user)
    manual = services.create_manual(folder, "刃物の交換の手順", "steps", user)
    services.update_manual(manual, {"body": "刃物の番号を確かめる"}, user)
    response = client_logged_in.get(reverse("core:search"), {"q": "刃物 番号"})
    assert response.status_code == 200
    html = response.content.decode()
    assert "マニュアル" in html
    assert "刃物の交換の手順" in html
    assert "段取り / 旋盤" in html
    assert reverse("manuals:manual_detail", args=[manual.pk]) in html
    section = search.search("刃物", limit=50)
    assert (section.kind, section.label, section.total) == ("manuals", "マニュアル", 1)
    assert section.rows[0].cells[:3] == ("刃物の交換の手順", "段取り / 旋盤", "ステップ形式")


def test_partner_filter_returns_empty(user):
    services.create_manual(None, "刃物の交換の手順", "steps", user)
    section = search.search("刃物", partner=1)
    assert section.total == 0 and section.rows == ()
    assert search.search("刃物").total == 1


def test_manuals_found_by_other_width_and_kana(client_logged_in, user):
    # 全角のダッシュは U+FF0D (－)。本文は Markdown の字のまま引く (描いた HTML ではない)。
    manual = services.create_manual(None, "刃物の交換の手順", "steps", user)
    services.update_manual(manual, {"body": "バイトの番号を確かめる。\n\n- 図面 WK-1001 を見る"}, user)
    other = services.create_manual(None, "朝の点検", "steps", user)
    for word in ("ばいと", "ﾊﾞｲﾄ", "ＷＫ－１００１"):
        assert search.search(word).total == 1, word
    assert search.search("ばいと 朝").total == 0
    html = client_logged_in.get(reverse("core:search"), {"q": "ばいと"}).content.decode()
    assert reverse("manuals:manual_detail", args=[manual.pk]) in html
    assert reverse("manuals:manual_detail", args=[other.pk]) not in html
