"""ファイルの欄のドラッグの枠 (N111。決め 11-04 14:4x)。

入力の土台 (core/form.html) とマニュアルの画像の欄が、<input type=file> を破線の枠 (data-file-drop) の中に置き、
input の name と multiple は今までどおり (form の処理と E2E の set_input_files はそのまま) であることを HTML で見る。
ドラッグとサムネの動きは JS (core/static/js/seizou-file-drop.js) で、ブラウザでの確かめは本体。
取引先と品目と題は作り物。
"""

import re

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse

from seizou.manuals import services as manuals_services

SCRIPT = '<script src="/static/js/seizou-file-drop.js" defer></script>'


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    """鍵を外す (注文書の読み取りと AI の聞き取りの段が本物の API を呼ばない。欄はどちらでも描く)。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _box_with_input(name: str) -> re.Pattern:
    """枠の開きの tag の直後に、その name の <input type=file> がある形。"""
    return re.compile(r"data-file-drop>\s*<input type=\"file\" name=\"" + re.escape(name) + r"\"[^>]*>")


def _manual_update(user) -> str:
    manual = manuals_services.create_manual(None, "作り物の段取りの手順", "steps", user)
    return reverse("manuals:manual_update", args=[manual.pk])


@pytest.mark.parametrize(
    ("url_of", "name"),
    [
        (lambda user: reverse("masters:item_create"), "drawing"),
        (lambda user: reverse("feedback:feedback_create"), "screenshots"),
        (lambda user: reverse("intake:extraction_create"), "file"),
        (_manual_update, "file"),
    ],
    ids=["部品の図面", "フィードバックの画面の絵", "注文書のファイル", "マニュアルの画像"],
)
def test_file_input_sits_in_drop_box(logged_in, user, url_of, name):
    response = logged_in.get(url_of(user))
    html = response.content.decode()

    assert response.status_code == 200
    assert html.count("data-file-drop>") == 1
    assert _box_with_input(name).search(html)
    assert html.count(f'type="file" name="{name}"') == 1
    assert "ファイルを選ぶ</span> か、ここへドラッグ" in html  # 枠の中の案内 (選ぶ所だけ主色)
    assert 'tabindex="0" role="button"' in html  # 枠に焦点を置いて Enter か Space で開ける


def test_multiple_input_keeps_multiple_in_box(logged_in):
    html = logged_in.get(reverse("feedback:feedback_create")).content.decode()

    tag = _box_with_input("screenshots").search(html).group(0)
    assert " multiple" in tag
    assert 'accept="image/png,image/jpeg,image/webp"' in tag


def test_script_is_loaded_from_base(logged_in):
    html = logged_in.get(reverse("core:home")).content.decode()

    assert html.count(SCRIPT) == 1
    path = finders.find("js/seizou-file-drop.js")
    assert path
    with open(path, encoding="utf-8") as f:
        script = f.read()
    assert "DataTransfer" in script
    assert "class" + "Name" not in script and "class" + "List" not in script
