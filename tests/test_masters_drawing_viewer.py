"""図面ビューアと、図面を版に戻す確認の画面 (N29 の 2 段目)。

ビューア (masters:item_drawing_viewer) は部品の図面を口の URL で出し、拡大・縮小・原寸・回転・移動は JS、
ページ送り・版の選択・見比べるは link と GET の form。版に戻す (masters:item_drawing_restore) は土台の入力の画面。
試験は Django の test client で HTML と表の状態を見る (ブラウザの動きは見ない)。
"""

import io
import re

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image, ImageDraw

from seizou.masters import services
from seizou.masters.models import Item


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def line_drawing(size):
    image = Image.new("1", size, 1)
    draw = ImageDraw.Draw(image)
    draw.line((0, 0, size[0] - 1, size[1] - 1), fill=0)
    return image


def image_bytes(image, fmt, **options):
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def g4_tiff(size=(400, 300)):
    return image_bytes(line_drawing(size), "TIFF", compression="group4")


def pages_tiff(sizes):
    first, *rest = [line_drawing(size) for size in sizes]
    return image_bytes(first, "TIFF", save_all=True, append_images=rest, compression="group4")


def png_bytes(size=(60, 40)):
    return image_bytes(Image.new("RGB", size, (200, 30, 30)), "PNG")


def upload(name, content, content_type="application/octet-stream"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def new_item(user, drawing=None, part_no="P-9301"):
    data = {"part_no": part_no, "name": "作り物の部品"}
    if drawing is not None:
        data["drawing"] = drawing
    return services.create_item(data, user)


def tiff_item(user, part_no="P-9301", size=(400, 300)):
    return new_item(user, upload("scan.tif", g4_tiff(size), "image/tiff"), part_no=part_no)


def viewer_url(item, query=""):
    return reverse("masters:item_drawing_viewer", args=[item.pk]) + query


def restore_url(item):
    return reverse("masters:item_drawing_restore", args=[item.pk])


def gate(item, filename):
    return reverse("masters:item_drawing", args=[item.pk, filename])


def html(response):
    assert response.status_code == 200
    return response.content.decode()


def image_sources(body):
    return re.findall(r'<img data-drawing-image src="([^"]+)"', body)


def unescape(url):
    return url.replace("&amp;", "&")


def png_size(client, url):
    response = client.get(url)
    assert response.status_code == 200, url
    content = b"".join(response.streaming_content)  # 読み切ると test client が close() を呼ぶ (自分で呼ばない)
    with Image.open(io.BytesIO(content)) as image:
        return image.format, image.size


# ---------------------------------------------------------------- ビューア


@pytest.mark.django_db
def test_viewer_requires_login(client, user):
    item = tiff_item(user)
    response = client.get(viewer_url(item))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("core:login"))


@pytest.mark.django_db
def test_viewer_shows_tools_and_gated_image(logged_in, user):
    item = tiff_item(user)
    body = html(logged_in.get(viewer_url(item)))
    for word in ("拡大", "縮小", "原寸", "回転", "図面ビューア", "ダウンロード", "詳細に戻る"):
        assert word in body, word
    for action in ("zoom-in", "zoom-out", "actual", "rotate"):
        assert f'data-action="{action}"' in body
    assert "data-drawing-viewer" in body
    assert image_sources(body) == [gate(item, "preview.png")]
    assert "/media/" not in body
    assert re.search(r'<script src="[^"]*masters/drawing-viewer\.js" defer></script>', body)
    assert f'href="{gate(item, "original.tif")}" download' in body
    assert f'href="{reverse("masters:item_detail", args=[item.pk])}"' in body
    assert "ページ 1 / 1" in body
    assert "btn-primary" not in body.split("<main", 1)[1]  # 主色のボタンを置かない
    assert "前の版はありません。図面を差し替えると、前の図面がここに残ります。" in body
    assert 'name="version"' not in body  # 版が無ければ選ぶ form を出さない


@pytest.mark.django_db
def test_viewer_png_uses_original(logged_in, user):
    item = new_item(user, upload("photo.png", png_bytes(), "image/png"))
    body = html(logged_in.get(viewer_url(item)))
    assert image_sources(body) == [gate(item, "original.png")]


@pytest.mark.django_db
def test_viewer_pages(logged_in, user):
    item = new_item(user, upload("pages.tif", pages_tiff([(300, 200), (500, 400), (240, 360)]), "image/tiff"))
    body = html(logged_in.get(viewer_url(item)))
    assert "ページ 1 / 3" in body
    assert f'href="{viewer_url(item, "?page=2")}">次のページ' in body
    assert "前のページ" not in body

    last = html(logged_in.get(viewer_url(item, "?page=3")))
    assert "ページ 3 / 3" in last
    assert image_sources(last) == [gate(item, "preview.png") + "?page=3"]
    assert "次のページ" not in last
    assert f'href="{viewer_url(item, "?page=2")}">' in last and "前のページ" in last

    # 読めないページは 1 ページ目にする (画面は誤りにしない)
    for query in ("?page=9", "?page=x", "?page=0"):
        assert "ページ 1 / 3" in html(logged_in.get(viewer_url(item, query)))


@pytest.mark.django_db
def test_viewer_versions(logged_in, user):
    item = tiff_item(user)
    services.update_item(item, {"drawing": upload("second.png", png_bytes(), "image/png")}, user)
    services.update_item(item, {"drawing": upload("third.tif", g4_tiff((320, 240)), "image/tiff")}, user)
    body = html(logged_in.get(viewer_url(item)))
    assert "版 3 (今の図面)" in body
    rows = re.findall(r"<td class=\"whitespace-nowrap tabular-nums\">(版 \d+(?: \(今の図面\))?)</td>", body)
    assert rows == ["版 3 (今の図面)", "版 2", "版 1"]
    assert '<option value="2">版 2</option>' in body and '<option value="1">版 1</option>' in body
    assert body.count('aria-current="true"') == 1
    assert f'href="{viewer_url(item, "?version=1")}"' in body  # 版の表の「見る」
    assert f'href="{restore_url(item)}"' in body and "版に戻す" in body

    old = html(logged_in.get(viewer_url(item, "?version=1")))
    assert [unescape(src) for src in image_sources(old)] == [gate(item, "preview.png") + "?version=1"]
    assert '<option value="1" selected>版 1</option>' in old
    assert f'href="{gate(item, "original.tif")}?version=1" download' in old
    second = html(logged_in.get(viewer_url(item, "?version=2")))
    assert [unescape(src) for src in image_sources(second)] == [gate(item, "original.png") + "?version=2"]
    # 読めない・無い版は今の図面にする
    assert image_sources(html(logged_in.get(viewer_url(item, "?version=9")))) == [gate(item, "preview.png")]


@pytest.mark.django_db
def test_viewer_compare(logged_in, user):
    item = tiff_item(user)
    services.update_item(item, {"drawing": upload("second.tif", g4_tiff((320, 240)), "image/tiff")}, user)
    plain = html(logged_in.get(viewer_url(item)))
    assert "見比べる" not in plain and "1 枚で見る" not in plain

    one = html(logged_in.get(viewer_url(item, "?version=1")))
    assert "見比べる" in one
    assert unescape(viewer_url(item, "?version=1&compare=1")) in unescape(one)

    both = html(logged_in.get(viewer_url(item, "?version=1&compare=1")))
    sources = [unescape(src) for src in image_sources(both)]
    assert sources == [gate(item, "preview.png"), gate(item, "preview.png") + "?version=1"]
    assert both.count("data-drawing-viewer") == 1  # 2 枚は同じ箱の中 (一緒に動く)
    assert "1 枚で見る" in both and f'href="{viewer_url(item, "?version=1")}">1 枚で見る' in both
    assert "版 2 (今の図面)" in both


@pytest.mark.django_db
def test_viewer_pdf_uses_iframe(logged_in, user):
    item = new_item(user, upload("paper.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"))
    body = html(logged_in.get(viewer_url(item)))
    assert f'<iframe src="{gate(item, "original.pdf")}"' in body
    assert "PDF はブラウザの表示を使います。" in body
    assert 'data-action="zoom-in"' not in body
    assert "drawing-viewer.js" not in body
    assert "data-drawing-image" not in body


@pytest.mark.django_db
def test_viewer_without_drawing(logged_in, user):
    item = new_item(user)
    body = html(logged_in.get(viewer_url(item)))
    assert "図面がありません。" in body
    main = body.split("<main", 1)[1]
    assert "<img" not in main and "data-action" not in main
    assert "drawing-viewer.js" not in body
    assert "ダウンロード" not in main


@pytest.mark.django_db
def test_viewer_broken_drawing(logged_in, user):
    item = new_item(user, upload("broken.tif", b"broken" * 50))
    body = html(logged_in.get(viewer_url(item)))
    assert 'class="alert alert-warning alert-soft mt-4" role="status"' in body
    assert services.DRAWING_BROKEN_MESSAGE in body
    assert "ダウンロード" in body and f'href="{gate(item, "original.tif")}" download' in body
    assert "<img" not in body.split("<main", 1)[1]
    assert "drawing-viewer.js" not in body


@pytest.mark.django_db
def test_detail_and_modal_lead_to_viewer(logged_in, user):
    item = tiff_item(user)
    detail = html(logged_in.get(reverse("masters:item_detail", args=[item.pk])))
    assert f'href="{viewer_url(item)}"' in detail and "図面ビューア" in detail
    assert detail.count("btn btn-primary") == 1  # 主色は土台の「編集」の 1 つのまま
    # 詳細のファイルの節の絵は、土台のまま口の表示用 (新しいタブ)
    assert f'<img src="{gate(item, "preview.png")}"' in detail

    plain = new_item(user, part_no="P-9302")
    plain_detail = html(logged_in.get(reverse("masters:item_detail", args=[plain.pk])))
    assert viewer_url(plain) not in plain_detail and "図面ビューア" not in plain_detail

    modal = html(logged_in.get(reverse("masters:item_modal", args=[item.pk]), HTTP_HX_REQUEST="true"))
    assert f'href="{viewer_url(item)}"><img src="{gate(item, "preview.png")}"' in modal
    assert 'target="_blank"' not in modal


# ---------------------------------------------------------------- 複数ページの表示用の PNG


@pytest.mark.django_db
def test_multipage_tiff_makes_page_pngs(logged_in, user):
    item = new_item(user, upload("pages.tif", pages_tiff([(300, 200), (500, 400), (240, 360)]), "image/tiff"))
    item.refresh_from_db()
    assert item.drawing_page_count == 3
    assert png_size(logged_in, gate(item, "preview.png")) == ("PNG", (300, 200))
    assert png_size(logged_in, gate(item, "preview.png") + "?page=2") == ("PNG", (500, 400))
    assert png_size(logged_in, gate(item, "preview.png") + "?page=3") == ("PNG", (240, 360))
    assert logged_in.get(gate(item, "preview.png") + "?page=4").status_code == 404


@pytest.mark.django_db
def test_page_limit(logged_in, user, monkeypatch):
    monkeypatch.setattr(services, "DRAWING_MAX_PAGES", 2)
    item = new_item(user, upload("pages.tif", pages_tiff([(300, 200), (500, 400), (240, 360)]), "image/tiff"))
    item.refresh_from_db()
    assert item.drawing_page_count == 2
    assert logged_in.get(gate(item, "preview.png") + "?page=3").status_code == 404


@pytest.mark.django_db
def test_versions_keep_pages(logged_in, user):
    item = new_item(user, upload("pages.tif", pages_tiff([(300, 200), (500, 400), (240, 360)]), "image/tiff"))
    services.update_item(item, {"drawing": upload("photo.png", png_bytes(), "image/png")}, user)
    item.refresh_from_db()
    assert item.drawing_page_count == 1
    version = item.drawing_versions.get(version_no=1)
    assert version.page_count == 3
    assert png_size(logged_in, gate(item, "preview.png") + "?version=1&page=3") == ("PNG", (240, 360))
    body = html(logged_in.get(viewer_url(item, "?version=1&page=3")))
    assert "ページ 3 / 3" in body


@pytest.mark.django_db
def test_pdf_has_one_page(logged_in, user):
    item = new_item(user, upload("paper.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"))
    item.refresh_from_db()
    assert item.drawing_page_count == 1
    assert logged_in.get(gate(item, "preview.png")).status_code == 404


@pytest.mark.django_db
def test_page_name_taken_stops_pages(logged_in, user, media):
    # 3 ページ目の名前のファイル (ほかの図面の物) が置き場に既にあれば、そのページから後を作らない
    taken = media / "drawings" / "previews" / "pages-p3.png"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(png_bytes((10, 10)))
    item = new_item(user, upload("pages.tif", pages_tiff([(300, 200), (500, 400), (240, 360)]), "image/tiff"))
    item.refresh_from_db()
    assert item.drawing_preview.name == "drawings/previews/pages.png"
    assert item.drawing_page_count == 2
    assert logged_in.get(gate(item, "preview.png") + "?page=3").status_code == 404
    assert taken.read_bytes() == png_bytes((10, 10))


@pytest.mark.django_db
def test_gate_allows_same_origin_frame(logged_in, user, settings):
    settings.MIDDLEWARE = [*settings.MIDDLEWARE, "django.middleware.clickjacking.XFrameOptionsMiddleware"]
    item = new_item(user, upload("paper.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"))
    response = logged_in.get(gate(item, "original.pdf"))
    assert response["X-Frame-Options"] == "SAMEORIGIN"
    b"".join(response.streaming_content)  # 読み切って閉じる (close() を自分で呼ばない)


# ---------------------------------------------------------------- 版に戻す


def item_with_versions(user, part_no="P-9301"):
    item = tiff_item(user, part_no=part_no)
    services.update_item(item, {"drawing": upload("second.png", png_bytes(), "image/png")}, user)
    item.refresh_from_db()
    return item


@pytest.mark.django_db
def test_restore_requires_login(client, user):
    item = item_with_versions(user)
    before = (item.drawing.name, item.drawing_versions.count())
    version = item.drawing_versions.get(version_no=1)
    for response in (client.get(restore_url(item)), client.post(restore_url(item), {"version": version.pk})):
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("core:login"))
    item.refresh_from_db()
    assert (item.drawing.name, item.drawing_versions.count()) == before


@pytest.mark.django_db
def test_restore_confirm_lists_only_this_items_versions(logged_in, user):
    item = item_with_versions(user)
    other = item_with_versions(user, part_no="P-9399")
    body = html(logged_in.get(restore_url(item)))
    mine = item.drawing_versions.get(version_no=1)
    theirs = other.drawing_versions.get(version_no=1)
    assert f'<option value="{mine.pk}"' in body
    assert f'<option value="{theirs.pk}"' not in body
    assert "選んだ版の図面を今の図面に戻します。今の図面は新しい版として残り、ファイルは消しません。" in body
    assert "保存" in body and "戻す版" in body
    assert f'href="{viewer_url(item)}"' in body and "ビューアに戻る" in body


@pytest.mark.django_db
def test_restore_post_runs_service(logged_in, user):
    item = item_with_versions(user)
    version = item.drawing_versions.get(version_no=1)
    response = logged_in.post(restore_url(item), {"version": version.pk, "note": "差し替えを誤った"})
    assert response.status_code == 302
    assert response["Location"] == reverse("masters:item_detail", args=[item.pk])
    item.refresh_from_db()
    assert item.drawing.name == version.drawing.name
    assert item.drawing_versions.count() == 2
    assert item.drawing_versions.get(version_no=2).note == "差し替えを誤った"


@pytest.mark.django_db
def test_restore_other_items_version_is_form_error(logged_in, user):
    item = item_with_versions(user)
    other = item_with_versions(user, part_no="P-9399")
    before = (item.drawing.name, other.drawing.name, item.drawing_versions.count(), other.drawing_versions.count())
    theirs = other.drawing_versions.get(version_no=1)
    body = html(logged_in.post(restore_url(item), {"version": theirs.pk}))
    assert 'id="id_version_error"' in body
    item.refresh_from_db()
    other.refresh_from_db()
    after = (item.drawing.name, other.drawing.name, item.drawing_versions.count(), other.drawing_versions.count())
    assert after == before
    assert Item.objects.count() == 2


# 版が 0 件の「図面を版に戻す」(初見テスト R5 の a-flagged-23。候補 C40 の その 1。C40-2 = a「文を足すだけ」)

NO_VERSIONS = "前の版はありません。図面を差し替えると、前の図面がここに残ります。"


@pytest.mark.django_db
def test_restore_without_versions_shows_viewer_message(logged_in, user):
    item = tiff_item(user)
    assert item.drawing_versions.count() == 0
    body = html(logged_in.get(restore_url(item)))
    # 図面ビューアと 1 字も同じ文
    viewer = html(logged_in.get(viewer_url(item)))
    assert NO_VERSIONS in viewer
    assert NO_VERSIONS in body
    # 入力の枠は今のまま出る (C40-2 = a。止める形を足さない)
    assert 'id="id_version"' in body
    assert "保存" in body and "戻す版" in body
    assert "選んだ版の図面を今の図面に戻します。今の図面は新しい版として残り、ファイルは消しません。" in body
    assert f'href="{viewer_url(item)}"' in body and "ビューアに戻る" in body


@pytest.mark.django_db
def test_restore_with_versions_has_no_message(logged_in, user):
    item = item_with_versions(user)
    body = html(logged_in.get(restore_url(item)))
    assert NO_VERSIONS not in body


@pytest.mark.django_db
def test_restore_without_versions_post_keeps_drawing(logged_in, user):
    item = tiff_item(user)
    before = (item.drawing.name, item.drawing_versions.count())
    response = logged_in.post(restore_url(item), {"note": "版を付けずに保存"})
    # 詳細へ移らない (誤りの字は見ない。字と出し方は C25 と C57 が替える)
    assert response.status_code == 200
    item.refresh_from_db()
    assert (item.drawing.name, item.drawing_versions.count()) == before
