"""書式つきのメモの画面と口 (N114。決め 11-04 14:5x) の試験。

見る所: 指示書と受注の詳細の下のメモの枠 (閲覧の形と「書く」)、記入の形 (Quill の箱) と保存、写真の口 (JSON)、
記入と写真の口が編集の権限 (access.require_write) とログインを見ること。
見ない所 (理由): Quill の動き (打つ・道具の列・写真を落とす・htmx の入れ替え) はブラウザが要る (本体が画面で見る。
「書く」の後の箱の focus は tests/e2e/test_e54_note_focus.py が見る)。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from seizou.core import access
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.notes import services
from seizou.notes.models import Note, NoteImage
from seizou.orders import services as orders_services
from seizou.workorders import services as workorders_services

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
HINT = "メモは 1 つで、この詳細を開いた人が読めます。書き足すときも直すときも「書く」から保存し、保存すると前の字と置き換わります。"
EMPTY_HELP = "「書く」から、次に作業する人への申し送り・段取りの注意・現場の写真を残せます。"


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("note-writer", password="pass-for-tests-only")


@pytest.fixture
def order(user):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=date(2026, 11, 2),
    )


@pytest.fixture
def workorder(user, order):
    cut = Process.objects.create(code="CUT", name="切断", sort_order=1)
    return workorders_services.issue_workorder(
        order.lines.get(), Decimal("4"), [{"process": cut, "planned_qty": Decimal("4")}], user
    )


def target_data(obj) -> dict:
    return {"content_type": ContentType.objects.get_for_model(obj).pk, "object_id": obj.pk}


def png(name="現場.png"):
    return SimpleUploadedFile(name, PNG_BYTES, content_type="image/png")


@pytest.mark.django_db
def test_note_panel_on_workorder_and_order_detail(client_logged_in, user, order, workorder):
    pages = [
        (reverse("workorders:workorder_detail", args=[workorder.pk]), workorder),
        (reverse("orders:order_detail", args=[order.pk]), order),
    ]
    for url, obj in pages:
        html = client_logged_in.get(url).content.decode()
        assert html.count('<section id="notes"') == 1, url
        assert "メモはまだありません。" in html, url
        data = target_data(obj)
        assert f'{reverse("notes:note_edit")}?content_type={data["content_type"]}&amp;object_id={obj.pk}' in html, url
        assert 'src="/static/vendor/quill.js" defer' in html
        assert 'src="/static/notes/note-editor.js" defer' in html

    services.save_note(order, "<h2>段取り</h2><p>注意<script>alert(1)</script></p>", user)
    html = client_logged_in.get(reverse("orders:order_detail", args=[order.pk])).content.decode()
    assert '<div class="ql-editor"><h2>段取り</h2><p>注意</p></div>' in html
    assert "メモはまだありません。" not in html
    assert "alert(1)" not in html
    # 受注の明細の表は今のまま、メモの枠は明細の後
    assert html.index('id="order-lines-title"') < html.index('<section id="notes"')


@pytest.mark.django_db
def test_edit_form_and_save_returns_to_detail(client_logged_in, user, order):
    services.save_note(order, "<p>前のメモ</p>", user)
    edit = client_logged_in.get(reverse("notes:note_edit"), target_data(order), HTTP_HX_REQUEST="true")
    html = edit.content.decode()
    assert edit.status_code == 200
    assert html.count('<section id="notes"') == 1
    assert "data-note-editor" in html
    assert '<div data-note-box><p>前のメモ</p></div>' in html
    assert 'class="ql-header" aria-label="見出し"' in html
    assert 'class="ql-undo" aria-label="元に戻す"' in html
    assert 'name="csrfmiddlewaretoken"' in html
    assert f'href="{reverse("orders:order_detail", args=[order.pk])}#notes"' in html  # やめる

    response = client_logged_in.post(
        reverse("notes:note_edit"), {**target_data(order), "body_html": "<p><em>新しい</em>メモ</p>"}, HTTP_HX_REQUEST="true"
    )
    note = Note.objects.get()
    assert response.status_code == 200
    assert response["HX-Redirect"] == reverse("notes:note_detail", args=[note.pk])
    assert note.body_html == "<p><em>新しい</em>メモ</p>"
    assert note.updated_by == user
    back = client_logged_in.get(response["HX-Redirect"])
    assert back.status_code == 302
    assert back["Location"] == f'{reverse("orders:order_detail", args=[order.pk])}#notes'


def _p_class(html: str, text: str) -> str:
    """text で始まる <p> の class の字 (無ければ空)。"""
    match = re.search(r'<p class="([^"]*)">' + re.escape(text), html)
    return match.group(1) if match else ""


@pytest.mark.django_db
def test_note_hint_in_view_and_form(client_logged_in, user, order, workorder):
    """メモの枠の 3 か所 (空の枠・保存の後の枠・記入の形) に、上書きと読める人を言う 1 文が 1 つずつ出る (初見テスト R5 の V108・V109。候補 C42-先 の 2・3)。

    1 文の class は、空の時の説明の行と「更新 日時 人」の行と同じ字 (字の大きさと色をそろえる。新しい class は使わない)。
    """
    for url in (
        reverse("workorders:workorder_detail", args=[workorder.pk]),
        reverse("orders:order_detail", args=[order.pk]),
    ):
        html = client_logged_in.get(url).content.decode()
        assert html.count(HINT) == 1, url
        assert EMPTY_HELP in html, url
        assert _p_class(html, HINT) == _p_class(html, EMPTY_HELP) != "", url

    services.save_note(order, "<p>前のメモ</p>", user)
    html = client_logged_in.get(reverse("orders:order_detail", args=[order.pk])).content.decode()
    assert html.count(HINT) == 1
    assert EMPTY_HELP not in html
    assert _p_class(html, HINT) == _p_class(html, "更新 ") != ""  # 「更新 日時 人」の行は残る

    edit = client_logged_in.get(reverse("notes:note_edit"), target_data(order), HTTP_HX_REQUEST="true")
    html = edit.content.decode()
    assert html.count(HINT) == 1
    notes = html[html.index('<section id="notes"') :]
    assert notes.index("data-note-editor") < notes.index(HINT) < notes.index("</form>")  # 箱の下、form の中


@pytest.mark.django_db
def test_image_upload_returns_media_url(client_logged_in, user, order):
    response = client_logged_in.post(reverse("notes:noteimage_upload"), {**target_data(order), "file": png()})

    assert response.status_code == 200
    url = response.json()["url"]
    image = NoteImage.objects.get()
    assert url == image.file.url
    assert url.startswith("/media/notes/") and url.endswith(".png")
    assert "現場" not in url
    assert image.original_name == "現場.png"
    assert image.note.content_type == ContentType.objects.get_for_model(order)
    assert image.note.object_id == order.pk
    assert image.note.body_html == ""
    assert client_logged_in.get(url).status_code == 200  # /media/ の口 (ログインした人に返す)
    # 返した URL は掃除を通る (本文の <img src> に入れられる)
    assert f'<img src="{url}">' in services.save_note(order, f'<p><img src="{url}"></p>', user).body_html

    bad = client_logged_in.post(
        reverse("notes:noteimage_upload"), {**target_data(order), "file": SimpleUploadedFile("x.svg", b"<svg/>")}
    )
    assert bad.status_code == 400
    assert bad.json() == {"error": "保存できません。" + services.MESSAGE_BAD_IMAGE}
    missing = client_logged_in.post(reverse("notes:noteimage_upload"), {"content_type": 0, "object_id": 0, "file": png()})
    assert missing.status_code == 404
    assert client_logged_in.get(reverse("notes:noteimage_upload")).status_code == 405
    assert NoteImage.objects.count() == 1


@pytest.mark.django_db
def test_write_paths_check_edit_permission(client, client_logged_in, user, order, monkeypatch):
    calls = []

    def refuse(who, action):
        calls.append(action)
        raise access.AccessDenied("作り物の断る文です。")

    monkeypatch.setattr(access, "require_write", refuse)
    saved = client_logged_in.post(reverse("notes:note_edit"), {**target_data(order), "body_html": "<p>x</p>"})
    image = client_logged_in.post(reverse("notes:noteimage_upload"), {**target_data(order), "file": png()})

    assert calls == ["notes.save_note", "notes.add_image"]
    assert "作り物の断る文です。" in saved.content.decode()
    assert image.status_code == 403
    assert image.json() == {"error": "作り物の断る文です。"}
    assert Note.objects.count() == 0
    assert NoteImage.objects.count() == 0
    # 2 つの action は権限の要らない物 (現場の記録と同じ扱い。本体の P3)
    assert {"notes.save_note", "notes.add_image"} <= access.FREE_ACTIONS

    client.logout()
    for url in (reverse("notes:note_edit"), reverse("notes:noteimage_upload")):
        response = client.post(url, {**target_data(order), "body_html": "<p>x</p>"})
        assert response.status_code == 302
        assert reverse("core:login") in response["Location"]
    assert Note.objects.count() == 0
