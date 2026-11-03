"""フィードバックの services (01 の 10.1)。表に書くのはここの関数だけ (R13)。

約束:
- 書くのはここだけ。書く関数は transaction.atomic の中。番号の無い入力の誤りは日本語の ValueError
  (文は何が起きて何をすればよいか。画面の土台が赤の知らせにし、口は messages.error にする)。
- HTTP を送らない。GitHub の Issue に出すのは、世話役が Issue の文 (export_markdown) を写す (工場の PC に認証を置かない)。
- 伏せ字は取引先マスターの名前と金額らしい数だけ。品番・数量・会社の自分の名前・人の名前は伏せない。
- 画面の目録は URL の木を歩いて作るので、process の中で 1 回だけ作って覚える (screen_catalog)。
  試験は screen_catalog.cache_clear() で作り直す。
- 世話役 = アカウント管理の権限 (seizou.core.access.can_manage_accounts)。確認済みと送ったにできるのは世話役だけ。
- 画面の絵は添付ファイルの部品の services (add_attachment・attachments_for) を通して下書きに結ぶ (表は変えない)。
  添付ファイルの部品が無い会社では欄を出さず、付けようとすると ValueError。決まり (画像だけ・5MB・3 枚) は書く前に全部見る。
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

from django.apps import apps
from django.db import DatabaseError, transaction
from django.utils import timezone

from seizou.core import access, manual
from seizou.core.screens import Screen, all_screens

from .models import Feedback

#: 種類の値の並び (使い方・自社の作業・皆の要望・不具合)
KINDS = tuple(value for value, _ in Feedback.KIND_CHOICES)
#: 状態の値の並び (下書き → 確認済み → 送った)
STATUSES = tuple(value for value, _ in Feedback.STATUS_CHOICES)
#: data に入れてよい鍵
FIELD_KEYS = ("kind", "screen", "title", "problem", "situation", "wish", "screens")
#: 伏せ字
MASK = "〇〇"

#: 題の字数の上限 (表の列の長さ)
TITLE_MAX_CHARS = Feedback._meta.get_field("title").max_length
#: 送った先の URL の字数の上限 (表の列の長さ)
SENT_URL_MAX_CHARS = Feedback._meta.get_field("sent_url").max_length

MESSAGE_KIND = "種類は 使い方・自社の作業・皆の要望・不具合 のどれかです。"
MESSAGE_TITLE_EMPTY = "題を入力してください。"
MESSAGE_TITLE_LONG = f"題は {TITLE_MAX_CHARS} 字までです。短くしてください。"
MESSAGE_PROBLEM_EMPTY = "困りごとを入力してください。"
MESSAGE_CANNOT_EDIT = "この下書きは、書いた人 (下書きの間) かアカウント管理の担当者だけが直せます。"
MESSAGE_ALREADY_SENT = "この件は送った状態です。"
MESSAGE_SENT_URL = "送った先は http か https の URL にしてください。"

#: 金額らしい数 (¥・￥ の後の数と、円の前の数)
_MONEY = re.compile(r"[¥￥]\s*\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*円")
#: 影響する画面の区切り (空白とカンマ)
_SEPARATORS = re.compile(r"[\s,、，]+")


# ---- 画面の目録 ----


@lru_cache(maxsize=1)
def screen_catalog() -> dict[str, Screen]:
    """画面の目録を {URL の名前: Screen} にした物 (process の中で 1 回だけ作る)。"""
    return {screen.url_name: screen for screen in all_screens() if screen.url_name}


def screen_choices() -> list[tuple[str, str]]:
    """入力の欄の選択肢 [(URL の名前, 「名前 (URL の名前)」), ...] (目録の順)。"""
    return [(url_name, f"{screen.name} ({url_name})") for url_name, screen in screen_catalog().items()]


def screen_name(url_name) -> str:
    """目録の画面の名前。目録に無ければ空の字。"""
    screen = screen_catalog().get(url_name) if isinstance(url_name, str) else None
    return screen.name if screen is not None else ""


def normalize_screens(value) -> str:
    """影響する画面の字 (空白とカンマで区切る) を、重ねない出た順の並びにして空白で結ぶ。

    目録に無い名前があれば日本語の ValueError。空なら空の字。
    """
    text = "" if value is None else str(value)
    names = list(dict.fromkeys(name for name in _SEPARATORS.split(text) if name))
    unknown = [name for name in names if name not in screen_catalog()]
    if unknown:
        raise ValueError(
            f"影響する画面に、画面の目録に無い名前があります: {'・'.join(unknown)}。"
            "list_screens の url_name (例 orders:order_list) で書いてください。"
        )
    return " ".join(names)


def list_filter(params) -> dict:
    """一覧の絞り込みの今の値 {"status": 字, "kind": 字}。画面の get_queryset と tag の feedback_filter が読む。

    params は GET の QueryDict か辞書 (.get で読む)。値が STATUSES・KINDS の中なら字のまま、
    外の値・空・無い鍵は空の字 (絞らない)。
    """
    if not hasattr(params, "get"):
        params = {}
    status = params.get("status")
    kind = params.get("kind")
    return {"status": status if status in STATUSES else "", "kind": kind if kind in KINDS else ""}


# ---- 権限 ----


def can_manage(user) -> bool:
    """世話役か (アカウント管理の権限かフルコントロールかシステム管理者。権限の部品が無い会社ではシステム管理者だけ)。"""
    return access.can_manage_accounts(user)


def can_edit(feedback, user) -> bool:
    """直せるか。書いた人で状態が下書きのとき、か世話役。"""
    if can_manage(user):
        return True
    user_pk = getattr(user, "pk", None)
    return user_pk is not None and feedback.created_by_id == user_pk and feedback.status == "draft"


# ---- 画面の絵 ----

#: 画面の絵に許す拡張子 (小文字。添付ファイルの部品がブラウザの中で開く画像の形式)
SCREENSHOT_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
#: 入力の欄の accept の字
SCREENSHOT_ACCEPT = "image/png,image/jpeg,image/webp"
#: 1 枚の大きさの上限 (MB と、同じ上限のバイト)
SCREENSHOT_MAX_MB = 5
SCREENSHOT_MAX_BYTES = SCREENSHOT_MAX_MB * 1024 * 1024
#: 1 回に付けられる枚数
SCREENSHOT_MAX_COUNT = 3
#: 添付ファイルの種類 (その他。写真はカメラで撮った物のため) と備考
SCREENSHOT_KIND = "other"
SCREENSHOT_NOTE = "画面の絵"

MESSAGE_SCREENSHOT_NO_PART = "添付ファイルの部品が入っていない会社では、画面の絵を付けられません。"
MESSAGE_SCREENSHOT_NOT_LIST = "画面の絵は、ファイルの並びで渡してください。"
MESSAGE_SCREENSHOT_COUNT = (
    f"画面の絵は 1 回に {SCREENSHOT_MAX_COUNT} 枚までです。"
    f"{SCREENSHOT_MAX_COUNT} 枚までにして保存し、残りは編集の画面で付けてください。"
)
MESSAGE_SCREENSHOT_TYPE = "画面の絵は PNG・JPEG・WebP の画像だけを付けられます。画像のファイルを選び直してください。"
MESSAGE_SCREENSHOT_EMPTY = "画面の絵のファイルが空です。中身のある画像を選び直してください。"
MESSAGE_SCREENSHOT_SIZE = f"画面の絵は 1 枚 {SCREENSHOT_MAX_MB}MB までです。小さくしてから選び直してください。"


def screenshots_enabled() -> bool:
    """画面の絵を付けられる会社か (添付ファイルの部品が入っているか)。入力の欄の有無もこれで決める。"""
    return apps.is_installed("seizou.attachments")


def _attachments_services():
    """添付ファイルの部品の services。部品が無い会社では None。

    purchasing → stock と同じく、入っていない部品を import しない (import は関数の中のここだけ)。
    """
    if not screenshots_enabled():
        return None
    from seizou.attachments import services as attachments_services

    return attachments_services


def _checked_screenshots(screenshots) -> list:
    """画面の絵の決まりを書く前に全部見て、通ったファイルの並びを返す。DB と storage に触らない。

    並びでなければ ValueError。空の値 (None・選ばなかった欄) を除いて空なら [] (部品が無い会社でも断らない)。
    部品の有無 → 枚数 → 1 枚ずつ 形式 → 空 → 大きさ → 名前の字数 の順に見て、外れたら日本語の ValueError。
    名前の字数は添付ファイルの部品の上限と文で先に見る (add_attachment が 2 枚目で断ると、1 枚目のファイルが storage に残るため)。
    """
    if not isinstance(screenshots, (list, tuple)):
        raise ValueError(MESSAGE_SCREENSHOT_NOT_LIST)
    files = [file for file in screenshots if file]
    if not files:
        return []
    attachments_services = _attachments_services()
    if attachments_services is None:
        raise ValueError(MESSAGE_SCREENSHOT_NO_PART)
    if len(files) > SCREENSHOT_MAX_COUNT:
        raise ValueError(MESSAGE_SCREENSHOT_COUNT)
    for file in files:
        name = os.path.basename(getattr(file, "name", "") or "")
        if attachments_services.extension_of(name) not in SCREENSHOT_EXTENSIONS:
            raise ValueError(MESSAGE_SCREENSHOT_TYPE)
        size = getattr(file, "size", None) or 0
        if size <= 0:
            raise ValueError(MESSAGE_SCREENSHOT_EMPTY)
        if size > SCREENSHOT_MAX_BYTES:
            raise ValueError(MESSAGE_SCREENSHOT_SIZE)
        if len(name) > attachments_services.MAX_NAME_LENGTH:
            raise ValueError(attachments_services.MESSAGE_NAME_TOO_LONG)
    return files


def _attach_screenshots(feedback, files, user) -> None:
    """確かめの済んだファイルを、並びの順に 1 枚ずつ添付ファイルとして行に結ぶ (書く関数の atomic の中で呼ぶ)。"""
    if not files:
        return
    attachments_services = _attachments_services()
    for file in files:
        attachments_services.add_attachment(feedback, file, user, kind=SCREENSHOT_KIND, note=SCREENSHOT_NOTE)


def screenshots_of(feedback) -> list:
    """下書きの画像の添付ファイルの並び (付けた順)。読むだけ。

    入力の形で付けた物と、添付ファイルの枠から付けた画像 (PNG・JPEG・WebP) の両方。PDF・TIFF・CSV などは入れない。
    部品が無い会社と保存前の行は空の並び。
    """
    attachments_services = _attachments_services()
    if attachments_services is None or getattr(feedback, "pk", None) is None:
        return []
    rows = list(attachments_services.attachments_for(feedback))
    rows.reverse()
    return [row for row in rows if attachments_services.extension_of(row.original_name) in SCREENSHOT_EXTENSIONS]


# ---- 書く ----


def _refuse_unknown_keys(data) -> None:
    if not isinstance(data, dict):
        raise ValueError("フィードバックの値は、項目の名前と値の組で渡してください。")
    unknown = sorted(set(data) - set(FIELD_KEYS))
    if unknown:
        raise ValueError(f"知らない項目 ({'・'.join(map(str, unknown))}) があります。フィードバックの列の名前だけを渡してください。")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _checked_value(key, value):
    """1 つの鍵の値を確かめて整える。外れたら日本語の ValueError。"""
    if key == "kind":
        kind = _text(value)
        if kind not in KINDS:
            raise ValueError(MESSAGE_KIND)
        return kind
    if key == "title":
        title = _text(value)
        if not title:
            raise ValueError(MESSAGE_TITLE_EMPTY)
        if len(title) > TITLE_MAX_CHARS:
            raise ValueError(MESSAGE_TITLE_LONG)
        return title
    if key == "problem":
        problem = _text(value)
        if not problem:
            raise ValueError(MESSAGE_PROBLEM_EMPTY)
        return problem
    if key == "screen":
        screen = _text(value)
        if screen and screen not in screen_catalog():
            raise ValueError(f"画面 {screen} は画面の目録にありません。選び直すか、「指定なし」にしてください。")
        return screen
    if key == "screens":
        return normalize_screens(value)
    return _text(value)


def _clean_transcript(transcript) -> list[dict]:
    """会話の記録のうち、形の合う物 (role が user か assistant で、text が字) だけを写す。"""
    if not isinstance(transcript, (list, tuple)):
        return []
    return [
        {"role": turn["role"], "text": turn["text"]}
        for turn in transcript
        if isinstance(turn, dict) and turn.get("role") in ("user", "assistant") and isinstance(turn.get("text"), str)
    ]


def _tokens(value) -> int:
    """0 以上の整数に丸める (数でない物は 0)。"""
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(number, 0)


def _author(user):
    return user if getattr(user, "pk", None) is not None else None


def create_feedback(data, user, *, transcript=(), input_tokens=0, output_tokens=0, screenshots=()):
    """フィードバックを 1 件作る (状態は下書き)。作った行を返す。

    data は FIELD_KEYS の鍵の辞書 (知らない鍵は ValueError。無い鍵は種類が皆の要望、ほかは空)。
    書いた人は user に pk があれば user。会話の記録は形の合う物だけ、token は 0 以上の整数に丸める。
    screenshots は画面の絵のファイルの並び (画像だけ・1 枚 5MB・3 枚まで。外れたら行もファイルも作らずに ValueError)。添付ファイルの部品で行に結ぶ。
    """
    _refuse_unknown_keys(data)
    values = {key: _checked_value(key, data.get(key, "common" if key == "kind" else "")) for key in FIELD_KEYS}
    files = _checked_screenshots(screenshots)
    with transaction.atomic():
        row = Feedback.objects.create(
            **values,
            status="draft",
            created_by=_author(user),
            transcript=_clean_transcript(transcript),
            input_tokens=_tokens(input_tokens),
            output_tokens=_tokens(output_tokens),
        )
        _attach_screenshots(row, files, user)
        return row


def update_feedback(feedback, data, user, *, screenshots=()):
    """data にある鍵だけを書き換える (create_feedback と同じ確かめ)。状態・token・会話の記録は変えない。

    直せるのは、書いた人 (下書きの間) か世話役だけ (外は ValueError)。
    screenshots は create_feedback と同じ (直せる人だけが付けられる。直せない人は絵も付けない)。
    """
    if not can_edit(feedback, user):
        raise ValueError(MESSAGE_CANNOT_EDIT)
    _refuse_unknown_keys(data)
    values = {key: _checked_value(key, value) for key, value in data.items()}
    files = _checked_screenshots(screenshots)
    with transaction.atomic():
        for key, value in values.items():
            setattr(feedback, key, value)
        feedback.save(update_fields=[*values, "updated_at"])
        _attach_screenshots(feedback, files, user)
        return feedback


def _refuse_non_manager(user, what) -> None:
    if not can_manage(user):
        raise ValueError(f"{what}にできるのはアカウント管理の担当者だけです。" + access.CONTACT)


def mark_confirmed(feedback, user):
    """下書き → 確認済み。世話役だけ。下書きでなければ ValueError。"""
    _refuse_non_manager(user, "確認済み")
    if feedback.status != "draft":
        raise ValueError(f"下書きだけを確認済みにできます (今は {feedback.get_status_display()})。")
    with transaction.atomic():
        feedback.status = "confirmed"
        feedback.save(update_fields=["status", "updated_at"])
        return feedback


def mark_sent(feedback, user, sent_url=""):
    """下書きか確認済み → 送った。世話役だけ。送った物をもう一度は ValueError。

    sent_url は空か、http:// か https:// で始まる 200 字までの字 (GitHub の Issue の URL など。字として持つだけ)。
    """
    _refuse_non_manager(user, "送った状態")
    if feedback.status == "sent":
        raise ValueError(MESSAGE_ALREADY_SENT)
    url = _text(sent_url)
    if url and (not url.startswith(("http://", "https://")) or len(url) > SENT_URL_MAX_CHARS or any(ch.isspace() for ch in url)):
        raise ValueError(MESSAGE_SENT_URL)
    with transaction.atomic():
        feedback.status = "sent"
        feedback.sent_url = url
        feedback.save(update_fields=["status", "sent_url", "updated_at"])
        return feedback


#: 削除の断りの文 (口は messages.error にする)
MESSAGE_ALREADY_DELETED = "この下書きは既にありません。一覧を開き直してください。"
MESSAGE_CANNOT_DELETE = "この下書きは、書いた人かアカウント管理の担当者だけが削除できます。"
#: {status} に今の状態の表示 (get_status_display) を入れる
MESSAGE_DELETE_DRAFT_ONLY = "下書きだけを削除できます (今は {status})。確認済みと送った物は残します。"


def can_delete(feedback, user) -> bool:
    """削除できるか。状態が下書きで、直せる人 (書いた人か世話役) のとき。"""
    return feedback.status == "draft" and can_edit(feedback, user)


def delete_feedback(feedback, user) -> None:
    """下書きを 1 件消す。

    下書きだけ。書いた人か世話役 (アカウント管理の権限)。確認済みと送った物は残す (世話役が GitHub に写した物と、
    その前の確かめの印)。会話の記録と token も一緒に消える。
    行は atomic の中で select_for_update で取り直し、無い → 状態 → 人の順に確かめる (外れたら日本語の ValueError)。
    """
    with transaction.atomic():
        row = Feedback.objects.select_for_update().filter(pk=feedback.pk).first()
        if row is None:
            raise ValueError(MESSAGE_ALREADY_DELETED)
        if row.status != "draft":
            raise ValueError(MESSAGE_DELETE_DRAFT_ONLY.format(status=row.get_status_display()))
        if not can_edit(row, user):
            raise ValueError(MESSAGE_CANNOT_DELETE)
        row.delete()


# ---- Issue の文 ----


def _partner_names() -> list[str]:
    """取引先マスターの名前と略称のうち 2 字以上の物 (重ねない・長い順)。DB を読むのは 1 回。

    masters が無い会社・表が読めない (DatabaseError) ときは空の並び。
    """
    if not apps.is_installed("seizou.masters"):
        return []
    try:
        model = apps.get_model("masters", "Partner")
        rows = list(model._default_manager.values_list("name", "short_name"))
    except (LookupError, DatabaseError):
        return []
    names = {name.strip() for row in rows for name in row if isinstance(name, str) and len(name.strip()) >= 2}
    return sorted(names, key=lambda name: (-len(name), name))


def _mask(text, names) -> str:
    text = "" if text is None else str(text)
    for name in names:
        text = text.replace(name, MASK)
    return _MONEY.sub(MASK, text)


def mask_text(text) -> str:
    """取引先マスターの名前と略称 (2 字以上。長い順) と、金額らしい数 (¥・円) を MASK に置き換える。"""
    return _mask(text, _partner_names())


def export_markdown(feedback) -> str:
    """GitHub の Issue に写す文 (題・種類・画面・影響する画面・作品の版・書いた日・困りごと・場面・望む形)。

    取引先の名前と金額らしい数は伏せる。会話の記録・書いた人・会社名は入れない。空の節は出さない。
    """
    names = _partner_names()
    lines = [
        f"# {_mask(feedback.title, names).strip()}",
        "",
        f"- 種類: {feedback.get_kind_display()}",
        f"- 画面: {feedback.get_screen_display() or '指定なし'}",
    ]
    if feedback.screens.strip():
        lines.append(f"- 影響する画面: {feedback.screens.strip()}")
    lines.append(f"- 作品の版: seizou {manual.package_version() or '不明'}")
    if feedback.created_at is not None:
        lines.append(f"- 書いた日: {timezone.localdate(feedback.created_at).isoformat()}")
    for heading, value in (("困りごと", feedback.problem), ("場面", feedback.situation), ("望む形", feedback.wish)):
        body = _mask(value, names).strip()
        if body:
            lines.extend(["", f"## {heading}", body])
    return "\n".join(lines) + "\n"
