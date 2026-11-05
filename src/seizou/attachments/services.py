"""添付ファイルの services (01 の 10.1)。表に書くのはここの関数だけ (R13)。

付ける先は contenttypes で指す (ほかの部品の model と services を使わない)。
許す形式は ALLOWED の 1 か所で決め、形式 (mime) はこの表の値を入れる (Python の標準の推測は OS で答えが違うので使わない)。
中身の先頭のバイトは見ない (CSV とテキストには印が無いため)。
"""

import os

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from .models import KIND_CHOICES, Attachment

#: 許す拡張子 (小文字) → (形式, ブラウザの中で開くか)。中で開かない物はダウンロードにする。
#: SVG と HTML は入れない (開くと script が動くため)。TIFF はブラウザの多くが開けないのでダウンロード。
ALLOWED = {
    ".png": ("image/png", True),
    ".jpg": ("image/jpeg", True),
    ".jpeg": ("image/jpeg", True),
    ".webp": ("image/webp", True),
    ".pdf": ("application/pdf", True),
    ".tif": ("image/tiff", False),
    ".tiff": ("image/tiff", False),
    ".csv": ("text/csv", False),
    ".txt": ("text/plain", False),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", False),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", False),
}

#: 付けられる大きさの上限 (バイト)
MAX_BYTES = 20 * 1024 * 1024
#: 元の名前と備考の字数の上限 (表の字数)
MAX_NAME_LENGTH = 100
MAX_NOTE_LENGTH = 500

MESSAGE_NO_TARGET = "付ける先の記録がまだ保存されていません。記録を保存してから、詳細の画面で付けてください。"
MESSAGE_SELF_TARGET = "付ける先が添付ファイルです。付けたい記録の詳細の画面から付けてください。"
MESSAGE_BAD_EXTENSION = (
    "ファイルの形式が違います。PNG・JPEG・WebP・PDF・TIFF・CSV・テキスト・.xlsx・.docx のファイルを選んでください。"
)
MESSAGE_EMPTY = "ファイルが空です。中身のあるファイルを選んでください。"
MESSAGE_TOO_LARGE = "ファイルが 20MB を超えています。分けるか小さくしてから選び直してください。"
MESSAGE_NAME_TOO_LONG = "ファイル名が 100 字を超えています。名前を短くしてから選び直してください。"
MESSAGE_BAD_KIND = "種類は写真・検査成績書・作業手順・その他のどれかを選んでください。"
MESSAGE_NOTE_TOO_LONG = "備考が 500 字を超えています。短くしてから保存してください。"
MESSAGE_ALREADY_REMOVED = "この添付ファイルは既に削除されています。付けた先の記録の詳細の画面を開き直してください。"


def extension_of(name: str) -> str:
    """ファイルの名前の拡張子 (小文字。無ければ空の字)。"""
    return os.path.splitext(name or "")[1].lower()


def opens_inline(name: str) -> bool:
    """ファイルを返すとき、ブラウザの中で開くか (ALLOWED の表。表に無ければダウンロード)。"""
    entry = ALLOWED.get(extension_of(name))
    return bool(entry and entry[1])


def _refuse_bad_target(target) -> None:
    if getattr(target, "_meta", None) is None or getattr(target, "pk", None) is None:
        raise ValueError("保存できません。" + MESSAGE_NO_TARGET)
    if isinstance(target, Attachment):
        raise ValueError("保存できません。" + MESSAGE_SELF_TARGET)


def _refuse_bad_file(file) -> tuple[str, str]:
    """ファイルを見て (元の名前, 形式) を返す。通らなければ日本語の ValueError。中身は読まない。"""
    original_name = os.path.basename(getattr(file, "name", "") or "")
    entry = ALLOWED.get(extension_of(original_name))
    if entry is None:
        raise ValueError("保存できません。" + MESSAGE_BAD_EXTENSION)
    size = getattr(file, "size", None) or 0
    if size <= 0:
        raise ValueError("保存できません。" + MESSAGE_EMPTY)
    if size > MAX_BYTES:
        raise ValueError("保存できません。" + MESSAGE_TOO_LARGE)
    if len(original_name) > MAX_NAME_LENGTH:
        raise ValueError("保存できません。" + MESSAGE_NAME_TOO_LONG)
    return original_name, entry[0]


def add_attachment(target, file, user, *, kind, note=""):
    """記録 (target) にファイルを付ける。作った Attachment を返す。

    先に付ける先・拡張子・大きさ・名前の字数・種類・備考の字数を見て、通らなければ日本語の ValueError
    (行もファイルも残さない)。通れば 1 行を作り、ファイルを storage に置く (保存の名前は乱数)。
    """
    _refuse_bad_target(target)
    original_name, mime = _refuse_bad_file(file)
    if kind not in dict(KIND_CHOICES):
        raise ValueError("保存できません。" + MESSAGE_BAD_KIND)
    note = note or ""
    if len(note) > MAX_NOTE_LENGTH:
        raise ValueError("保存できません。" + MESSAGE_NOTE_TOO_LONG)
    with transaction.atomic():
        return Attachment.objects.create(
            content_type=ContentType.objects.get_for_model(target),
            object_id=target.pk,
            file=file,
            original_name=original_name,
            kind=kind,
            mime=mime,
            size=file.size,
            note=note,
            uploaded_by=user if getattr(user, "pk", None) is not None else None,
        )


def remove_attachment(attachment, user):
    """添付ファイルを消す。付けていた先の記録を返す (付けた先が無ければ None)。

    行は atomic の中で取り直して消す (無ければ日本語の ValueError)。storage のファイルは atomic を抜けた後に消す
    (行が戻ったときにファイルだけが消えないように)。無いファイルは飛ばす。user は受けるだけ (記録の列が無い)。
    """
    with transaction.atomic():
        row = Attachment.objects.select_for_update().filter(pk=attachment.pk).first()
        if row is None:
            raise ValueError("削除できません。" + MESSAGE_ALREADY_REMOVED)
        target = row.target
        name = row.file.name
        storage = row.file.storage
        row.delete()
    if name:
        try:
            storage.delete(name)
        except FileNotFoundError:
            pass
    return target


def attachments_for(target):
    """記録の添付ファイルを新しい順に返す (読むだけ)。保存前の記録は空。"""
    if getattr(target, "_meta", None) is None or getattr(target, "pk", None) is None:
        return Attachment.objects.none()
    return (
        Attachment.objects.filter(content_type=ContentType.objects.get_for_model(target), object_id=target.pk)
        .select_related("uploaded_by")
        .order_by("-uploaded_at", "-pk")
    )
