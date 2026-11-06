"""マニュアルの画面の「ルート」→「トップ」・「置き場」→「フォルダ」と、列の見出し「保護 (移動・削除を止める)」。

初見テスト R5 の V85 (候補 C41 の仕事 3。C41-1 = a・C41-2 = a・C41-3 = a)。一覧・CSV・パンくず・詳細の帯・新規登録の欄・
横断の検索の列で、同じ言い方になっていることを見る。保護の列の値 (「はい」「いいえ」) は別の候補で見せ方が変わるので見ない。
この試験では、題とフォルダの名前に「ルート」「置き場」を使わない。フォルダとマニュアルの名前は作り物。
"""

import pytest
from django.urls import reverse

from seizou.manuals import search as manual_search
from seizou.manuals import services

CSV_HEADER = "題,フォルダ,形式,保護 (移動・削除を止める),最終更新"


@pytest.fixture
def folder(user):
    folder = services.create_folder(None, "段取り", "blue", user)
    services.create_manual(folder, "段取り替えの手順", "steps", user)
    return folder


def test_list_columns_and_csv(client_logged_in, folder):
    url = reverse("manuals:manual_list")
    response = client_logged_in.get(url, {"folder": folder.pk, "format": "csv"})
    assert response.status_code == 200
    assert response.content.decode().lstrip("\ufeff").splitlines()[0] == CSV_HEADER

    html = client_logged_in.get(url, {"folder": folder.pk}).content.decode()
    assert "保護 (移動・削除を止める)" in html
    assert "sort=protected" in html
    assert "置き場" not in html


def test_breadcrumb_says_top(client_logged_in, folder):
    url = reverse("manuals:manual_list")
    top = client_logged_in.get(url).content.decode()
    assert '<span class="font-medium">トップ</span>' in top
    assert "ルート" not in top

    inside = client_logged_in.get(url, {"folder": folder.pk}).content.decode()
    assert 'href="/manuals/">トップ</a>' in inside


def test_detail_path_says_folder_and_top(client_logged_in, folder):
    manual = folder.manuals.get()
    html = client_logged_in.get(reverse("manuals:manual_detail", args=[manual.pk])).content.decode()
    assert '<nav aria-label="フォルダ"' in html
    assert "トップ</a>" in html
    assert 'aria-label="置き場"' not in html
    assert "ルート" not in html


def test_create_form_field_says_folder_and_top(client_logged_in, folder):
    html = client_logged_in.get(reverse("manuals:manual_create")).content.decode()
    assert '<option value="" selected>トップ</option>' in html
    assert 'for="id_folder" class="font-medium">フォルダ</label>' in html
    assert "置き場" not in html


def test_search_columns_and_top(user):
    assert [column.label for column in manual_search.COLUMNS] == ["題", "フォルダ", "形式", "最終更新"]
    services.create_manual(None, "朝の点検の手順", "steps", user)
    section = manual_search.search("朝の点検")
    assert [row.cells[1] for row in section.rows] == ["トップ"]
