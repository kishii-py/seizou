"""ファイルの欄のドラッグの枠 (N111。決め 11-04 14:4x)。

入力の土台 (core/form.html) とマニュアルの画像の欄が、<input type=file> を破線の枠 (data-file-drop) の中に置き、
input の name と multiple は今までどおり (form の処理と E2E の set_input_files はそのまま) であることを HTML で見る。
ドラッグとサムネの動きは JS (core/static/js/seizou-file-drop.js) で、ブラウザでの確かめは本体。
取引先と品目と題は作り物。
"""

import io
import re

import pytest
from django.contrib.staticfiles import finders
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from seizou.core import services as core_services
from seizou.core.models import Company
from seizou.core.widgets import ClearableFileWidget
from seizou.manuals import services as manuals_services
from seizou.masters import services as masters_services
from seizou.masters.models import Item
from tests.test_intake_extract import FAKE_KEY

SCRIPT = '<script src="/static/js/seizou-file-drop.js" defer></script>'


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    """鍵を外す (注文書の読み取りと AI の聞き取りの段が本物の API を呼ばない)。

    注文書を読み取るの欄は鍵があるときだけ描く (C09) ので、その段だけ試験の中で鍵を置く (GET だけで API は呼ばない)。
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    """上げた図面の置き場を試験ごとの一時の置き場にする (手本 = tests/test_masters_drawing_viewer.py の media)。"""
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def _box_with_input(name: str) -> re.Pattern:
    """枠の開きの tag の直後に、その name の <input type=file> がある形。"""
    return re.compile(r"data-file-drop>\s*<input type=\"file\" name=\"" + re.escape(name) + r"\"[^>]*>")


def _manual_update(user) -> str:
    manual = manuals_services.create_manual(None, "作り物の段取りの手順", "steps", user)
    return reverse("manuals:manual_update", args=[manual.pk])


def _png(name: str = "WK-1001.png") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (200, 30, 30)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def _item_with_drawing(user) -> Item:
    return masters_services.create_item({"part_no": "WK-1001", "name": "作り物の部品", "drawing": _png()}, user)


def _item_update(user) -> str:
    return reverse("masters:item_update", args=[_item_with_drawing(user).pk])


@pytest.mark.parametrize(
    ("url_of", "name"),
    [
        (lambda user: reverse("masters:item_create"), "drawing"),
        (lambda user: reverse("feedback:feedback_create"), "screenshots"),
        (lambda user: reverse("intake:extraction_create"), "file"),
        (_manual_update, "file"),
        (_item_update, "drawing"),
    ],
    ids=["部品の図面", "フィードバックの画面の絵", "注文書のファイル", "マニュアルの画像", "図面のある部品の編集"],
)
def test_file_input_sits_in_drop_box(logged_in, user, monkeypatch, url_of, name):
    url = url_of(user)
    if url == reverse("intake:extraction_create"):
        monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)  # 鍵が無いと入力のカードを描かない (C09)
    response = logged_in.get(url)
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


# 編集の画面の今のファイル: 「現在:」の link と「クリア」を枠の上に出す (初見テスト R5 の a-flagged-13。候補 C40 の その 2)


def _above_box(html: str) -> str:
    """ファイルの欄の包み (data-file-field) の開きから、枠 (data-file-drop) の開きまでの HTML。"""
    match = re.search(r"data-file-field>(.*?)<div [^>]*data-file-drop>", html, re.S)
    assert match
    return match.group(1)


def _tag(html: str, pattern: str) -> str:
    match = re.search(r"<" + pattern + r"[^>]*>", html)
    assert match
    return match.group(0)


def _edit_post(item: Item, **extra) -> dict:
    return {"part_no": item.part_no, "name": item.name, "active": "on", **extra}


@pytest.mark.django_db
def test_current_file_row_sits_above_box(logged_in, user):
    item = _item_with_drawing(user)
    stored = item.drawing.name
    assert stored.startswith("drawings/")
    filename = stored.rsplit("/", 1)[-1]
    html = logged_in.get(reverse("masters:item_update", args=[item.pk])).content.decode()

    above = _above_box(html)
    assert "現在:" in above
    link = re.search(r"<a ([^>]*)>(.*?)</a>", above, re.S)
    assert link
    attrs, text = link.groups()
    assert 'class="link link-primary' in attrs
    assert 'target="_blank"' in attrs and 'rel="noopener"' in attrs
    assert f'href="{item.drawing.url}"' in attrs
    shown = re.sub(r"<[^>]+>", "", text).strip()
    assert shown == filename  # 名前だけ (drawings/ の無い字)
    assert "drawings/" not in shown
    assert "変更:" not in html

    clear = _tag(above, r'input type="checkbox"[^>]*name="drawing-clear"')
    assert 'id="drawing-clear_id"' in clear
    assert 'class="checkbox"' in clear
    assert " checked" not in clear
    assert 'for="drawing-clear_id"' in above and "クリア" in above

    file_input = _tag(html, r'input type="file" name="drawing"')
    assert "value=" not in file_input
    assert "現在:" not in html.split("data-file-drop>", 1)[1].split("</div>", 1)[0]  # 枠の中に「現在:」は無い


@pytest.mark.django_db
def test_new_item_has_no_current_row(logged_in):
    html = logged_in.get(reverse("masters:item_create")).content.decode()

    assert "現在:" not in html
    assert "drawing-clear" not in html


@pytest.mark.django_db
def test_swapped_widget_keeps_type_and_required(logged_in, user):
    item = _item_with_drawing(user)
    form = logged_in.get(reverse("masters:item_update", args=[item.pk])).context["form"]

    widget = form.fields["drawing"].widget
    assert isinstance(widget, ClearableFileWidget)
    assert form["drawing"].widget_type == "clearablefile"
    assert widget.is_required is form.fields["drawing"].required is False
    assert widget.attrs["class"] == "file-input w-full"


@pytest.mark.django_db
def test_clear_check_removes_drawing(logged_in, user):
    item = _item_with_drawing(user)
    response = logged_in.post(reverse("masters:item_update", args=[item.pk]), _edit_post(item, **{"drawing-clear": "on"}))

    assert response.status_code == 302
    item.refresh_from_db()
    assert not item.drawing


@pytest.mark.django_db
def test_clear_with_new_file_stays_with_check(logged_in, user):
    item = _item_with_drawing(user)
    before = item.drawing.name
    data = _edit_post(item, **{"drawing-clear": "on", "drawing": _png("WK-1001-2.png")})
    response = logged_in.post(reverse("masters:item_update", args=[item.pk]), data)

    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="id_drawing_error"' in html
    clear = _tag(_above_box(html), r'input type="checkbox"[^>]*name="drawing-clear"')
    assert " checked" in clear
    item.refresh_from_db()
    assert item.drawing.name == before


@pytest.mark.django_db
def test_company_logo_row_sits_above_box_and_clears(client, django_user_model):
    """会社の情報のロゴも同じ土台なので同じ形 (E2E の tests/e2e/test_e44_login_logo.py が押す logo-clear の name を保つ)。"""
    admin = django_user_model.objects.create_superuser("admin", password="pass-for-tests-only")
    client.force_login(admin)
    core_services.update_company(core_services.company(), {"logo": _png("logo.png")}, admin)
    url = reverse("core:company_update")
    html = client.get(url).content.decode()

    above = _above_box(html)
    assert "現在:" in above and "company/" not in re.sub(r'href="[^"]*"', "", above)
    clear = _tag(above, r'input type="checkbox"[^>]*name="logo-clear"')
    assert 'id="logo-clear_id"' in clear and 'class="checkbox"' in clear

    response = client.post(url, {"address": "", "phone": "", "bank_account": "", "logo-clear": "on"})
    assert response.status_code == 302
    assert not Company.objects.get().logo
