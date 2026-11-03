"""戻るで閉じる左のメニュー (N77 の 8): 今の大項目の印 (data-seizou-active) と、履歴の復元の後に開き直す script。

ブラウザの閉じる動き (同じ name の details の取り合い) は test client では見えないので、印と script の有無と、
戻る (htmx の履歴の復元の要求) の応答が画面の全体で印を持つことを HTML で見る。動きは手の確かめ。
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

MARKED = 'name="seizou-menu-section" data-seizou-active open>'
UNMARKED = 'name="seizou-menu-section">'


@pytest.fixture
def admin_client(client, db):
    client.force_login(get_user_model().objects.create_superuser("admin", password="tests-only-2611"))
    return client


def html(response) -> str:
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_active_section_is_marked_once(admin_client):
    page = html(admin_client.get(reverse("orders:order_list")))

    assert page.count(MARKED) == 1
    assert page.count(UNMARKED) >= 1
    assert page.count("htmx:historyRestore") == 1
    assert page.count("window.seizouSidebarReopen") == 2  # 登録の印を見る if と、印を立てる代入
    # 印は details の外に出さない (開いた大項目の summary の直前)
    assert page.index(MARKED) < page.index("受注管理")

    # ダッシュボード (開け閉めの無い括り) では、印の付いた大項目が無い
    home = html(admin_client.get(reverse("core:home")))
    assert "<details" in home
    assert home.count("data-seizou-active open") == 0
    assert home.count("htmx:historyRestore") == 1


@pytest.mark.django_db
def test_history_restore_returns_marked_sidebar(admin_client):
    url = reverse("orders:order_list")
    restored = admin_client.get(
        url, {"sort": "order_no"}, HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true"
    )
    page = html(restored)

    # 戻るの応答は行の部分でなく画面の全体 (body を丸ごと差し替える) で、同じ印と script を持つ
    assert "<html" in page and 'id="seizou-drawer"' in page
    assert page.count(MARKED) == 1
    assert page.count("htmx:historyRestore") == 1

    # 並び替えの htmx の要求 (戻るでない) は行の部分だけ = 左のメニューを差し替えない
    rows_only = html(admin_client.get(url, {"sort": "order_no"}, HTTP_HX_REQUEST="true"))
    assert "seizou-menu-section" not in rows_only
