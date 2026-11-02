"""図面のファイルをログインした人にだけ返す口 (N29。05 の 7 節と 06 の E-24)。

URL は /masters/items/<pk>/drawing/original<拡張子> と preview.png。GET の version (版) と page (表示用のページ)。
部品の詳細と小窓の図面の URL は全部この口になり、media の URL を出さない。
"""

import io

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image, ImageDraw

from seizou.masters import services


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


def png_bytes(size=(60, 40)):
    return image_bytes(Image.new("RGB", size, (200, 30, 30)), "PNG")


PDF_BYTES = b"%PDF-1.4\n%%EOF\n"


def upload(name, content, content_type="application/octet-stream"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def new_item(user, drawing=None, part_no="P-9201"):
    data = {"part_no": part_no, "name": "作り物の部品"}
    if drawing is not None:
        data["drawing"] = drawing
    return services.create_item(data, user)


def gate(item, filename, **query):
    url = reverse("masters:item_drawing", args=[item.pk, filename])
    if query:
        url += "?" + "&".join(f"{key}={value}" for key, value in query.items())
    return url


def body_of(response):
    content = b"".join(response.streaming_content)
    response.close()
    return content


def stored(media, field_file):
    return (media / field_file.name).read_bytes()


def items_of_each_kind(user):
    return {
        "tiff": new_item(user, upload("scan.tif", g4_tiff(), "image/tiff"), part_no="P-9211"),
        "png": new_item(user, upload("photo.png", png_bytes(), "image/png"), part_no="P-9212"),
        "pdf": new_item(user, upload("paper.pdf", PDF_BYTES, "application/pdf"), part_no="P-9213"),
        "none": new_item(user, part_no="P-9214"),
        "broken": new_item(user, upload("broken.tif", b"broken" * 50), part_no="P-9215"),
    }


@pytest.mark.django_db
def test_drawing_requires_login(client, user):
    item = new_item(user, upload("scan.tif", g4_tiff(), "image/tiff"))
    for filename in ("original.tif", "preview.png"):
        response = client.get(gate(item, filename))
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("core:login"))
        assert response.content == b""


@pytest.mark.django_db
def test_original_and_preview_are_served(logged_in, user, media):
    items = items_of_each_kind(user)
    tiff = items["tiff"]

    original = logged_in.get(gate(tiff, "original.tif"))
    assert original.status_code == 200
    assert original["Content-Type"] == "image/tiff"
    assert original["Content-Disposition"] == 'inline; filename="P-9211-v1.tif"'
    assert body_of(original) == stored(media, tiff.drawing)

    preview = logged_in.get(gate(tiff, "preview.png"))
    assert preview.status_code == 200
    assert preview["Content-Type"] == "image/png"
    assert preview["Content-Disposition"] == 'inline; filename="P-9211-v1-p1.png"'
    assert body_of(preview) == stored(media, tiff.drawing_preview)

    png = logged_in.get(gate(items["png"], "original.png"))
    assert png["Content-Type"] == "image/png"
    assert body_of(png) == stored(media, items["png"].drawing)
    pdf = logged_in.get(gate(items["pdf"], "original.pdf"))
    assert pdf["Content-Type"] == "application/pdf"
    assert pdf["Content-Disposition"] == 'inline; filename="P-9213-v1.pdf"'
    assert body_of(pdf) == PDF_BYTES


@pytest.mark.django_db
def test_wrong_or_missing_is_404(logged_in, user):
    items = items_of_each_kind(user)
    tiff = items["tiff"]
    urls = [
        gate(tiff, "original.png"),
        gate(tiff, "original.pdf"),
        gate(tiff, "thumbnail.png"),
        gate(tiff, "original"),
        gate(items["png"], "preview.png"),
        gate(items["pdf"], "preview.png"),
        gate(items["broken"], "preview.png"),
        gate(items["none"], "original"),
        gate(tiff, "preview.png", page=2),
        gate(tiff, "preview.png", page=0),
        gate(tiff, "preview.png", page="x"),
        gate(tiff, "original.tif", version=9),
        gate(tiff, "original.tif", version="x"),
        gate(tiff, "preview.png", version=9),
        reverse("masters:item_drawing", args=[999999, "original.tif"]),
    ]
    for url in urls:
        assert logged_in.get(url).status_code == 404, url


@pytest.mark.django_db
def test_version_files_are_served(logged_in, user, media):
    first = g4_tiff((400, 300))
    item = new_item(user, upload("first.tif", first, "image/tiff"))
    old_preview = stored(media, item.drawing_preview)
    services.update_item(item, {"drawing": upload("second.tif", g4_tiff((320, 240)), "image/tiff")}, user)

    original = logged_in.get(gate(item, "original.tif", version=1))
    assert original.status_code == 200
    assert original["Content-Disposition"] == 'inline; filename="P-9201-v1.tif"'
    assert body_of(original) == first
    preview = logged_in.get(gate(item, "preview.png", version=1))
    assert preview["Content-Disposition"] == 'inline; filename="P-9201-v1-p1.png"'
    assert body_of(preview) == old_preview

    current = logged_in.get(gate(item, "original.tif"))
    assert current["Content-Disposition"] == 'inline; filename="P-9201-v2.tif"'
    content = body_of(current)
    assert content == stored(media, item.drawing)
    assert content != first


@pytest.mark.django_db
def test_unsafe_part_no_falls_back(logged_in, user):
    for number, part_no in enumerate(("部品 1", 'P"1;x')):
        item = new_item(user, upload("scan.tif", g4_tiff(), "image/tiff"), part_no=part_no)
        response = logged_in.get(gate(item, "preview.png"))
        assert response["Content-Disposition"] == 'inline; filename="drawing-v1-p1.png"', number
        body_of(response)


@pytest.mark.django_db
def test_other_extension_is_attachment(logged_in, user):
    item = new_item(user, upload("shape.DXF", b"0\nSECTION\n0\nEOF\n"))
    assert item.drawing.name.endswith(".DXF")
    response = logged_in.get(gate(item, "original.dxf"))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/octet-stream"
    assert response["Content-Disposition"] == 'attachment; filename="P-9201-v1.dxf"'
    body_of(response)
    assert item.file_url("drawing") == gate(item, "original.dxf")


@pytest.mark.django_db
def test_no_store(logged_in, user):
    item = new_item(user, upload("scan.tif", g4_tiff(), "image/tiff"))
    for filename in ("original.tif", "preview.png"):
        response = logged_in.get(gate(item, filename))
        assert "no-store" in response["Cache-Control"]
        body_of(response)


@pytest.mark.django_db
def test_file_url_only_for_drawing_fields(user):
    items = items_of_each_kind(user)
    tiff = items["tiff"]
    assert tiff.file_url("drawing") == reverse("masters:item_drawing", args=[tiff.pk, "original.tif"])
    assert tiff.file_url("drawing_preview") == reverse("masters:item_drawing", args=[tiff.pk, "preview.png"])
    assert tiff.file_url("note") is None
    assert items["none"].file_url("drawing") is None
    assert items["png"].file_url("drawing_preview") is None
    assert tiff.display_drawing_url == reverse("masters:item_drawing", args=[tiff.pk, "preview.png"])
    assert items["png"].display_drawing_url == reverse("masters:item_drawing", args=[items["png"].pk, "original.png"])
    for kind in ("pdf", "none", "broken"):
        assert items[kind].display_drawing_url is None, kind


@pytest.mark.django_db
def test_detail_and_modal_have_no_media_urls(logged_in, user):
    items = items_of_each_kind(user)
    expected = {"tiff": "original.tif", "png": "original.png", "pdf": "original.pdf"}
    for kind, filename in expected.items():
        item = items[kind]
        detail = logged_in.get(reverse("masters:item_detail", args=[item.pk])).content.decode()
        modal = logged_in.get(reverse("masters:item_modal", args=[item.pk]), HTTP_HX_REQUEST="true").content.decode()
        assert "/media/" not in detail and "/media/" not in modal, kind
        assert gate(item, filename) in detail, kind
    tiff_modal = logged_in.get(reverse("masters:item_modal", args=[items["tiff"].pk]), HTTP_HX_REQUEST="true")
    assert f'<img src="{gate(items["tiff"], "preview.png")}"' in tiff_modal.content.decode()
