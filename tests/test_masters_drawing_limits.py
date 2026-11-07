"""部品の図面の受け口の上限 (形式・大きさ・記憶の予算) と、CAD のファイルを添付で持つこと (決め 11-08 00:16)。

図面として受けるのは PDF・TIFF・PNG・JPEG の 20MB まで。CAD のファイル (.dxf・.dwg) は図面としては受けず、
部品の詳細の「添付ファイル」の枠で付ける。断るときは行もファイルも残さない。
表示用の絵を作るときは、絵を読む前に記憶の使う量を見積もり、予算を超える絵は読まずに大きすぎるの文にする
(部品と元のファイルは保存する)。
試験の絵は Pillow でこの中で作る小さな作り物。予算と大きさの定数を monkeypatch で小さくして見る
(17000 画素四方の実物を作ると、試験が約 1.2GB の記憶を取るため)。置き場 (MEDIA_ROOT) は試験ごとの一時のフォルダ。
"""

import io
import random
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageDraw, ImageFile

from seizou.masters import services
from seizou.masters.models import Item, ItemDrawingVersion

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "companies" / "a_machining" / "files"
SAMPLE_NAMES = ("WK-1001.png", "WK-1003.tif", "WK-1004.png", "WK-1010.png")
PDF_BYTES = b"%PDF-1.4\n"


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


def new_item(user, drawing=None, part_no="P-7101"):
    data = {"part_no": part_no, "name": "作り物の部品"}
    if drawing is not None:
        data["drawing"] = drawing
    return services.create_item(data, user)


def png_bytes(size=(60, 40)):
    return image_bytes(Image.new("RGB", size, (200, 30, 30)), "PNG")


def jpeg_bytes(size=(60, 40)):
    return image_bytes(Image.new("RGB", size, (30, 30, 200)), "JPEG")


def noisy_png(size=(200, 200)):
    data = random.Random(0).randbytes(size[0] * size[1] * 3)
    return image_bytes(Image.frombytes("RGB", size, data), "PNG")


def stored_files(root):
    return sorted(path for path in root.rglob("*") if path.is_file())


def refused(user, name, content, part_no="P-7101"):
    """図面を付けた部品の作成が断られることを見て、断りの文を返す (行もファイルも残らない)。"""
    with pytest.raises(ValueError) as caught:
        new_item(user, upload(name, content), part_no=part_no)
    return str(caught.value)


# ---------------------------------------------------------------- 1: 受け口の検査 (形式・CAD・20MB)


@pytest.mark.django_db
def test_other_formats_are_refused_without_row_or_file(user, media):
    for name in ("drawing.html", "notes.xlsx"):
        before = Item.objects.count()
        message = refused(user, name, b"<html>not a drawing</html>")
        assert message == services.DRAWING_BAD_SUFFIX_MESSAGE
        assert message.startswith("保存できません。")
        assert Item.objects.count() == before
        assert stored_files(media) == []


@pytest.mark.django_db
def test_cad_files_are_refused_and_pointed_to_attachments(user, media):
    for name in ("shape.dxf", "PLAN.DWG"):
        message = refused(user, name, b"0\nSECTION\n0\nEOF\n")
        assert message == services.DRAWING_CAD_MESSAGE
        assert "添付ファイル" in message
        assert Item.objects.count() == 0
        assert stored_files(media) == []


@pytest.mark.django_db
def test_file_over_max_bytes_is_refused_before_reading(user, media, monkeypatch):
    assert services.DRAWING_MAX_BYTES == 20 * 1024 * 1024
    monkeypatch.setattr(services, "DRAWING_MAX_BYTES", 1_000)

    # ちょうど上限の大きさは受ける (中身は作り物なので、壊れているの文で保存される)。
    item = new_item(user, upload("edge.png", b"x" * 1_000), part_no="P-7102")
    assert item.drawing_preview_error == services.DRAWING_BROKEN_MESSAGE

    opened = []

    def must_not_open(*args, **kwargs):
        opened.append(args)
        raise AssertionError("図面の中身を読んではいけない")

    monkeypatch.setattr(services.Image, "open", must_not_open)
    before = Item.objects.count()
    files = stored_files(media)
    message = refused(user, "big.png", b"x" * 1_001)
    assert message == services.DRAWING_TOO_BIG_FILE_MESSAGE
    assert "20MB" in message
    assert opened == []
    assert Item.objects.count() == before
    assert stored_files(media) == files


@pytest.mark.django_db
def test_update_with_cad_file_keeps_current_drawing(user, media):
    item = new_item(user, upload("first.tif", g4_tiff(), "image/tiff"))
    services.update_item(item, {"drawing": upload("second.tif", g4_tiff((400, 300)), "image/tiff")}, user)
    item.refresh_from_db()
    before = (item.drawing.name, item.drawing_preview.name, item.drawing_preview_error, item.drawing_page_count)
    versions = ItemDrawingVersion.objects.filter(item=item).count()
    assert versions == 1
    files = stored_files(media)

    with pytest.raises(ValueError) as caught:
        services.update_item(item, {"drawing": upload("shape.dxf", b"0\nSECTION\n0\nEOF\n")}, user)
    assert str(caught.value) == services.DRAWING_CAD_MESSAGE

    item.refresh_from_db()
    after = (item.drawing.name, item.drawing_preview.name, item.drawing_preview_error, item.drawing_page_count)
    assert after == before
    assert ItemDrawingVersion.objects.filter(item=item).count() == versions
    assert stored_files(media) == files


@pytest.mark.django_db
def test_sample_drawings_and_pdf_are_accepted(user):
    for number, name in enumerate(SAMPLE_NAMES):
        content = (SAMPLE_DIR / name).read_bytes()
        item = new_item(user, upload(name, content), part_no=f"P-711{number}")
        item.refresh_from_db()
        assert item.drawing.name.endswith(Path(name).suffix), name
        assert item.drawing_preview_error == "", name
    for number, name in enumerate(("drawing.pdf", "PLAN.PDF")):
        item = new_item(user, upload(name, PDF_BYTES, "application/pdf"), part_no=f"P-712{number}")
        item.refresh_from_db()
        assert item.drawing.name.endswith(Path(name).suffix)
        assert item.drawing_preview_error == ""


# ---------------------------------------------------------------- 2: 記憶の予算と、形式の判定を読む前に置くこと


def too_large_saved(item, size):
    """部品と元のファイルは保存され、表示用の絵が無く、大きすぎるの文 (画素の数を入れた物) が残っている。"""
    item.refresh_from_db()
    assert Item.objects.filter(pk=item.pk).exists()
    assert item.drawing
    assert not item.drawing_preview
    assert "大きすぎます" in item.drawing_preview_error
    assert f"{size[0]}×{size[1]}" in item.drawing_preview_error
    assert "元のファイルは保存してあります。" in item.drawing_preview_error
    assert item.display_drawing is None


def test_memory_budget_passes_mono_a0_and_refuses_color_a0():
    """A0 を 400dpi で読んだ大きさ (13244×18725) で、白黒は予算の内、カラーは予算の外 (実物の絵は作らない)。"""
    assert services.DRAWING_MEMORY_BUDGET == 600 * 1024 * 1024

    class Header:
        """Image.open の後に分かる頭 (大きさと形) だけを持つ作り物。"""

        def __init__(self, mode):
            self.size = (13244, 18725)
            self.mode = mode

    assert services._drawing_memory(Header("1")) == 13244 * 18725 * (1 + 1)
    assert services._drawing_memory(Header("1")) <= services.DRAWING_MEMORY_BUDGET
    for mode in ("RGB", "CMYK"):
        assert services._drawing_memory(Header(mode)) > services.DRAWING_MEMORY_BUDGET, mode


@pytest.mark.django_db
def test_image_over_memory_budget_is_not_loaded(user, monkeypatch):
    monkeypatch.setattr(services, "DRAWING_MEMORY_BUDGET", 50_000)
    loaded = []

    def must_not_load(self, *args, **kwargs):
        loaded.append(self.size)
        raise AssertionError("予算を超える絵を読んではいけない")

    content = image_bytes(Image.new("CMYK", (100, 100), (10, 20, 30, 0)), "TIFF")  # 100 × 100 × (4 + 4) = 80,000
    item = new_item(user, upload("color.tif", content, "image/tiff"), part_no="P-7201")
    too_large_saved(item, (100, 100))

    monkeypatch.setattr(Image.Image, "load", must_not_load)
    item = new_item(user, upload("color.tif", content, "image/tiff"), part_no="P-7202")
    too_large_saved(item, (100, 100))
    assert loaded == []


@pytest.mark.django_db
def test_images_within_memory_budget_are_read(user, monkeypatch):
    monkeypatch.setattr(services, "DRAWING_MEMORY_BUDGET", 50_000)
    rgba = image_bytes(Image.new("RGBA", (100, 100), (200, 30, 30, 255)), "PNG")  # 100 × 100 × 4 = 40,000
    item = new_item(user, upload("photo.png", rgba, "image/png"), part_no="P-7211")
    item.refresh_from_db()
    assert item.drawing_preview_error == ""
    assert item.display_drawing.name == item.drawing.name

    mono = g4_tiff((100, 100))  # 100 × 100 × (1 + 1) = 20,000
    item = new_item(user, upload("scan.tif", mono, "image/tiff"), part_no="P-7212")
    item.refresh_from_db()
    assert item.drawing_preview_error == ""
    assert item.drawing_preview.name.endswith(".png")


@pytest.mark.django_db
def test_memory_budget_applies_to_png(user, monkeypatch):
    monkeypatch.setattr(services, "DRAWING_MEMORY_BUDGET", 30_000)
    rgba = image_bytes(Image.new("RGBA", (100, 100), (200, 30, 30, 255)), "PNG")
    item = new_item(user, upload("photo.png", rgba, "image/png"), part_no="P-7221")
    too_large_saved(item, (100, 100))


@pytest.mark.django_db
def test_truncated_jpeg_and_png_are_broken(user, monkeypatch):
    # 帳票の PDF の道具 (WeasyPrint) は import のときに途中で切れた絵も読む設定にする。その後でも壊れた図面と分かる。
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
    whole = jpeg_bytes((400, 300))
    good = new_item(user, upload("whole.jpg", whole, "image/jpeg"), part_no="P-7230")
    assert good.drawing_preview_error == ""
    for number, (name, content) in enumerate((("half.jpg", whole[: len(whole) // 2]), ("half.png", noisy_png()[:4000]))):
        item = new_item(user, upload(name, content), part_no=f"P-723{number + 1}")
        item.refresh_from_db()
        assert item.drawing.name.endswith(Path(name).suffix), name
        assert not item.drawing_preview, name
        assert item.drawing_preview_error == services.DRAWING_BROKEN_MESSAGE, name
    assert ImageFile.LOAD_TRUNCATED_IMAGES is True  # 変換の後に元の値へ戻す


@pytest.mark.django_db
def test_later_page_over_memory_budget_is_not_made(user, monkeypatch):
    first = line_drawing((100, 100))  # 100 × 100 × (1 + 1) = 20,000
    second = Image.new("RGB", (200, 200), (30, 30, 200))  # 200 × 200 × 4 = 160,000
    content = image_bytes(first, "TIFF", save_all=True, append_images=[second])

    item = new_item(user, upload("two.tif", content, "image/tiff"), part_no="P-7241")
    item.refresh_from_db()
    assert item.drawing_page_count == 2  # 予算の既定では 2 ページとも作る

    monkeypatch.setattr(services, "DRAWING_MEMORY_BUDGET", 50_000)
    item = new_item(user, upload("two.tif", content, "image/tiff"), part_no="P-7242")
    item.refresh_from_db()
    assert item.drawing_preview_error == ""
    assert item.drawing_preview.name.endswith(".png")
    assert item.drawing_page_count == 1
