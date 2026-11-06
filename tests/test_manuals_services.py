"""業務マニュアルの services (記録は services で作る。画像は試験の中で Pillow で作る)。"""

import io

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from seizou.manuals import services
from seizou.manuals.models import Manual, ManualFolder, ManualImage, ManualVersion


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def png_bytes(size=(4, 3), color=(200, 30, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def png_upload(name="写真.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, png_bytes(), content_type="image/png")


def stored_files(root) -> list:
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def test_create_folder_and_path(user):
    top = services.create_folder(None, "  段取り  ", "blue", user)
    child = services.create_folder(top, "旋盤", "red", user)
    assert top.name == "段取り"
    assert services.folder_path(child) == [top, child]
    assert services.folder_path(None) == []
    assert child.path_text() == "段取り / 旋盤"
    for name, color in (("  ", "gray"), ("あ" * 101, "gray"), ("名前", "purple")):
        with pytest.raises(ValueError) as exc:
            services.create_folder(None, name, color, user)
        assert any("぀" <= c <= "ヿ" for c in str(exc.value))
    assert ManualFolder.objects.count() == 2


def test_move_folder_refuses_self_descendant_and_protected(user):
    top = services.create_folder(None, "上", "gray", user)
    child = services.create_folder(top, "子", "gray", user)
    grandchild = services.create_folder(child, "孫", "gray", user)
    other = services.create_folder(None, "ほか", "gray", user)
    assert services.descendant_ids(top) == {child.pk, grandchild.pk}
    for target in (top, child, grandchild):
        with pytest.raises(ValueError, match="移動先がこのフォルダの中です"):
            services.move_folder(top, target, user)
    moved = services.move_folder(child, other, user)
    assert moved.parent_id == other.pk
    assert services.move_folder(child, None, user).parent_id is None
    services.protect_folder(child, True, user)
    with pytest.raises(ValueError, match="保護されたフォルダです"):
        services.move_folder(child, other, user)
    child.refresh_from_db()
    assert child.parent_id is None


def test_delete_folder_rules(user, media_root):
    top = services.create_folder(None, "上", "gray", user)
    child = services.create_folder(top, "子", "gray", user)
    grandchild = services.create_folder(child, "孫", "gray", user)
    manual = services.create_manual(grandchild, "深い所の手順", "steps", user)
    services.save_as_version(manual, {"body": "新しい本文"}, "メモ", user)
    services.add_image(manual, png_upload(), user)
    assert len(stored_files(media_root)) == 1
    with pytest.raises(ValueError, match="フォルダが空ではありません"):
        services.delete_folder(top, user)
    services.protect_manual(manual, True, user)
    with pytest.raises(ValueError, match="保護された"):
        services.delete_folder(top, user, with_contents=True)
    assert ManualFolder.objects.count() == 3 and Manual.objects.count() == 1
    services.protect_manual(manual, False, user)
    assert services.delete_folder(top, user, with_contents=True) is None
    assert ManualFolder.objects.count() == 0
    assert Manual.objects.count() == 0
    assert ManualVersion.objects.count() == 0
    assert ManualImage.objects.count() == 0
    assert stored_files(media_root) == []
    # 空のフォルダは with_contents なしで消え、親を返す。保護されたフォルダは消えない。
    parent = services.create_folder(None, "親", "gray", user)
    empty = services.create_folder(parent, "空", "gray", user)
    assert services.delete_folder(empty, user) == parent
    services.protect_folder(parent, True, user)
    with pytest.raises(ValueError, match="保護されたフォルダです"):
        services.delete_folder(parent, user)


def test_create_and_update_manual(user):
    folder = services.create_folder(None, "置き場", "gray", user)
    manual = services.create_manual(None, "  手順書  ", "steps", user)
    assert (manual.title, manual.version, manual.body, manual.created_by) == ("手順書", 1, "", user)
    updated = services.update_manual(
        manual, {"title": "新しい題", "format": "page", "body": "  ## 字下げを残す\n", "folder": folder}, user
    )
    assert (updated.title, updated.format, updated.body, updated.folder) == ("新しい題", "page", "  ## 字下げを残す\n", folder)
    assert updated.folder_path_text() == "置き場"
    assert services.update_manual(manual, {"folder": None}, user).folder_path_text() == "トップ"
    for data, message in (
        ({"body": "あ" * 100_001}, "100,000 字を超えています"),
        ({"color": "red"}, "知らない項目です: color"),
        ({"format": "grid"}, "形式は"),
        ({"title": " "}, "題を入れてください"),
    ):
        with pytest.raises(ValueError, match=message):
            services.update_manual(manual, data, user)
    manual.refresh_from_db()
    assert manual.title == "新しい題"
    with pytest.raises(ValueError):
        services.create_manual(None, "題", "grid", user)


def test_protected_manual_allows_body_but_not_move_or_delete(user):
    folder = services.create_folder(None, "置き場", "gray", user)
    manual = services.create_manual(None, "保護する手順", "steps", user)
    services.protect_manual(manual, True, user)
    assert services.update_manual(manual, {"body": "本文は変えてよい", "title": "題も変えてよい"}, user).body == "本文は変えてよい"
    with pytest.raises(ValueError, match="保護されたマニュアルです"):
        services.update_manual(manual, {"folder": folder}, user)
    with pytest.raises(ValueError, match="保護されたマニュアルです"):
        services.move_manual(manual, folder, user)
    with pytest.raises(ValueError, match="保護されたマニュアルです"):
        services.delete_manual(manual, user)
    services.protect_manual(manual, False, user)
    assert services.move_manual(manual, folder, user).folder == folder
    assert services.delete_manual(manual, user) == folder
    assert Manual.objects.count() == 0


def test_save_as_version_keeps_previous_content(user):
    manual = services.create_manual(None, "題 A", "steps", user)
    services.update_manual(manual, {"body": "本文 A"}, user)
    # 呼び手が対象の物を保存せずに書き換えていても、版に残るのは表の中身 (行を取り直す)。
    manual.body = "書き換えた字"
    manual.title = "書き換えた題"
    saved = services.save_as_version(manual, {"body": "本文 B"}, "手順を足した", user)
    assert (saved.version, saved.body, saved.title) == (2, "本文 B", "題 A")
    version = ManualVersion.objects.get()
    assert (version.number, version.title, version.body, version.note, version.saved_by) == (1, "題 A", "本文 A", "手順を足した", user)
    with pytest.raises(ValueError, match="200 字まで"):
        services.save_as_version(saved, {"body": "本文 C"}, "あ" * 201, user)
    with pytest.raises(ValueError, match="知らない項目です"):
        services.save_as_version(saved, {"unknown": 1}, "", user)
    assert ManualVersion.objects.count() == 1


def test_restore_version_keeps_current_as_version(user):
    manual = services.create_manual(None, "題", "steps", user)
    services.update_manual(manual, {"body": "本文 A"}, user)
    services.save_as_version(manual, {"body": "本文 B"}, "", user)
    first = ManualVersion.objects.get(number=1)
    restored = services.restore_version(first, user)
    assert (restored.body, restored.version) == ("本文 A", 3)
    kept = ManualVersion.objects.get(number=2)
    assert (kept.body, kept.note) == ("本文 B", "版 1 に戻す前の中身")
    assert list(manual.versions.values_list("number", flat=True)) == [2, 1]


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("anim.gif", b"GIF89a", "画像の形式が違います"),
        ("drawing.svg", b"<svg></svg>", "画像の形式が違います"),
        ("noext", png_bytes(), "画像の形式が違います"),
        ("empty.png", b"", "空です"),
        ("big.png", png_bytes(), "10MB を超えています"),
        ("あ" * 97 + ".png", png_bytes(), "100 字を超えています"),
        ("page.png", b"<html><script>alert(1)</script></html>", "中身が画像の形式と合いません"),
        ("photo.jpg", png_bytes(), "中身が画像の形式と合いません"),
    ],
)
def test_add_image_rules(user, media_root, monkeypatch, name, content, message):
    manual = services.create_manual(None, "画像の手順", "steps", user)
    if name == "big.png":
        monkeypatch.setattr(services, "MAX_IMAGE_BYTES", 10)
    with pytest.raises(ValueError, match=message):
        services.add_image(manual, SimpleUploadedFile(name, content), user)
    assert ManualImage.objects.count() == 0
    assert stored_files(media_root) == []


def test_add_image_accepts_png(user, media_root):
    manual = services.create_manual(None, "画像の手順", "steps", user)
    image = services.add_image(manual, png_upload("治具の写真.PNG"), user)
    assert image.original_name == "治具の写真.PNG"
    assert image.size == len(png_bytes())
    assert image.file.name.startswith("manuals/")
    assert image.file.name.endswith(".png")
    assert "治具" not in image.file.name
    assert len(stored_files(media_root)) == 1
    with pytest.raises(ValueError, match="画像を選んでください"):
        services.add_image(manual, None, user)


def test_remove_image_deletes_file(user, media_root):
    manual = services.create_manual(None, "画像の手順", "steps", user)
    image = services.add_image(manual, png_upload(), user)
    assert services.remove_image(image, user) == manual
    assert ManualImage.objects.count() == 0
    assert stored_files(media_root) == []


def test_render_body_maps_images(user):
    manual = services.create_manual(None, "画像の手順", "steps", user)
    other = services.create_manual(None, "ほかの手順", "steps", user)
    image = services.add_image(manual, png_upload(), user)
    foreign = services.add_image(other, png_upload(), user)
    services.update_manual(manual, {"body": f"![a](image:{image.pk})\n\n![b](image:{foreign.pk})"}, user)
    manual.refresh_from_db()
    screen = services.render_body(manual).html
    assert f'src="/manuals/images/{image.pk}/"' in screen
    assert "[画像: b]" in screen
    pdf = services.render_body(manual, pdf=True).html
    assert f'src="/media/{image.file.name}"' in pdf
    preview = services.render_body(manual, body="## 手順", manual_format="page")
    assert preview.toc[0].number == "i"


def test_search_manuals_all_terms(user):
    services.create_manual(None, "旋盤の段取り", "steps", user)
    second = services.create_manual(None, "梱包の手順", "steps", user)
    services.update_manual(second, {"body": "旋盤で削った物を箱に入れる"}, user)
    services.create_manual(None, "品質の基準", "page", user)
    assert [m.title for m in services.search_manuals("旋盤")] == ["旋盤の段取り", "梱包の手順"]
    assert [m.title for m in services.search_manuals("旋盤 箱")] == ["梱包の手順"]
    assert list(services.search_manuals("無い言葉")) == []
