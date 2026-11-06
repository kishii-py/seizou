"""マニュアルの一覧のいちばん上の空の枠 (初見テスト R5 の V68・U56。候補 C41 の仕事 2。C41-4 = a)。

いちばん上は直下のマニュアルだけを並べるので、フォルダの中にしか無いと「全 0 件」の下に空の枠が出る。
枠を上へ詰め (min-h-64 と justify-center を外す)、フォルダの中にある数の 1 文を添える。
画面の全体と htmx の部分の返しの両方で同じ字が出ることを見る。フォルダとマニュアルの名前は作り物。
"""

import pytest
from django.urls import reverse

from seizou.manuals import services

ROOT_EMPTY = "このフォルダの直下にはマニュアルがありません。フォルダを開いてください。"
IN_FOLDERS = "マニュアルはフォルダの中に"
HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def url():
    return reverse("manuals:manual_list")


def test_top_says_count_in_folders(client_logged_in, user, url):
    folder = services.create_folder(None, "段取り", "blue", user)
    services.create_manual(folder, "段取りの手順", "steps", user)

    for html in (client_logged_in.get(url).content.decode(), client_logged_in.get(url, **HTMX).content.decode()):
        assert ROOT_EMPTY in html
        assert "マニュアルはフォルダの中に 1 件あります。" in html


def test_top_counts_every_depth(client_logged_in, user, url):
    top = services.create_folder(None, "出荷", "green", user)
    deep = services.create_folder(services.create_folder(top, "梱包", "gray", user), "箱", "gray", user)
    services.create_manual(top, "出荷の手順", "steps", user)
    services.create_manual(deep, "箱詰めの手順", "steps", user)

    html = client_logged_in.get(url).content.decode()
    assert "マニュアルはフォルダの中に 2 件あります。" in html


def test_top_without_manuals_in_folders_has_no_count(client_logged_in, user, url):
    services.create_folder(None, "段取り", "blue", user)

    for html in (client_logged_in.get(url).content.decode(), client_logged_in.get(url, **HTMX).content.decode()):
        assert ROOT_EMPTY in html
        assert IN_FOLDERS not in html


def test_count_only_at_top(client_logged_in, user, url):
    folder = services.create_folder(None, "段取り", "blue", user)
    services.create_manual(folder, "段取りの手順", "steps", user)

    for params in ({"folder": folder.pk}, {"q": "段取り"}):
        assert IN_FOLDERS not in client_logged_in.get(url, params).content.decode(), params
        assert IN_FOLDERS not in client_logged_in.get(url, params, **HTMX).content.decode(), params


def test_no_folders_has_no_count(client_logged_in, url):
    for html in (client_logged_in.get(url).content.decode(), client_logged_in.get(url, **HTMX).content.decode()):
        assert IN_FOLDERS not in html
        assert ROOT_EMPTY not in html


def test_top_box_is_not_tall(client_logged_in, user, url):
    folder = services.create_folder(None, "段取り", "blue", user)
    services.create_manual(folder, "段取りの手順", "steps", user)
    empty = services.create_folder(None, "検査", "red", user)

    top = client_logged_in.get(url, **HTMX).content.decode()
    assert ROOT_EMPTY in top
    assert "min-h-64" not in top
    # フォルダの中の空の枠 (B-11) は今のまま。
    inside = client_logged_in.get(url, {"folder": empty.pk}, **HTMX).content.decode()
    assert "このフォルダの直下にはまだ 1 件もありません。" in inside
    assert "min-h-64" in inside
