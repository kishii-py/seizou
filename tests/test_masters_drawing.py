"""部品の図面の画像と TIFF (05 の 7 節)。

保存のときに 1 回だけ、TIFF を表示用の PNG に変えて drawing_preview に置く。PNG と JPEG はそのまま、PDF は link だけ。
壊れたファイルと大きすぎる絵でも部品と元のファイルは保存し、理由を drawing_preview_error に残す。
試験の絵は Pillow でこの中で作る。置き場 (MEDIA_ROOT) は試験ごとの一時のフォルダ。
"""

import io
import random
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageDraw, ImageFile

from seizou.masters import services
from seizou.masters.models import Item


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


def line_drawing(size):
    """白黒 (1) の絵に線を 2 本描く (スキャンした図面の代わり)。"""
    image = Image.new("1", size, 1)
    draw = ImageDraw.Draw(image)
    draw.line((0, 0, size[0] - 1, size[1] - 1), fill=0)
    draw.rectangle((10, 10, size[0] // 2, size[1] // 2), outline=0)
    return image


def image_bytes(image, fmt, **options):
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def g4_tiff(size=(800, 600)):
    return image_bytes(line_drawing(size), "TIFF", compression="group4")


def upload(name, content, content_type="application/octet-stream"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def new_item(user, drawing=None, part_no="P-7001"):
    data = {"part_no": part_no, "name": "作り物の部品"}
    if drawing is not None:
        data["drawing"] = drawing
    return services.create_item(data, user)


def opened(field_file):
    with Image.open(field_file.path) as image:
        return image.format, image.size


def png_bytes(size=(60, 40)):
    return image_bytes(Image.new("RGB", size, (200, 30, 30)), "PNG")


def jpeg_bytes(size=(60, 40)):
    return image_bytes(Image.new("RGB", size, (30, 30, 200)), "JPEG")


def noisy_png(size=(200, 200)):
    data = random.Random(0).randbytes(size[0] * size[1] * 3)
    return image_bytes(Image.frombytes("RGB", size, data), "PNG")


# ---------------------------------------------------------------- 1 段目: 保存のときの変換


@pytest.mark.django_db
def test_tiff_makes_png_preview(user):
    item = new_item(user, upload("drawing.tif", g4_tiff(), "image/tiff"))
    item.refresh_from_db()
    assert item.drawing.name.endswith(".tif")
    assert item.drawing_preview.name.endswith(".png")
    assert item.drawing_preview.name.startswith("drawings/previews/")
    assert opened(item.drawing_preview) == ("PNG", (800, 600))
    assert item.drawing_preview_error == ""
    assert item.display_drawing.name == item.drawing_preview.name


@pytest.mark.django_db
def test_large_tiff_preview_is_reduced(user):
    item = new_item(user, upload("large.tiff", g4_tiff((4000, 3000)), "image/tiff"))
    fmt, (width, height) = opened(item.drawing_preview)
    assert fmt == "PNG"
    assert max(width, height) == services.PREVIEW_MAX_SIDE == 2000
    assert width * 3000 == height * 4000


@pytest.mark.django_db
def test_multipage_tiff_uses_first_page(user):
    first = line_drawing((300, 200))
    second = line_drawing((500, 400))
    content = image_bytes(first, "TIFF", save_all=True, append_images=[second], compression="group4")
    with Image.open(io.BytesIO(content)) as check:
        assert check.n_frames == 2
    item = new_item(user, upload("pages.tif", content, "image/tiff"))
    assert opened(item.drawing_preview) == ("PNG", (300, 200))


@pytest.mark.django_db
def test_png_and_jpeg_are_kept_as_is(user):
    cases = (("drawing.png", png_bytes(), "image/png"), ("drawing.JPG", jpeg_bytes(), "image/jpeg"))
    for number, (name, content, content_type) in enumerate(cases):
        item = new_item(user, upload(name, content, content_type), part_no=f"P-71{number}")
        item.refresh_from_db()
        assert item.drawing.name.lower().endswith(Path(name).suffix.lower())
        assert Path(item.drawing.path).read_bytes() == content
        assert not item.drawing_preview
        assert item.drawing_preview_error == ""
        assert item.display_drawing.name == item.drawing.name


@pytest.mark.django_db
def test_pdf_is_link_only(user, monkeypatch):
    calls = []
    monkeypatch.setattr(services.Image, "open", lambda *args, **kwargs: calls.append(args))
    content = b"%PDF-1.4\n% made for tests\n%%EOF\n"
    item = new_item(user, upload("drawing.pdf", content, "application/pdf"))
    item.refresh_from_db()
    assert calls == []  # Pillow で読まない
    assert item.drawing.name.endswith(".pdf")
    assert Path(item.drawing.path).read_bytes() == content
    assert not item.drawing_preview
    assert item.drawing_preview_error == ""
    assert item.display_drawing is None


@pytest.mark.django_db
def test_broken_file_saves_item_with_error(user, media, monkeypatch):
    # 帳票の PDF の道具 (WeasyPrint) は import のときに途中で切れた絵も読む設定にする。その後でも壊れた図面と分かる。
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
    limit = Image.MAX_IMAGE_PIXELS
    garbage = b"this is not a drawing at all" * 20
    truncated = noisy_png()[:4000]
    for number, (name, content) in enumerate((("broken.tif", garbage), ("broken.png", truncated))):
        before = Item.objects.count()
        item = new_item(user, upload(name, content), part_no=f"P-72{number}")
        assert Item.objects.count() == before + 1
        item.refresh_from_db()
        path = Path(item.drawing.path)
        assert item.drawing.name.endswith(Path(name).suffix)
        assert path.is_relative_to(media)
        assert path.read_bytes() == content  # 読んだ後に先頭へ戻している (seek(0))
        assert not item.drawing_preview
        assert item.drawing_preview_error == services.DRAWING_BROKEN_MESSAGE
        assert item.drawing_preview_error == (
            "図面の表示用の絵は作れませんでした (ファイルが壊れています)。元のファイルは保存してあります。"
        )
        for word in ("cannot", "identify", "truncated"):
            assert word not in item.drawing_preview_error
        assert item.display_drawing is None
    assert Image.MAX_IMAGE_PIXELS == limit
    assert ImageFile.LOAD_TRUNCATED_IMAGES is True  # 変換の後に元の値へ戻す


@pytest.mark.django_db
def test_too_many_pixels_saves_item_with_error(user, monkeypatch):
    monkeypatch.setattr(services, "DRAWING_MAX_PIXELS", 10_000)
    limit = Image.MAX_IMAGE_PIXELS
    content = g4_tiff((200, 200))
    item = new_item(user, upload("huge.tif", content, "image/tiff"))
    item.refresh_from_db()
    assert Item.objects.filter(pk=item.pk).exists()
    assert Path(item.drawing.path).read_bytes() == content
    assert not item.drawing_preview
    assert "大きすぎます" in item.drawing_preview_error
    assert "200×200" in item.drawing_preview_error
    assert "元のファイルは保存してあります。" in item.drawing_preview_error
    assert item.display_drawing is None
    assert Image.MAX_IMAGE_PIXELS == limit


def state(item):
    item.refresh_from_db()
    return item.drawing.name, item.drawing_preview.name, item.drawing_preview_error


@pytest.mark.django_db
def test_update_replaces_and_clears_preview(user):
    item = new_item(user, upload("first.tif", g4_tiff(), "image/tiff"))
    assert item.drawing_preview

    # TIFF → PNG: 表示用は空
    services.update_item(item, {"drawing": upload("second.png", png_bytes(), "image/png")}, user)
    drawing, preview, error = state(item)
    assert drawing.endswith(".png") and preview == "" and error == ""

    # PNG → TIFF: 表示用ができる
    services.update_item(item, {"drawing": upload("third.tif", g4_tiff((320, 240)), "image/tiff")}, user)
    drawing, preview, error = state(item)
    assert drawing.endswith(".tif") and preview.endswith(".png") and error == ""
    assert opened(item.drawing_preview) == ("PNG", (320, 240))

    # 壊れた TIFF: 誤りの文、表示用は空
    services.update_item(item, {"drawing": upload("broken.tif", b"broken" * 50)}, user)
    drawing, preview, error = state(item)
    assert drawing.endswith(".tif") and preview == "" and error == services.DRAWING_BROKEN_MESSAGE

    # 選び直さない直し (今の FieldFile) では、誤りの文も変わらない
    services.update_item(item, {"drawing": item.drawing, "name": "名前だけ直す"}, user)
    assert state(item) == (drawing, preview, error)

    # 良い TIFF に替え直す: 誤りは空、表示用ができる
    services.update_item(item, {"drawing": upload("fixed.tif", g4_tiff(), "image/tiff")}, user)
    good = state(item)
    assert good[0].endswith(".tif") and good[1].endswith(".png") and good[2] == ""

    # drawing を渡さない直しと、今の FieldFile を渡す直しでは 3 つとも変わらない
    services.update_item(item, {"name": "品名だけ直す"}, user)
    assert state(item) == good
    services.update_item(item, {"drawing": item.drawing}, user)
    assert state(item) == good

    # 「消す」(False): 3 つとも空。名前に False が残らない
    services.update_item(item, {"drawing": False}, user)
    assert state(item) == ("", "", "")
    assert not item.drawing and not item.drawing_preview
    assert item.display_drawing is None


@pytest.mark.django_db
def test_preview_keys_are_refused(user):
    for key in ("drawing_preview", "drawing_preview_error"):
        with pytest.raises(ValueError, match="知らない項目"):
            services.create_item({"part_no": "P-7301", "name": "作り物", key: "x"}, user)
        item = new_item(user, part_no=f"P-73{len(key)}")
        with pytest.raises(ValueError, match="知らない項目"):
            services.update_item(item, {key: "x"}, user)
    assert not Item.objects.filter(part_no="P-7301").exists()
