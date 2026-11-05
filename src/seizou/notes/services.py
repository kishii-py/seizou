"""書式つきのメモの services (01 の 10.1)。表に書くのはここの関数だけ (R13)。

付ける先は contenttypes で指す (ほかの部品の model と services を使わない)。1 つの記録にメモは 1 つ。
本文の HTML は clean_html の 1 か所で掃除する (nh3。許す tag と属性を ALLOWED_TAGS・ALLOWED_ATTRIBUTES に絞る)。
保存の前 (save_note) と、描く前 (notes_tags の note_html) の両方で同じ関数を通す。
写真は 1 枚ずつ受けて /media/notes/ の下に置き、その URL を本文の <img src> に入れる (外の src は掃除で消える)。
"""

import os
import re

import nh3
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from .models import Note, NoteImage

#: 本文に許す tag (Quill の toolbar の書式 = 見出し・太字・斜体・下線・取り消し線・色・箇条書き・番号・引用・写真・link)
ALLOWED_TAGS = frozenset({"p", "br", "strong", "em", "u", "s", "h2", "h3", "ul", "ol", "li", "a", "img", "blockquote", "span"})
#: tag ごとに許す属性。span の style は色の 2 つ (ALLOWED_STYLES) だけを残す
ALLOWED_ATTRIBUTES = {"a": {"href"}, "img": {"src", "alt"}, "span": {"style"}}
ALLOWED_STYLES = frozenset({"color", "background-color"})
#: link の先に許す形 (小文字で比べる)。相対の道と javascript: は消す
ALLOWED_HREF_PREFIXES = ("http://", "https://", "mailto:")
#: 中身ごと消す tag (字を残すと script の中身が本文に出るため)
DROPPED_CONTENT_TAGS = frozenset({"script", "style"})
#: 写真の src に許す頭 (core.media の口がログインした人にだけ返す道)
IMAGE_SRC_PREFIX = "/media/notes/"
_EMPTY_IMAGE = re.compile(r"<img(?![^>]*\ssrc=)[^>]*>")

#: 貼れる写真の拡張子 (小文字)。SVG は入れない (開くと script が動くため)
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
#: 貼れる写真の大きさの上限 (バイト)
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_NAME_LENGTH = 100
#: 本文 (掃除の前の HTML) の字数の上限
MAX_BODY_CHARS = 200_000

MESSAGE_NO_TARGET = "メモを付ける先の記録がまだ保存されていません。記録を保存してから、詳細の画面で書いてください。"
MESSAGE_SELF_TARGET = "付ける先がメモです。付けたい記録の詳細の画面から書いてください。"
MESSAGE_TOO_LONG = "メモが長すぎます。20 万字より短くしてから保存してください。"
MESSAGE_NO_IMAGE = "写真のファイルがありません。貼る写真を選び直してください。"
MESSAGE_BAD_IMAGE = "写真の形式が違います。PNG・JPEG・WebP・GIF の写真を選んでください。"
MESSAGE_EMPTY_IMAGE = "写真のファイルが空です。中身のある写真を選んでください。"
MESSAGE_IMAGE_TOO_LARGE = "写真が 10MB を超えています。小さくしてから選び直してください。"
MESSAGE_NAME_TOO_LONG = "ファイル名が 100 字を超えています。名前を短くしてから選び直してください。"


def _keep_attribute(tag, attribute, value):
    """nh3 の attribute_filter。写真の src は /media/notes/ の下だけ、link の先は http・https・mailto だけを残す。"""
    if tag == "img" and attribute == "src":
        return value if value.startswith(IMAGE_SRC_PREFIX) and ".." not in value else None
    if tag == "a" and attribute == "href":
        return value if value.strip().lower().startswith(ALLOWED_HREF_PREFIXES) else None
    return value


def clean_html(html) -> str:
    """本文の HTML を掃除する (掃除の関数はこの 1 つ)。許す tag と属性の外を消し、src の無くなった <img> も消す。"""
    cleaned = nh3.clean(
        str(html or ""),
        tags=set(ALLOWED_TAGS),
        clean_content_tags=set(DROPPED_CONTENT_TAGS),
        attributes={tag: set(names) for tag, names in ALLOWED_ATTRIBUTES.items()},
        attribute_filter=_keep_attribute,
        url_schemes={"http", "https", "mailto"},
        filter_style_properties=set(ALLOWED_STYLES),
        link_rel="noopener noreferrer",
    )
    return _EMPTY_IMAGE.sub("", cleaned).strip()


def is_blank(html) -> bool:
    """本文に字も写真も無いか (Quill の空の本文 <p><br></p> を含む)。"""
    if "<img" in (html or ""):
        return False
    text = re.sub(r"<[^>]+>", "", html or "").replace("&nbsp;", " ")
    return not text.strip()


def _refuse_bad_target(target) -> None:
    if getattr(target, "_meta", None) is None or getattr(target, "pk", None) is None:
        raise ValueError("保存できません。" + MESSAGE_NO_TARGET)
    if isinstance(target, (Note, NoteImage)):
        raise ValueError("保存できません。" + MESSAGE_SELF_TARGET)


def note_for(target) -> Note:
    """記録のメモを返す (読むだけ)。無ければ保存していない空のメモ (pk は None。表に書かない)。"""
    _refuse_bad_target(target)
    content_type = ContentType.objects.get_for_model(target)
    note = Note.objects.filter(content_type=content_type, object_id=target.pk).select_related("updated_by").first()
    return note if note is not None else Note(content_type=content_type, object_id=target.pk)


def _user_or_none(user):
    return user if getattr(user, "pk", None) is not None else None


def save_note(target, html, user) -> Note:
    """記録 (target) のメモを保存する。1 つの記録に 1 つ (2 回目からは上書き)。掃除した HTML を書き、メモを返す。

    字数の上限を超えるときと、付ける先が無いときは日本語の ValueError (表に書かない)。
    """
    _refuse_bad_target(target)
    html = str(html or "")
    if len(html) > MAX_BODY_CHARS:
        raise ValueError("保存できません。" + MESSAGE_TOO_LONG)
    body = clean_html(html)
    if is_blank(body):
        body = ""
    with transaction.atomic():
        note, _created = Note.objects.select_for_update().get_or_create(
            content_type=ContentType.objects.get_for_model(target),
            object_id=target.pk,
        )
        note.body_html = body
        note.updated_by = _user_or_none(user)
        note.save()
    return note


def _refuse_bad_image(file) -> str:
    """写真を見て元の名前を返す。通らなければ日本語の ValueError。中身は読まない。"""
    if file is None:
        raise ValueError("保存できません。" + MESSAGE_NO_IMAGE)
    original_name = os.path.basename(getattr(file, "name", "") or "")
    if os.path.splitext(original_name)[1].lower() not in IMAGE_EXTENSIONS:
        raise ValueError("保存できません。" + MESSAGE_BAD_IMAGE)
    size = getattr(file, "size", None) or 0
    if size <= 0:
        raise ValueError("保存できません。" + MESSAGE_EMPTY_IMAGE)
    if size > MAX_IMAGE_BYTES:
        raise ValueError("保存できません。" + MESSAGE_IMAGE_TOO_LARGE)
    if len(original_name) > MAX_NAME_LENGTH:
        raise ValueError("保存できません。" + MESSAGE_NAME_TOO_LONG)
    return original_name


def add_image(note, file, user) -> NoteImage:
    """メモ (note) に写真を 1 枚置く。作った NoteImage を返す (URL は image.file.url = /media/notes/…)。

    note が保存していない空のメモ (note_for が返す pk の無い物) なら、同じ atomic の中でメモの行を先に作る
    (本文はまだ空。保存のボタンで save_note が書く)。形式と大きさが通らなければ日本語の ValueError (行もファイルも残さない)。
    """
    original_name = _refuse_bad_image(file)
    with transaction.atomic():
        if note.pk is None:
            note, _created = Note.objects.get_or_create(
                content_type=note.content_type,
                object_id=note.object_id,
                defaults={"updated_by": _user_or_none(user)},
            )
        return NoteImage.objects.create(
            note=note,
            file=file,
            original_name=original_name,
            size=file.size,
            uploaded_by=_user_or_none(user),
        )
