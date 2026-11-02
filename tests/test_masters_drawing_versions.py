"""図面の版 (N29)。部品の図面を差し替えたら前の図面を版に残し、選んだ版に戻せる。

版の行は、置き場に残した前の図面の名前の字を持つ (ファイルを写さない)。今の図面の版の番号は「版の番号の最大 + 1」。
試験の絵は Pillow でこの中で作る。置き場 (MEDIA_ROOT) は試験ごとの一時のフォルダ。
"""

import io
import re

import pytest
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.urls import reverse
from PIL import Image, ImageDraw

from seizou.masters import services
from seizou.masters.models import Item, ItemDrawingVersion

JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


def line_drawing(size):
    image = Image.new("1", size, 1)
    draw = ImageDraw.Draw(image)
    draw.line((0, 0, size[0] - 1, size[1] - 1), fill=0)
    draw.rectangle((10, 10, size[0] // 2, size[1] // 2), outline=0)
    return image


def image_bytes(image, fmt, **options):
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def g4_tiff(size=(400, 300)):
    return image_bytes(line_drawing(size), "TIFF", compression="group4")


def png_bytes(size=(60, 40)):
    return image_bytes(Image.new("RGB", size, (200, 30, 30)), "PNG")


def upload(name, content, content_type="application/octet-stream"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def new_item(user, drawing=None, part_no="P-9101"):
    data = {"part_no": part_no, "name": "作り物の部品"}
    if drawing is not None:
        data["drawing"] = drawing
    return services.create_item(data, user)


def tiff_upload(name="scan.tif", size=(400, 300)):
    return upload(name, g4_tiff(size), "image/tiff")


def png_upload(name="photo.png"):
    return upload(name, png_bytes(), "image/png")


def item_state(item):
    item.refresh_from_db()
    return (item.drawing.name, item.drawing_preview.name, item.drawing_preview_error, item.drawing_page_count)


def version_state(version):
    version.refresh_from_db()
    return (version.drawing.name, version.drawing_preview.name, version.drawing_preview_error, version.page_count)


def version_numbers(item):
    return sorted(item.drawing_versions.values_list("version_no", flat=True))


# ---------------------------------------------------------------- 版を残す


@pytest.mark.django_db
def test_replace_keeps_previous_as_version(user, media):
    item = new_item(user, tiff_upload("first.tif"))
    first = item_state(item)
    assert first[0].endswith(".tif") and first[1].endswith(".png")

    services.update_item(item, {"drawing": png_upload("second.png")}, user)
    assert version_numbers(item) == [1]
    version_1 = item.drawing_versions.get(version_no=1)
    assert version_state(version_1) == first  # 名前の字が同じ = ファイルを写していない
    assert not (media / "drawings" / "versions").exists()
    second = item_state(item)
    assert second[0].endswith(".png") and second[1] == ""

    services.update_item(item, {"drawing": tiff_upload("third.tif")}, user)
    assert version_numbers(item) == [1, 2]
    assert version_state(version_1) == first
    assert version_state(item.drawing_versions.get(version_no=2)) == second
    assert item.drawing_version_no == 3
    assert item.drawing_versions.get(version_no=2).note == ""


@pytest.mark.django_db
def test_no_version_without_previous_drawing(user):
    plain = new_item(user, part_no="P-9102")
    services.update_item(plain, {"drawing": tiff_upload()}, user)
    assert plain.drawing_versions.count() == 0
    assert plain.drawing_version_no == 1

    created = new_item(user, tiff_upload(), part_no="P-9103")
    assert created.drawing_versions.count() == 0
    assert ItemDrawingVersion.objects.count() == 0


@pytest.mark.django_db
def test_no_version_for_same_file_or_clear(user):
    item = new_item(user, tiff_upload())
    services.update_item(item, {"drawing": item.drawing, "name": "名前だけ直す"}, user)
    services.update_item(item, {"name": "品名だけ直す"}, user)
    services.update_item(item, {"drawing": item.drawing.name}, user)  # 名前の字 (CSV の取り込みの形)
    assert item.drawing_versions.count() == 0
    services.update_item(item, {"drawing": False}, user)
    assert item.drawing_versions.count() == 0
    assert item_state(item)[0] == ""

    for number, cleared in enumerate((None, "")):
        other = new_item(user, tiff_upload(), part_no=f"P-911{number}")
        services.update_item(other, {"drawing": cleared}, user)
        assert other.drawing_versions.count() == 0
    assert ItemDrawingVersion.objects.count() == 0


@pytest.mark.django_db
def test_demo_style_update_still_works(user, tmp_path):
    source = tmp_path / "seed" / "x.png"
    source.parent.mkdir()
    source.write_bytes(png_bytes())
    item = new_item(user)
    with source.open("rb") as handle:
        services.update_item(item, {"drawing": File(handle, name="x.png")}, user)
    item.refresh_from_db()
    assert item.drawing.name.endswith(".png")
    assert item.drawing_preview_error == ""
    assert item.drawing_versions.count() == 0


# ---------------------------------------------------------------- 版に戻す


@pytest.mark.django_db
def test_restore_makes_version_current(user):
    item = new_item(user, tiff_upload("first.tif"))
    services.update_item(item, {"drawing": png_upload("second.png")}, user)
    services.update_item(item, {"drawing": tiff_upload("third.tif", (320, 240))}, user)
    before = item_state(item)
    version_1 = item.drawing_versions.get(version_no=1)
    wanted = version_state(version_1)
    assert item.drawing_version_no == 3

    result = services.restore_drawing_version(item, version_1, user, note="差し替えを誤った")
    assert result == item
    assert item_state(item) == wanted
    assert version_numbers(item) == [1, 2, 3]
    version_3 = item.drawing_versions.get(version_no=3)
    assert version_state(version_3) == before
    assert version_3.note == "差し替えを誤った"
    assert version_state(version_1) == wanted  # 選んだ版の行は残る
    assert item.drawing_version_no == 4


@pytest.mark.django_db
def test_restore_without_current_drawing_makes_no_version(user):
    item = new_item(user, tiff_upload("first.tif"))
    services.update_item(item, {"drawing": png_upload("second.png")}, user)
    services.update_item(item, {"drawing": False}, user)
    version_1 = item.drawing_versions.get(version_no=1)
    services.restore_drawing_version(item, version_1, user)
    assert item_state(item) == version_state(version_1)
    assert version_numbers(item) == [1]


@pytest.mark.django_db
def test_restore_other_items_version_is_refused(user):
    first = new_item(user, tiff_upload("a1.tif"), part_no="P-9121")
    services.update_item(first, {"drawing": png_upload("a2.png")}, user)
    second = new_item(user, tiff_upload("b1.tif"), part_no="P-9122")
    services.update_item(second, {"drawing": png_upload("b2.png")}, user)
    states = (item_state(first), item_state(second))
    other = second.drawing_versions.get(version_no=1)

    with pytest.raises(ValueError, match="版 1 はこの部品の版ではありません。この部品の版を選んでください。"):
        services.restore_drawing_version(first, other, user)
    assert (item_state(first), item_state(second)) == states
    assert version_numbers(first) == [1] and version_numbers(second) == [1]


@pytest.mark.django_db
def test_version_no_per_item(user):
    first = new_item(user, tiff_upload(), part_no="P-9131")
    second = new_item(user, tiff_upload(), part_no="P-9132")
    services.update_item(first, {"drawing": png_upload()}, user)
    services.update_item(first, {"drawing": tiff_upload()}, user)
    services.update_item(second, {"drawing": png_upload()}, user)
    assert version_numbers(first) == [1, 2]
    assert version_numbers(second) == [1]
    assert first.drawing_version_no == 3
    assert second.drawing_version_no == 2
    assert [v.version_no for v in first.drawing_versions.all()] == [2, 1]  # 新しい版が上
    assert str(first.drawing_versions.get(version_no=1)) == "P-9131 版 1"


@pytest.mark.django_db
def test_page_count_is_not_an_item_key(user):
    with pytest.raises(ValueError, match="知らない項目"):
        services.create_item({"part_no": "P-9141", "name": "作り物", "drawing_page_count": 3}, user)
    item = new_item(user, part_no="P-9142")
    with pytest.raises(ValueError, match="知らない項目"):
        services.update_item(item, {"drawing_page_count": 3}, user)
    assert not Item.objects.filter(part_no="P-9141").exists()
    item.refresh_from_db()
    assert item.drawing_page_count == 1


def test_version_table_follows_masters_rules():
    assert JAPANESE.search(str(ItemDrawingVersion._meta.verbose_name))
    own = [field for field in ItemDrawingVersion._meta.fields if not field.auto_created]
    for field in own:
        assert JAPANESE.search(str(field.verbose_name)), field.name
        if isinstance(field, models.CharField):
            assert field.max_length == 500, field.name
    page_count = Item._meta.get_field("drawing_page_count")
    assert JAPANESE.search(str(page_count.verbose_name))
    assert page_count.editable is False and page_count.default == 1
    assert [field.name for field in Item._meta.concrete_fields][-1] == "drawing_page_count"


# ---------------------------------------------------------------- 詳細と小窓に出さない列


@pytest.mark.django_db
def test_detail_and_modal_hide_preview_fields(client, user):
    client.force_login(user)
    items = [
        new_item(user, tiff_upload(), part_no="P-9151"),
        new_item(user, upload("broken.tif", b"broken" * 50), part_no="P-9152"),
        new_item(user, part_no="P-9153"),
    ]
    for item in items:
        detail = client.get(reverse("masters:item_detail", args=[item.pk])).content.decode()
        modal = client.get(reverse("masters:item_modal", args=[item.pk]), HTTP_HX_REQUEST="true").content.decode()
        for body in (detail, modal):
            assert "図面ファイル" in body
            assert "図面の表示用の誤り" not in body
            assert "図面のページ数" not in body
