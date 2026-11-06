"""業務マニュアルの書く処理と読む処理 (02 の 6 節。表を書くのはここだけ = R13)。

引数の順は 対象 → 値 → user (01 の 10.1)。書く関数は全部 transaction.atomic の中で、保存した物を返す。
断るときは日本語の ValueError (何が起きて何をすればよいか) で、行もファイルも残さない。
書く関数は、渡された物の属性でなく、select_for_update で取り直した行を「保存前の中身」と保護の判断に使う
(form や呼び手が対象の物を書き換えても、版に残る中身と保護の判断がずれないため)。
storage のファイルは atomic を抜けた後に消す (行が戻ったときにファイルだけ消えないため)。
保護は移動と削除だけを止める (題・形式・本文・紐付け・フォルダの名前と色は変えてよい)。
"""

from __future__ import annotations

import os

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.urls import reverse

from seizou.core import reports
from seizou.manuals import markdown as manual_markdown
from seizou.parts import PARTS

from .models import (
    COLOR_CHOICES,
    FORMAT_CHOICES,
    MAX_DEPTH,
    Manual,
    ManualFolder,
    ManualImage,
    ManualVersion,
)

MAX_BODY_CHARS = 100_000
MAX_NOTE_CHARS = 200
MAX_IMAGE_BYTES = 10 * 1024 * 1024
#: 拡張子 → (返す型, Pillow の形)。画像の口もこれを読む
IMAGE_TYPES = {
    ".png": ("image/png", "PNG"),
    ".jpg": ("image/jpeg", "JPEG"),
    ".jpeg": ("image/jpeg", "JPEG"),
    ".webp": ("image/webp", "WEBP"),
}
#: update_manual の data の鍵
MANUAL_KEYS = frozenset({"title", "format", "folder", "body", "linked"})

MAX_FOLDER_NAME = 100
MAX_TITLE = 200
MAX_ORIGINAL_NAME = 100

COLOR_VALUES = frozenset(value for value, _label in COLOR_CHOICES)
FORMAT_VALUES = frozenset(value for value, _label in FORMAT_CHOICES)

FOLDER_NOT_FOUND = "フォルダが見つかりません。一覧から開き直してください。"
MANUAL_NOT_FOUND = "マニュアルが見つかりません。一覧から開き直してください。"
FOLDER_PROTECTED_MOVE = "保護されたフォルダです。先に保護を解除してください。"
FOLDER_PROTECTED_DELETE = "保護されたフォルダです。先に保護を解除してください。"
FOLDER_PROTECTED_INSIDE = "中に保護されたフォルダかマニュアルがあります。先に保護を解除してください。"
MANUAL_PROTECTED_MOVE = "保護されたマニュアルです。先に保護を解除してください。"
MANUAL_PROTECTED_DELETE = "保護されたマニュアルです。先に保護を解除してください。"
BODY_TOO_LONG = "本文が 100,000 字を超えています。分けて別のマニュアルにしてください。"


# --- 中で使う物 ---


def _user_or_none(user):
    """記録の列に入れる利用者 (ログインしていない物と None は None)。"""
    return user if getattr(user, "pk", None) is not None else None


def _clean_text(value, *, limit: int, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"保存できません。{label}を入れてください。")
    if len(text) > limit:
        raise ValueError(f"保存できません。{label}は {limit} 字までです。短くしてください。")
    return text


def _clean_color(color) -> str:
    if color not in COLOR_VALUES:
        raise ValueError("保存できません。色は一覧の 7 色から選んでください。")
    return color


def _clean_format(value) -> str:
    if value not in FORMAT_VALUES:
        raise ValueError("保存できません。形式はステップ形式かページ形式から選んでください。")
    return value


def _saved_folder(folder) -> ManualFolder | None:
    """None (ルート) か、表にあるフォルダ (取り直した物)。ほかは断る。"""
    if folder is None:
        return None
    if not isinstance(folder, ManualFolder) or folder.pk is None:
        raise ValueError("保存できません。" + FOLDER_NOT_FOUND)
    found = ManualFolder.objects.filter(pk=folder.pk).first()
    if found is None:
        raise ValueError("保存できません。" + FOLDER_NOT_FOUND)
    return found


def _lock_folder(folder) -> ManualFolder:
    pk = getattr(folder, "pk", folder)
    found = ManualFolder.objects.select_for_update().filter(pk=pk).first() if pk is not None else None
    if found is None:
        raise ValueError("保存できません。フォルダが見つかりません。一覧から開き直してください。")
    return found


def _lock_manual(manual) -> Manual:
    pk = getattr(manual, "pk", manual)
    found = Manual.objects.select_for_update().filter(pk=pk).first() if pk is not None else None
    if found is None:
        raise ValueError("保存できません。" + MANUAL_NOT_FOUND)
    return found


def _descendant_levels(folder) -> list[list[int]]:
    """子孫のフォルダの pk を段ごとに (浅い段から)。壊れた親の輪と、MAX_DEPTH 段より深い所で止まる。"""
    if folder is None or getattr(folder, "pk", None) is None:
        return []
    seen = {folder.pk}
    levels: list[list[int]] = []
    current = [folder.pk]
    while current and len(levels) < MAX_DEPTH:
        children = [pk for pk in ManualFolder.objects.filter(parent_id__in=current).values_list("pk", flat=True)]
        children = [pk for pk in children if pk not in seen]
        if not children:
            break
        seen.update(children)
        levels.append(children)
        current = children
    return levels


def _image_storage():
    return ManualImage._meta.get_field("file").storage


def _delete_files(names) -> None:
    """storage のファイルを消す (atomic の外で呼ぶ)。無いファイルは飛ばす。"""
    storage = _image_storage()
    for name in names:
        if name and storage.exists(name):
            storage.delete(name)


def _linkable_apps() -> frozenset:
    return frozenset(part.rsplit(".", 1)[-1] for part in PARTS) - {"manuals"}


def _set_linked(manual: Manual, obj) -> None:
    """紐付けを当てる。None は外す。保存済みの、部品 (PARTS) の記録だけ (manuals の記録は紐付けない)。"""
    if obj is None:
        manual.linked_type = None
        manual.linked_id = None
        return
    meta = getattr(obj, "_meta", None)
    pk = getattr(obj, "pk", None)
    if (
        meta is None
        or not isinstance(pk, int)
        or isinstance(pk, bool)
        or pk < 0
        or meta.app_label not in _linkable_apps()
        or not apps.is_installed(meta.app_config.name)
        or not type(obj)._default_manager.filter(pk=pk).exists()
    ):
        raise ValueError("保存できません。紐付けられない記録です。部品マスターや取引先などの記録を選び直してください。")
    manual.linked_type = ContentType.objects.get_for_model(obj)
    manual.linked_id = pk


def _apply(manual: Manual, data: dict) -> None:
    """update_manual と save_as_version が使う、data の検査と当てはめ (表には書かない)。"""
    unknown = sorted(set(data) - MANUAL_KEYS)
    if unknown:
        raise ValueError(f"保存できません。知らない項目です: {'・'.join(unknown)}")
    if "title" in data:
        manual.title = _clean_text(data["title"], limit=MAX_TITLE, label="題")
    if "format" in data:
        manual.format = _clean_format(data["format"])
    if "body" in data:
        body = data["body"] or ""
        if len(body) > MAX_BODY_CHARS:
            raise ValueError("保存できません。" + BODY_TOO_LONG)
        manual.body = body
    if "folder" in data:
        target = _saved_folder(data["folder"])
        target_id = target.pk if target is not None else None
        if target_id != manual.folder_id:
            if manual.protected:
                raise ValueError("保存できません。" + MANUAL_PROTECTED_MOVE)
            manual.folder = target
    if "linked" in data:
        _set_linked(manual, data["linked"])


# --- フォルダ ---


def create_folder(parent, name, color, user):
    """フォルダを作る。parent は None (ルートの直下) か保存済みのフォルダ。"""
    clean_name = _clean_text(name, limit=MAX_FOLDER_NAME, label="フォルダの名前")
    clean_color = _clean_color(color)
    with transaction.atomic():
        target = _saved_folder(parent)
        return ManualFolder.objects.create(parent=target, name=clean_name, color=clean_color)


def update_folder(folder, name, color, user):
    """フォルダの名前と色を変える (保護されたフォルダも変えてよい)。"""
    clean_name = _clean_text(name, limit=MAX_FOLDER_NAME, label="フォルダの名前")
    clean_color = _clean_color(color)
    with transaction.atomic():
        current = _lock_folder(folder)
        current.name = clean_name
        current.color = clean_color
        current.save(update_fields=["name", "color"])
        return current


def move_folder(folder, parent, user):
    """フォルダを parent の下へ移す (None はルートの直下)。保護・自分・自分の子孫への移動は断る。"""
    with transaction.atomic():
        current = _lock_folder(folder)
        if current.protected:
            raise ValueError("保存できません。" + FOLDER_PROTECTED_MOVE)
        target = _saved_folder(parent)
        if target is not None and (target.pk == current.pk or target.pk in descendant_ids(current)):
            raise ValueError("保存できません。移動先がこのフォルダの中です。ほかのフォルダを選んでください。")
        current.parent = target
        current.save(update_fields=["parent"])
        return current


def protect_folder(folder, on, user):
    with transaction.atomic():
        current = _lock_folder(folder)
        current.protected = bool(on)
        current.save(update_fields=["protected"])
        return current


def delete_folder(folder, user, *, with_contents=False):
    """フォルダを消し、親のフォルダ (ルートの直下なら None) を返す。

    中にフォルダかマニュアルがあれば、with_contents が真のときだけ中身ごと消す。
    子孫に保護された物があれば何も消さない。消す順は 子孫のマニュアル → 深いフォルダから自分まで。
    """
    with transaction.atomic():
        current = _lock_folder(folder)
        if current.protected:
            raise ValueError("削除できません。" + FOLDER_PROTECTED_DELETE)
        levels = _descendant_levels(current)
        below = [pk for level in levels for pk in level]
        folder_ids = [current.pk, *below]
        manuals = Manual.objects.filter(folder_id__in=folder_ids)
        if (below or manuals.exists()) and not with_contents:
            raise ValueError(
                "削除できません。フォルダが空ではありません。中を移すか削除するか、「中身も削除する」を選んでください。"
            )
        if ManualFolder.objects.filter(pk__in=below, protected=True).exists() or manuals.filter(protected=True).exists():
            raise ValueError("削除できません。" + FOLDER_PROTECTED_INSIDE)
        names = list(ManualImage.objects.filter(manual__folder_id__in=folder_ids).values_list("file", flat=True))
        parent_id = current.parent_id
        manuals.delete()
        for level in reversed(levels):
            ManualFolder.objects.filter(pk__in=level).delete()
        current.delete()
        parent = ManualFolder.objects.filter(pk=parent_id).first() if parent_id is not None else None
    _delete_files(names)
    return parent


# --- マニュアル ---


def create_manual(folder, title, format, user):
    """マニュアルを作る。本文は空・版は 1。folder は None (ルート) か保存済みのフォルダ。"""
    clean_title = _clean_text(title, limit=MAX_TITLE, label="題")
    clean_format = _clean_format(format)
    with transaction.atomic():
        target = _saved_folder(folder)
        return Manual.objects.create(
            folder=target,
            title=clean_title,
            format=clean_format,
            body="",
            version=1,
            created_by=_user_or_none(user),
        )


def update_manual(manual, data, user):
    """data の鍵 (MANUAL_KEYS の中) だけを変える。folder が今と違えば移動と同じ扱い (保護なら断る)。"""
    with transaction.atomic():
        current = _lock_manual(manual)
        _apply(current, dict(data))
        current.save()
        return current


def save_as_version(manual, data, note, user):
    """保存前の中身 (取り直した行の題と本文) を版に残してから data を当て、版の番号を 1 増やす。"""
    clean_note = (note or "").strip()
    if len(clean_note) > MAX_NOTE_CHARS:
        raise ValueError(f"保存できません。版のメモは {MAX_NOTE_CHARS} 字までです。短くしてください。")
    with transaction.atomic():
        current = _lock_manual(manual)
        ManualVersion.objects.create(
            manual=current,
            number=current.version,
            title=current.title,
            body=current.body,
            note=clean_note,
            saved_by=_user_or_none(user),
        )
        _apply(current, dict(data))
        current.version += 1
        current.save()
        return current


def restore_version(version, user):
    """版の中身 (題と本文) に戻す。今の中身を版として残してから戻し、版の番号を 1 増やす (履歴は消さない)。"""
    with transaction.atomic():
        target = ManualVersion.objects.filter(pk=getattr(version, "pk", version)).first()
        if target is None:
            raise ValueError("保存できません。版が見つかりません。版の履歴から開き直してください。")
        current = _lock_manual(target.manual_id)
        ManualVersion.objects.create(
            manual=current,
            number=current.version,
            title=current.title,
            body=current.body,
            note=f"版 {target.number} に戻す前の中身",
            saved_by=_user_or_none(user),
        )
        current.title = target.title
        current.body = target.body
        current.version += 1
        current.save()
        return current


def move_manual(manual, folder, user):
    """マニュアルを folder へ移す (None はルート)。保護は断る。"""
    with transaction.atomic():
        current = _lock_manual(manual)
        if current.protected:
            raise ValueError("保存できません。" + MANUAL_PROTECTED_MOVE)
        current.folder = _saved_folder(folder)
        current.save(update_fields=["folder", "updated_at"])
        return current


def protect_manual(manual, on, user):
    with transaction.atomic():
        current = _lock_manual(manual)
        current.protected = bool(on)
        current.save(update_fields=["protected", "updated_at"])
        return current


def delete_manual(manual, user):
    """マニュアルを消し (版と画像の行は CASCADE)、置き場のフォルダ (ルートなら None) を返す。保護は断る。"""
    with transaction.atomic():
        current = _lock_manual(manual)
        if current.protected:
            raise ValueError("削除できません。" + MANUAL_PROTECTED_DELETE)
        names = list(current.images.values_list("file", flat=True))
        folder_id = current.folder_id
        current.delete()
        folder = ManualFolder.objects.filter(pk=folder_id).first() if folder_id is not None else None
    _delete_files(names)
    return folder


# --- 画像 ---


def _check_image_content(file, expected: str) -> None:
    """中身を Pillow で開き、形が拡張子の表と合うかを見る (HTML を .png の名前で上げられないため)。"""
    from PIL import Image, UnidentifiedImageError

    kind = None
    try:
        file.seek(0)
        with Image.open(file) as picture:
            kind = picture.format
            picture.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        kind = None
    finally:
        file.seek(0)
    if kind != expected:
        raise ValueError("保存できません。ファイルの中身が画像の形式と合いません。PNG・JPEG・WebP の画像を選び直してください。")


def add_image(manual, file, user):
    """マニュアルに画像を 1 つ足す。形式・大きさ・名前・中身を先に見る。"""
    if file is None:
        raise ValueError("保存できません。画像を選んでください。")
    original = os.path.basename(getattr(file, "name", "") or "")
    ext = os.path.splitext(original)[1].lower()
    if ext not in IMAGE_TYPES:
        raise ValueError("保存できません。画像の形式が違います。PNG・JPEG・WebP を選んでください。")
    size = getattr(file, "size", 0) or 0
    if size <= 0:
        raise ValueError("保存できません。画像のファイルが空です。中身のあるファイルを選び直してください。")
    if size > MAX_IMAGE_BYTES:
        raise ValueError("保存できません。画像が 10MB を超えています。小さくしてから選び直してください。")
    if len(original) > MAX_ORIGINAL_NAME:
        raise ValueError("保存できません。ファイルの名前が 100 字を超えています。名前を短くしてから選び直してください。")
    _check_image_content(file, IMAGE_TYPES[ext][1])
    with transaction.atomic():
        current = _lock_manual(manual)
        return ManualImage.objects.create(manual=current, file=file, original_name=original, size=size)


def remove_image(image, user):
    """画像の行を消し、ファイルは atomic の後に消す。本文の image:<番号> は残る (描くと「[画像: 説明]」)。"""
    with transaction.atomic():
        current = (
            ManualImage.objects.select_for_update().select_related("manual").filter(pk=getattr(image, "pk", image)).first()
        )
        if current is None:
            raise ValueError("削除できません。画像が見つかりません。画面を開き直してください。")
        name = current.file.name
        manual = current.manual
        current.delete()
    _delete_files([name])
    return manual


# --- 読むだけ ---


def folder_path(folder) -> list[ManualFolder]:
    """ルートの直下から folder までの並び (None なら空の並び)。"""
    return folder.path() if folder is not None else []


def descendant_ids(folder) -> set[int]:
    """子孫のフォルダの pk の集合 (自分を含まない。段ごとに引く)。"""
    return {pk for level in _descendant_levels(folder) for pk in level}


def folder_contents(folder) -> dict:
    """フォルダの中の数 {"folders": 子孫のフォルダの数, "manuals": 自分と子孫のフォルダにあるマニュアルの数, "protected": 保護された物があるか}。

    delete_folder が断る条件と同じ数え方 (どの深さの物も数える)。フォルダの削除の確かめ (forms.FolderDeleteForm) が読む。
    protected は子孫のフォルダか、自分と子孫のフォルダにあるマニュアルの保護を見る。フォルダ自身の保護は見ない (delete_folder が別に断る)。
    None と保存前のフォルダは 0 と False。書かない・ロックしない (押した時は delete_folder がロックして判断し直す)。
    """
    if folder is None or getattr(folder, "pk", None) is None:
        return {"folders": 0, "manuals": 0, "protected": False}
    levels = _descendant_levels(folder)
    below = [pk for level in levels for pk in level]
    manuals = Manual.objects.filter(folder_id__in=[folder.pk, *below])
    protected = ManualFolder.objects.filter(pk__in=below, protected=True).exists() or manuals.filter(protected=True).exists()
    return {"folders": len(below), "manuals": manuals.count(), "protected": protected}


def search_manuals(query: str):
    """空白で区切った言葉を全部、題か本文に含むマニュアル (題の順)。"""
    manuals = Manual.objects.all()
    for term in (query or "").split():
        manuals = manuals.filter(Q(title__icontains=term) | Q(body__icontains=term))
    return manuals.order_by("title", "pk")


def image_url(image, *, pdf: bool = False) -> str:
    """本文の画像の URL。画面は口の URL、PDF は fetch_local が読める /media/ の道 (PDF の bytes の中だけ)。"""
    if pdf:
        return reports.MEDIA_PREFIX + image.file.name
    return reverse("manuals:manualimage_download", args=[image.pk])


def render_body(manual, *, body=None, manual_format=None, pdf=False):
    """本文を描いた Rendered。body・manual_format を渡せばその字と形式で描く (プレビュー)。"""
    images = {image.pk: image_url(image, pdf=pdf) for image in manual.images.all()} if manual.pk else {}
    return manual_markdown.render(
        manual.body if body is None else body,
        images=images,
        manual_format=manual.format if manual_format is None else manual_format,
    )
