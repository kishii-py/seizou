"""添付ファイルの services (01 の 10.1)。表に書くのはここの関数だけ (R13)。

付ける先は contenttypes で指す (ほかの部品の model と services を使わない)。
許す形式は ALLOWED の 1 か所で決め、形式 (mime) はこの表の値を入れる (Python の標準の推測は OS で答えが違うので使わない)。
中身の先頭のバイトは見ない (CSV とテキストには印が無いため)。
"""

import os

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q

from seizou.core import access

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
    # CAD のファイル。図面としては受けないので添付で持つ (masters の図面の受け口。決め 11-08 00:16)。中では開かない
    ".dxf": ("application/octet-stream", False),
    ".dwg": ("application/octet-stream", False),
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
MESSAGE_CANNOT_OPEN_TARGET = "付ける先の記録を開けません。付けたい記録の詳細の画面から付けてください。"
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
    if getattr(user, "is_authenticated", False) and not can_open_target(target, user):
        # 訳: 付ける (保存) も、読む・消す・一覧 (openable_by) と同じ人だけにする。何のため = 部署の外の記録やほかの人の
        # フィードバックにファイルを付けられると、開けない記録を外から変えられるため。出所 = Claude Security の走査の候補 10
        # (11-07。係 sB は下見だけを直し、保存は本体が門で足した)。変えてよい条件 = 付ける先を開けない人に付けさせる業務の理由が出たとき。
        raise ValueError("保存できません。" + MESSAGE_CANNOT_OPEN_TARGET)
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


#: 書いた本人と世話役だけが開ける記録の種類 (app の名前, model の名前)。フィードバックは一覧と詳細を自分の分に絞る (feedback.views._visible_to)
FEEDBACK_TYPE = ("feedback", "feedback")


def _feedback_manager(user) -> bool:
    """フィードバックの世話役か (feedback.services.can_manage)。feedback の部品が無い会社では偽。

    feedback の services は関数の中で import する (feedback の services も attachments を関数の中で import するので、外で import すると輪になる)。
    """
    if not apps.is_installed("seizou.feedback"):
        return False
    from seizou.feedback import services as feedback_services

    return feedback_services.can_manage(user)


def openable_by(queryset, user):
    """添付ファイルの並びを、user が付けた先の記録を開ける物だけに絞る (読むだけ)。開く口・削除の画面・一覧・移るだけの口が使う。

    1) 付けた先の部品を、部署で開けない (access.can_open。記録は書かない) 種類の行を外す。部署の判定が off と log の会社・
       システム管理者・フルコントロール・部署の無い人には何も外さない (画面の門 screen_denied と同じ判定)。アカウント管理の部品は
       アカウント管理の権限で決まる。
    2) フィードバックに付けた行は、世話役 (feedback.services.can_manage) でなければ、user が書いたフィードバックの物だけを残す。
       付けた先が消えた行は、種類 (content_type) で 1) を見て、フィードバックなら世話役だけに残す。
    訳: 記録を開けない人に、その記録に付けたファイル (フィードバックの画面の絵・部署の外の記録の写真や検査成績書) を開かせず、消させず、
    一覧で題とファイル名を見せないため。添付は番号を順に変えるだけで開けたので、記録の側の絞りが添付の口で抜けていた。
    出所 = Claude Security の走査の候補 11-07 (7・8・9)。判定は access.can_open と feedback の一覧と詳細の絞り (_visible_to) の写し。
    変えてよい条件 = 記録の部品の開ける人の決め (部署の門・フィードバックの絞り) が変わったとき。部品の判定と食い違う形にはしない。
    """
    # 種類は副問い合わせで引く (並びを外す。並びの列が SELECT に入ると、種類でなく行ごとの並びになるため)
    used_types = queryset.order_by().values("content_type_id")
    hidden = [
        kind.pk
        for kind in ContentType.objects.filter(pk__in=used_types)
        if not access.can_open(user, kind.app_label, record=False)
    ]
    if hidden:
        queryset = queryset.exclude(content_type_id__in=hidden)
    feedback_type = ContentType.objects.filter(app_label=FEEDBACK_TYPE[0], model=FEEDBACK_TYPE[1]).first()
    model = feedback_type.model_class() if feedback_type is not None else None
    if model is not None and not _feedback_manager(user):
        user_pk = getattr(user, "pk", None)
        own = model._default_manager.filter(created_by_id=user_pk).values("pk") if user_pk is not None else model._default_manager.none().values("pk")
        queryset = queryset.filter(~Q(content_type=feedback_type) | Q(object_id__in=own))
    return queryset


def can_open_target(target, user) -> bool:
    """user が記録 (target) を開けるか (読むだけ)。付ける入力の頭の「付ける先」の 1 行を出すかの判定。

    openable_by の 1 件の形: 付けた先の部品を部署で開けるか (access.can_open。記録は書かない) と、フィードバックは書いた本人か世話役か。
    訳: 開けない記録 (ほかの人のフィードバック・アカウント管理の部品の行・部署の外の記録) の題やログイン ID を、
    付ける入力の頭の 1 行で、種類と番号を変えるだけで読ませないため。出所 = Claude Security の走査の候補 11-07 (10)。
    変えてよい条件 = openable_by と同じ (2 つは同じ判定にそろえる)。
    """
    meta = getattr(target, "_meta", None)
    if meta is None or not access.can_open(user, meta.app_label, record=False):
        return False
    if (meta.app_label, meta.model_name) == FEEDBACK_TYPE and not _feedback_manager(user):
        user_pk = getattr(user, "pk", None)
        return user_pk is not None and getattr(target, "created_by_id", None) == user_pk
    return True
