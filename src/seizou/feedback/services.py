"""フィードバックの services (01 の 10.1)。表に書くのはここの関数だけ (R13)。

約束:
- 書くのはここだけ。書く関数は transaction.atomic の中。番号の無い入力の誤りは日本語の ValueError
  (文は何が起きて何をすればよいか。画面の土台が赤の知らせにし、口は messages.error にする)。
- GitHub へは、世話役がトークンを置いた会社 (github.py。鍵は置き場の .env だけ) だけが送る。確認済みの下書きを send_to_github で
  Issue にし、閉じた Issue を sync_github で閉じた理由と直る版で分ける (直した・見送った・版待ち)。HTTP は transaction.atomic の外で
  送る (R17。送る前に書く物は無く、送った後に書く物は mark_sent・_write_fixed・_write_dismissed・_save_fix_version の atomic)。トークンが無い会社は今までどおり、世話役が Issue の文 (export_markdown) を手で写す。
- 皆の Issue を探す search_issues は読むだけ (トークンは無くてよい。返事は django.core.cache に 5 分だけ控える)。
- 伏せ字は取引先マスターの名前と金額らしい数だけ。品番・数量・会社の自分の名前・人の名前は伏せない。
- 画面の目録は URL の木を歩いて作るので、process の中で 1 回だけ作って覚える (screen_catalog)。
  試験は screen_catalog.cache_clear() で作り直す。
- 世話役 = アカウント管理の権限 (seizou.core.access.can_manage_accounts)。確認済みと送ったにできるのは世話役だけ。
- 画面の絵は添付ファイルの部品の services (add_attachment・attachments_for) を通して下書きに結ぶ (表は変えない)。
  添付ファイルの部品が無い会社では欄を出さず、付けようとすると ValueError。決まり (画像だけ・5MB・3 枚) は書く前に全部見る。
- 直した (fixed) は世話役が mark_fixed で、どの状態からでも付ける。書いた人がログインしたときに 1 回だけ知らせる (signals.py)。
- GitHub でログインしてつなぐ (依頼書 N320。決め 11-07 09:2x): OAuth のデバイスの認証。始める・待つ・やめるの途中は
  利用者の session (DEVICE_KEY) にだけ置き、表に書かない。承認が済んだら、トークンを貼る形と同じ _store_github で .env に書く。
- 見送った (dismissed) は sync_github だけが付ける (GitHub で対応しない・重複として閉じた Issue)。直したと別の列に日時と知らせた印を持ち、
  書いた人がログインしたときに 1 回だけ知らせる (依頼書 N322。決め 11-07 09:0x〜09:4x)。
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from datetime import UTC
from functools import lru_cache

from django.apps import apps
from django.core.cache import cache
from django.db import DatabaseError, transaction
from django.db.models import F
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from seizou.core import access, manual
from seizou.core.screens import Screen, all_screens

from . import github
from .models import Feedback
from .models import FeedbackReply

#: 種類の値の並び (CONTRIBUTING.md の Issue の型の 5 つ = 不具合・決まりの反例・決まりの提案・業務の流れの違い・要望 と、型の外の使い方)
KINDS = tuple(value for value, _ in Feedback.KIND_CHOICES)
#: 状態の値の並び (下書き → 確認済み → 送った と、世話役がどの状態からでも付ける直した と、取り込みが付ける見送った)
STATUSES = tuple(value for value, _ in Feedback.STATUS_CHOICES)
#: data に入れてよい鍵
FIELD_KEYS = ("kind", "screen", "title", "problem", "situation", "wish", "screens")
#: 伏せ字
MASK = "〇〇"

#: 題の字数の上限 (表の列の長さ)
TITLE_MAX_CHARS = Feedback._meta.get_field("title").max_length
#: 送った先の URL の字数の上限 (表の列の長さ)
SENT_URL_MAX_CHARS = Feedback._meta.get_field("sent_url").max_length
#: 直した場所の字数の上限 (表の列の長さ)
FIXED_NOTE_MAX_CHARS = Feedback._meta.get_field("fixed_note").max_length

MESSAGE_KIND = f"種類は {'・'.join(label for _, label in Feedback.KIND_CHOICES)} のどれかです。"
MESSAGE_TITLE_EMPTY = "題を入力してください。"
MESSAGE_TITLE_LONG = f"題は {TITLE_MAX_CHARS} 字までです。短くしてください。"
MESSAGE_PROBLEM_EMPTY = "困りごとを入力してください。"
MESSAGE_CANNOT_EDIT = "この下書きは、書いた人 (下書きの間) かアカウント管理の担当者だけが直せます。"
MESSAGE_ALREADY_SENT = "この件は送った状態です。"
MESSAGE_SENT_URL = "送った先は http か https の URL にしてください。"
MESSAGE_ALREADY_FIXED = "この件は既に直した状態です。"
MESSAGE_FIXED_NOTE_LONG = f"直した場所は {FIXED_NOTE_MAX_CHARS} 字までです。短くしてください。"
MESSAGE_ALREADY_DISMISSED = "この件は既に見送った状態です。"
MESSAGE_DISMISSED_NOTE_LONG = f"見送った理由の場所は {FIXED_NOTE_MAX_CHARS} 字までです。短くしてください。"
#: 直る版の字数の上限 (表の列の長さ)
FIX_VERSION_MAX_CHARS = Feedback._meta.get_field("fix_version").max_length

#: 金額らしい数 (¥・￥ の後の数と、円の前の数)
_MONEY = re.compile(r"[¥￥]\s*\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*円")
#: 影響する画面の区切り (「、」「,」「，」と改行)。画面の名前に空白を含む物があるので、空白は分けた 1 つが引けないときだけ区切る
_NAME_SEPARATORS = re.compile(r"[,、，\n]+")


# ---- 画面の目録 ----


@lru_cache(maxsize=1)
def screen_catalog() -> dict[str, Screen]:
    """画面の目録を {URL の名前: Screen} にした物 (process の中で 1 回だけ作る)。"""
    return {screen.url_name: screen for screen in all_screens() if screen.url_name}


def screen_choices() -> list[tuple[str, str]]:
    """入力の欄の選択肢 [(URL の名前, 画面の名前), ...] (目録の順)。画面に URL の名前を出さない。同じ名前の 2 つ目からは「名前 (2)」。"""
    seen: dict[str, int] = {}
    choices = []
    for url_name, screen in screen_catalog().items():
        seen[screen.name] = seen.get(screen.name, 0) + 1
        count = seen[screen.name]
        choices.append((url_name, screen.name if count == 1 else f"{screen.name} ({count})"))
    return choices


def _screen_words() -> dict[str, list[str]]:
    """URL の名前・画面の名前・選択肢の見出しのそれぞれから、URL の名前の並びを引く表 (画面の名前が同じ画面は目録の順に全部)。"""
    words: dict[str, list[str]] = {}
    catalog = screen_catalog()
    for url_name, label in screen_choices():
        for word in (url_name, catalog[url_name].name, label):
            found = words.setdefault(word, [])
            if url_name not in found:
                found.append(url_name)
    return words


def screen_name(url_name) -> str:
    """目録の画面の名前。目録に無ければ空の字。"""
    screen = screen_catalog().get(url_name) if isinstance(url_name, str) else None
    return screen.name if screen is not None else ""


def normalize_screens(value) -> str:
    """影響する画面の字を、URL の名前の重ねない出た順の並びにして空白で結ぶ (保存する字と Issue の文は今のまま)。

    画面の名前でも URL の名前でも受ける。先に「、」「,」「，」と改行で分け、分けた 1 つが画面の名前か URL の名前なら
    それ、無ければその 1 つを空白で分けて 1 つずつ引く (画面の名前に空白を含む物があるため。AI は URL の名前を空白で書く)。
    画面の名前が同じ画面は全部。目録に無い名前があれば日本語の ValueError。空なら空の字。
    """
    text = "" if value is None else str(value)
    words = _screen_words()
    found: list[str] = []
    unknown: list[str] = []
    for part in _NAME_SEPARATORS.split(text):
        part = part.strip()
        if not part:
            continue
        if part in words:
            found += words[part]
            continue
        for word in part.split():
            if word in words:
                found += words[word]
            else:
                unknown.append(word)
    if unknown:
        raise ValueError(
            f"保存できません。影響する画面に、画面の目録に無い名前があります: {'・'.join(dict.fromkeys(unknown))}。"
            "画面の名前を「、」で区切って書いてください (例 受注、生産管理ボード)。"
        )
    return " ".join(dict.fromkeys(found))


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
        raise ValueError("保存できません。" + MESSAGE_SCREENSHOT_NOT_LIST)
    files = [file for file in screenshots if file]
    if not files:
        return []
    attachments_services = _attachments_services()
    if attachments_services is None:
        raise ValueError("保存できません。" + MESSAGE_SCREENSHOT_NO_PART)
    if len(files) > SCREENSHOT_MAX_COUNT:
        raise ValueError("保存できません。" + MESSAGE_SCREENSHOT_COUNT)
    for file in files:
        name = os.path.basename(getattr(file, "name", "") or "")
        if attachments_services.extension_of(name) not in SCREENSHOT_EXTENSIONS:
            raise ValueError("保存できません。" + MESSAGE_SCREENSHOT_TYPE)
        size = getattr(file, "size", None) or 0
        if size <= 0:
            raise ValueError("保存できません。" + MESSAGE_SCREENSHOT_EMPTY)
        if size > SCREENSHOT_MAX_BYTES:
            raise ValueError("保存できません。" + MESSAGE_SCREENSHOT_SIZE)
        if len(name) > attachments_services.MAX_NAME_LENGTH:
            raise ValueError("保存できません。" + attachments_services.MESSAGE_NAME_TOO_LONG)
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
        raise ValueError("保存できません。フィードバックの値は、項目の名前と値の組で渡してください。")
    unknown = sorted(set(data) - set(FIELD_KEYS))
    if unknown:
        raise ValueError(f"保存できません。知らない項目 ({'・'.join(map(str, unknown))}) があります。フィードバックの列の名前だけを渡してください。")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _checked_value(key, value):
    """1 つの鍵の値を確かめて整える。外れたら日本語の ValueError。"""
    if key == "kind":
        kind = _text(value)
        if kind not in KINDS:
            raise ValueError("保存できません。" + MESSAGE_KIND)
        return kind
    if key == "title":
        title = _text(value)
        if not title:
            raise ValueError("保存できません。" + MESSAGE_TITLE_EMPTY)
        if len(title) > TITLE_MAX_CHARS:
            raise ValueError("保存できません。" + MESSAGE_TITLE_LONG)
        return title
    if key == "problem":
        problem = _text(value)
        if not problem:
            raise ValueError("保存できません。" + MESSAGE_PROBLEM_EMPTY)
        return problem
    if key == "screen":
        screen = _text(value)
        if screen and screen not in screen_catalog():
            raise ValueError(f"保存できません。画面 {screen} は画面の目録にありません。選び直すか、「指定なし」にしてください。")
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

    data は FIELD_KEYS の鍵の辞書 (知らない鍵は ValueError。無い鍵は種類が要望、ほかは空)。
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
        raise ValueError("保存できません。" + MESSAGE_CANNOT_EDIT)
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
        raise ValueError(f"保存できません。{what}にできるのはアカウント管理の担当者だけです。" + access.CONTACT)


def mark_confirmed(feedback, user):
    """下書き → 確認済み。世話役だけ。下書きでなければ ValueError。"""
    _refuse_non_manager(user, "確認済み")
    if feedback.status != "draft":
        raise ValueError(f"保存できません。下書きだけを確認済みにできます (今は {feedback.get_status_display()})。")
    with transaction.atomic():
        feedback.status = "confirmed"
        feedback.save(update_fields=["status", "updated_at"])
        return feedback


def mark_sent(feedback, user, sent_url=""):
    """下書きか確認済み → 送った。世話役だけ。送った物をもう一度は ValueError。

    sent_url は空か、http:// か https:// で始まる 200 字までの字 (GitHub の Issue の URL など。字として持つだけ)。
    直した物も ValueError (直した後に送ったへ戻さない)。
    """
    _refuse_non_manager(user, "送った状態")
    if feedback.status == "sent":
        raise ValueError("送れません。" + MESSAGE_ALREADY_SENT)
    if feedback.status == "fixed":
        raise ValueError("送れません。" + MESSAGE_ALREADY_FIXED)
    url = _text(sent_url)
    if url and (not url.startswith(("http://", "https://")) or len(url) > SENT_URL_MAX_CHARS or any(ch.isspace() for ch in url)):
        raise ValueError("送れません。" + MESSAGE_SENT_URL)
    with transaction.atomic():
        feedback.status = "sent"
        feedback.sent_url = url
        feedback.save(update_fields=["status", "sent_url", "updated_at"])
        return feedback


def mark_fixed(feedback, note, user):
    """直した状態にする (下書き・確認済み・送ったのどれからでも。Issue にせずに直す事もあるため)。世話役だけ。

    note は直した場所と版 (例「版 0.4.0 の受注の入力の画面」。空でよい。前後の空白を外して 200 字まで)。
    直した日時は今にし、知らせた日時は空にする (書いた人が次にログインしたときに 1 回知らせる = signals.py)。
    既に直した物は ValueError。直した後は、書いた人は直せず消せない (can_edit・can_delete は書いた人に下書きだけを許すため)。
    """
    _refuse_non_manager(user, "直した状態")
    return _write_fixed(feedback, note)


def _write_fixed(feedback, note):
    """直した状態を書く (mark_fixed と、置き場の持ち主の取り込み sync_github が呼ぶ。人の確かめは呼び手がする)。"""
    if feedback.status == "fixed":
        raise ValueError("保存できません。" + MESSAGE_ALREADY_FIXED)
    text = _text(note)
    if len(text) > FIXED_NOTE_MAX_CHARS:
        raise ValueError("保存できません。" + MESSAGE_FIXED_NOTE_LONG)
    with transaction.atomic():
        feedback.status = "fixed"
        feedback.fixed_at = timezone.now()
        feedback.fixed_note = text
        feedback.fixed_seen_at = None
        feedback.save(update_fields=["status", "fixed_at", "fixed_note", "fixed_seen_at", "updated_at"])
        return feedback


def unseen_fixed(user) -> list:
    """user が書いたフィードバックのうち、直した日時があり、まだ知らせていない物の並び (直した日時の新しい順)。読むだけ。

    pk の無い利用者 (ログインしていない人) は空の並び。
    """
    user_pk = getattr(user, "pk", None)
    if user_pk is None:
        return []
    rows = Feedback.objects.filter(created_by_id=user_pk, fixed_at__isnull=False, fixed_seen_at__isnull=True)
    return list(rows.order_by("-fixed_at", "-pk"))


def fixed_notice(rows) -> str:
    """ログインのときの知らせの文 (いちばん新しく直した物の題と件数)。空の並びなら空の字。

    題は伏せない (書いた本人にだけ出す字のため)。toast は字だけを出すので link は入れず、行き先を字で書く。
    """
    rows = list(rows)
    if not rows:
        return ""
    more = "ほか " if len(rows) > 1 else ""
    return (
        f"あなたのフィードバックから直しました: 「{rows[0].title}」{more}({len(rows)} 件)。"
        "詳細は左のメニューの下の「フィードバック」を開き、上の「送った件」のタブで見られます。"
    )


def mark_fixed_seen(rows, user) -> int:
    """知らせた日時を今にする (user が書いた行で、まだ空の物だけ)。書いた行の数を返す。

    変えた日時 (updated_at) は変えない (知らせは中身の変更でないため。queryset の update は auto_now を動かさない)。
    """
    user_pk = getattr(user, "pk", None)
    pks = [row.pk for row in rows]
    if user_pk is None or not pks:
        return 0
    with transaction.atomic():
        return Feedback.objects.filter(pk__in=pks, created_by_id=user_pk, fixed_seen_at__isnull=True).update(
            fixed_seen_at=timezone.now()
        )


def _write_dismissed(feedback, note):
    """見送った状態を書く (置き場の持ち主の取り込み sync_github が呼ぶ。人の確かめは呼び手がする)。_write_fixed と同じ形。

    note は見送った理由の場所 (例「GitHub の Issue #7 (閉じた日 2026-11-06。理由 対応しない)」。前後の空白を外して 200 字まで)。
    見送った日時は今にし、知らせた日時は空にする (書いた人が次にログインしたときに 1 回知らせる = signals.py)。
    直る版は空にする (見送りは版を待たない)。直したと見送った物は ValueError。
    訳: 対応しないとして閉じた Issue を「直した」と知らせると、直していない物に「直しました」が出て、皆で育てる輪が信用を失うため、
    直したと別の状態にする。出所 = 本人の決め 11-07 09:0x〜09:4x の 1 と 3 (手元の B 社の通しで not_planned が直したになった)。
    変えてよい条件 = 見送りを書いた人に知らせない形に変えるときは、本人の決めを取り直してから。
    """
    if feedback.status == "fixed":
        raise ValueError("保存できません。" + MESSAGE_ALREADY_FIXED)
    if feedback.status == "dismissed":
        raise ValueError("保存できません。" + MESSAGE_ALREADY_DISMISSED)
    text = _text(note)
    if len(text) > FIXED_NOTE_MAX_CHARS:
        raise ValueError("保存できません。" + MESSAGE_DISMISSED_NOTE_LONG)
    with transaction.atomic():
        feedback.status = "dismissed"
        feedback.dismissed_at = timezone.now()
        feedback.dismissed_note = text
        feedback.dismissed_seen_at = None
        feedback.fix_version = ""
        feedback.save(update_fields=["status", "dismissed_at", "dismissed_note", "dismissed_seen_at", "fix_version", "updated_at"])
        return feedback


def unseen_dismissed(user) -> list:
    """user が書いたフィードバックのうち、見送った日時があり、まだ知らせていない物の並び (見送った日時の新しい順)。読むだけ。

    pk の無い利用者 (ログインしていない人) は空の並び。unseen_fixed と同じ形。
    """
    user_pk = getattr(user, "pk", None)
    if user_pk is None:
        return []
    rows = Feedback.objects.filter(created_by_id=user_pk, dismissed_at__isnull=False, dismissed_seen_at__isnull=True)
    return list(rows.order_by("-dismissed_at", "-pk"))


def dismissed_notice(rows) -> str:
    """ログインのときの見送りの知らせの文 (いちばん新しく見送った物の題と件数)。空の並びなら空の字。

    題は伏せない (書いた本人にだけ出す字のため)。toast は字だけを出すので link は入れず、Issue への行き先を字で書く。
    """
    rows = list(rows)
    if not rows:
        return ""
    more = "ほか " if len(rows) > 1 else ""
    return (
        f"あなたのフィードバックは見送りになりました: 「{rows[0].title}」{more}({len(rows)} 件)。"
        "理由は GitHub の Issue に書かれています (詳細の「送った先」から開けます)。"
    )


def mark_dismissed_seen(rows, user) -> int:
    """見送りを知らせた日時を今にする (user が書いた行で、まだ空の物だけ)。書いた行の数を返す。mark_fixed_seen と同じ形。

    変えた日時 (updated_at) は変えない (知らせは中身の変更でないため。queryset の update は auto_now を動かさない)。
    """
    user_pk = getattr(user, "pk", None)
    pks = [row.pk for row in rows]
    if user_pk is None or not pks:
        return 0
    with transaction.atomic():
        return Feedback.objects.filter(pk__in=pks, created_by_id=user_pk, dismissed_seen_at__isnull=True).update(
            dismissed_seen_at=timezone.now()
        )


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
            raise ValueError("削除できません。" + MESSAGE_ALREADY_DELETED)
        if row.status != "draft":
            raise ValueError("削除できません。" + MESSAGE_DELETE_DRAFT_ONLY.format(status=row.get_status_display()))
        if not can_edit(row, user):
            raise ValueError("削除できません。" + MESSAGE_CANNOT_DELETE)
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


#: Issue の文の本文の見出しと、見出しの下に入れる欄 (CONTRIBUTING.md の Issue の型の見出しと並び。決め 11-04 14:4x の 7)。
#: 不具合と決まりの反例は、その型の見出しのうち欄で埋まる物だけ (ブラウザ・重さ・選べる設定の値などは世話役が足す)。
ISSUE_SECTIONS = {
    "bug": (("再現の手順", ("situation",)), ("期待した動き", ("wish",)), ("実際の動き", ("problem",))),
    "counter": (("操作の手順", ("situation",)), ("止まるはずの決まりの番号", ("wish",)), ("実際に起きた事", ("problem",))),
}
#: ほかの種類は要望の見出し (決まりの提案と業務の流れの違いは、CONTRIBUTING が箇条の見出しを持たない)。
#: 困りごとと場面は「困っている場面」に空の行で結ぶ。「今はどうしているか」は欄が無いので出さない (世話役が足す)。
REQUEST_SECTIONS = (("困っている場面", ("problem", "situation")), ("欲しい動き", ("wish",)))


def export_markdown(feedback) -> str:
    """GitHub の Issue に写す文 (題・種類・画面・影響する画面・作品の版・書いた日と、種類ごとの見出しの本文)。

    本文の見出しは CONTRIBUTING.md の Issue の型にそろえる (ISSUE_SECTIONS・REQUEST_SECTIONS。困りごと・場面・望む形の欄を入れる)。
    取引先の名前と金額らしい数は伏せる。会話の記録・書いた人・会社名は入れない。空の節は出さない。
    画面の絵 (画像の添付ファイル) が 1 枚以上なら、末尾に枚数とファイル名 (伏せ字つき) の 1 行。絵そのものは入れない (世話役が Issue に手で付ける)。0 枚なら今までと同じ文。
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
    for heading, keys in ISSUE_SECTIONS.get(feedback.kind, REQUEST_SECTIONS):
        # 字のある欄だけを空の行で結ぶ (字の無い見出しは出さない)
        body = "\n\n".join(text for text in (_mask(getattr(feedback, key), names).strip() for key in keys) if text)
        if body:
            lines.extend(["", f"## {heading}", body])
    shots = screenshots_of(feedback)
    if shots:
        files = "・".join(_mask(row.original_name, names).strip() for row in shots)
        lines.extend(["", f"画面の絵: {len(shots)} 枚 ({files}。絵は世話役が Issue に手で付ける)"])
    return "\n".join(lines) + "\n"


# ---- GitHub (依頼書 N185。決め 11-06 21:2x) ----

#: Issue に付ける印 (協力者でない口座が付けた印は、GitHub が黙って外す)
GITHUB_LABEL = "from-seizou"
#: 皆の Issue を控える秒 (5 分)
ISSUES_CACHE_SECONDS = 5 * 60
#: 皆の Issue を探したときに返す件数の上限
ISSUES_SHOWN = 20
#: 送った先の URL が GitHub の Issue の形か (owner・name・番号)
_ISSUE_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([0-9]+)")

MESSAGE_GITHUB_NOT_CONNECTED = "GitHub とまだつないでいません。フィードバックの一覧の「GitHub の設定」から、トークンを置いてください。"
MESSAGE_GITHUB_CONFIRM_FIRST = "先に「確認済みにする」を押してください。"
MESSAGE_GITHUB_NO_CLIENT_ID = (
    "保存できません。GitHub の OAuth App の Client ID が入っていません。"
    "下の手順でトークンを貼るか、置き場の .env に SEIZOU_GITHUB_CLIENT_ID を書いてください。"
)
#: GitHub でログインしてつなぐ途中の控えの session の鍵 (依頼書 N320)
DEVICE_KEY = "github_device"
#: slow_down の返事に間隔が無いときに足す秒 (GitHub の Device Flow の決まり)
DEVICE_SLOW_DOWN_SECONDS = 5


def _refuse_github(user, what, *, sending=False) -> None:
    """GitHub の口は世話役だけ (外は日本語の ValueError)。what は「GitHub とつなぐ」のような動きの字。

    文の頭は、送る口 (sending) なら「送れません。」、ほかは「保存できません。」(断りの文の型。tests/test_wording.py)。
    """
    if can_manage(user):
        return
    reason = f"{what}のは、アカウント管理の担当者だけです。" + access.CONTACT
    if sending:
        raise ValueError("送れません。" + reason)
    raise ValueError("保存できません。" + reason)


def github_status() -> dict:
    """GitHub とつないだ状態 {"connected": トークンがあるか, "login": 口座の名前, "repo": 送り先}。読むだけ。トークンの値は返さない。

    送り先は .env の SEIZOU_GITHUB_ISSUES_REPO、無ければ既定 (github.DEFAULT_REPO)。
    """
    return {
        "connected": bool(github.read_env_value(github.TOKEN_ENV)),
        "login": github.read_env_value(github.LOGIN_ENV),
        "repo": github.read_env_value(github.REPO_ENV) or github.DEFAULT_REPO,
    }


@sensitive_variables("token")  # 開発の設定 (DEBUG) の誤りの画面に、トークンの値を出さない
def _store_github(token, repo) -> dict:
    """トークンを .env に置く (トークンを貼る形とデバイスの認証の共通)。返す物は {"login": 口座の名前, "repo": 送り先}。

    形を見て → 口座の名前を GitHub に聞き (HTTP。atomic の外。表には書かない) → .env に 3 行 (トークン・送り先・口座の名前) を書く。
    失敗は github.GithubError (呼び手が日本語の ValueError か画面の文にする)。repo は呼び手が check_repo を通した物。
    """
    token = github.check_token(token)
    login = github.whoami(token)
    github.write_env_value(github.TOKEN_ENV, token)
    github.write_env_value(github.REPO_ENV, repo)
    github.write_env_value(github.LOGIN_ENV, login)
    return {"login": login, "repo": repo}


@sensitive_variables("data", "token")  # 開発の設定 (DEBUG) の誤りの画面に、トークンの値を出さない
def connect_github(data, user) -> dict:
    """GitHub とつなぐ。世話役だけ。data = {"token": トークン, "repo": 送り先 (owner/name。空なら既定)}。

    形を見て → 口座の名前を GitHub に聞き (HTTP。atomic の外。表には書かない) → .env に 3 行 (トークン・送り先・口座の名前) を書く。
    返す物は {"login": 口座の名前, "repo": 送り先}。失敗は日本語の ValueError (画面の土台が赤の知らせにする)。
    トークンの値を、返す物・文・記録に入れない。
    """
    _refuse_github(user, "GitHub とつなぐ")
    if not isinstance(data, dict):
        raise ValueError("保存できません。トークンと送り先を、項目の名前と値の組で渡してください。")
    try:
        token = github.check_token(data.get("token"))
        repo = github.check_repo(_text(data.get("repo")) or github.DEFAULT_REPO)
        return _store_github(token, repo)
    except github.GithubError as exc:
        raise ValueError("保存できません。" + f"GitHub とつなげませんでした。{exc}") from None


# ---- GitHub でログインしてつなぐ (OAuth のデバイスの認証。依頼書 N320) ----


def _device_raw(session):
    """session の待ちの控え (形の合う辞書だけ。合わなければ None)。"""
    raw = session.get(DEVICE_KEY) if session is not None else None
    if not isinstance(raw, dict):
        return None
    texts = ("device_code", "user_code", "verification_uri", "repo")
    numbers = ("interval", "expires_at")
    if not all(isinstance(raw.get(key), str) and raw.get(key) for key in texts):
        return None
    if not all(isinstance(raw.get(key), (int, float)) and not isinstance(raw.get(key), bool) for key in numbers):
        return None
    return raw


def _drop_device(session) -> None:
    """session から待ちの控えを消す。"""
    if session is not None and DEVICE_KEY in session:
        session.pop(DEVICE_KEY, None)
        session.modified = True


@sensitive_variables("answer")  # 開発の設定 (DEBUG) の誤りの画面に、device_code の値を出さない
def start_github_device(session, data, user) -> dict:
    """GitHub でログインしてつなぐを始める。世話役だけ。data = {"repo": 送り先 (owner/name。空なら既定), "private": 真偽}。

    GitHub から 8 文字の符号をもらい (HTTP。atomic の外。表には書かない)、待ちの控えを利用者の session に置く。
    返す物は {"user_code", "verification_uri", "interval", "expires_at"} (device_code は返さない)。失敗は日本語の ValueError。
    権限は送り先が非公開なら repo、公開なら public_repo (トークンを貼る形の手順と同じ)。

    訳: Client ID の無い置き場では HTTP を呼ばずに断る。GitHub のデバイスの認証は OAuth App の Client ID が無いと始められないため
    (出所 = GitHub の Device Flow の仕様。依頼書 N320)。変えてよい条件 = 作品が別の認証の形 (中継のサーバーなど) を持ったとき。
    """
    _refuse_github(user, "GitHub とつなぐ")
    if not isinstance(data, dict):
        raise ValueError("保存できません。送り先と権限を、項目の名前と値の組で渡してください。")
    client = github.client_id()
    if not client:
        raise ValueError(MESSAGE_GITHUB_NO_CLIENT_ID)
    scope = "repo" if data.get("private") else "public_repo"
    try:
        repo = github.check_repo(_text(data.get("repo")) or github.DEFAULT_REPO)
        answer = github.device_code(client, scope)
    except github.GithubError as exc:
        raise ValueError("保存できません。GitHub とつなげませんでした。" + str(exc)) from None
    expires_at = time.time() + answer["expires_in"]
    session[DEVICE_KEY] = {
        "device_code": answer["device_code"],
        "user_code": answer["user_code"],
        "verification_uri": answer["verification_uri"],
        "interval": answer["interval"],
        "expires_at": expires_at,
        "repo": repo,
        "scope": scope,
    }
    session.modified = True
    return {
        "user_code": answer["user_code"],
        "verification_uri": answer["verification_uri"],
        "interval": answer["interval"],
        "expires_at": expires_at,
    }


def github_device_state(session) -> dict | None:
    """GitHub でログインしてつなぐの待ちの状態。待ちが無ければ None。期限を過ぎた控えは消して None。

    返す物は {"user_code", "verification_uri", "interval", "expires_at", "repo"} (device_code は返さない)。HTTP は呼ばない。
    """
    raw = _device_raw(session)
    if raw is None:
        return None
    if raw["expires_at"] <= time.time():
        _drop_device(session)
        return None
    return {key: raw[key] for key in ("user_code", "verification_uri", "interval", "expires_at", "repo")}


@sensitive_variables("token", "result")  # 開発の設定 (DEBUG) の誤りの画面に、トークンの値を出さない
def poll_github_device(session, user) -> dict:
    """GitHub でログインしてつなぐの承認を 1 回聞く (待ちの札が interval 秒ごとに呼ぶ)。

    返す物は {"state": "pending"} / {"state": "connected", "login", "repo"} / {"state": "expired"} / {"state": "denied"} /
    {"state": "none"} (待ちが無い) / {"state": "error", "message": 日本語の文}。例外を外に投げない。
    世話役でなければ HTTP を呼ばずに error。承認が済んだら _store_github で .env に書き、待ちの控えを消す。
    slow_down は控えの間隔を返事の値 (無ければ 5 秒足す) にして pending。トークンの値を返す物と文に入れない。
    """
    try:
        _refuse_github(user, "GitHub とつなぐ")
    except ValueError as exc:
        return {"state": "error", "message": str(exc)}
    raw = _device_raw(session)
    if raw is None:
        return {"state": "none"}
    if raw["expires_at"] <= time.time():
        _drop_device(session)
        return {"state": "expired"}
    try:
        result = github.device_token(github.client_id(), raw["device_code"])
    except github.GithubError as exc:
        _drop_device(session)
        return {"state": "error", "message": "保存できません。GitHub とつなげませんでした。" + str(exc)}
    state = result["state"]
    if state == "pending":
        return {"state": "pending"}
    if state == "slow_down":
        raw["interval"] = result.get("interval") or raw["interval"] + DEVICE_SLOW_DOWN_SECONDS
        session[DEVICE_KEY] = raw
        session.modified = True
        return {"state": "pending"}
    _drop_device(session)
    if state in ("expired", "denied"):
        return {"state": state}
    token = result["token"]
    try:
        stored = _store_github(token, raw["repo"])
    except github.GithubError as exc:
        return {"state": "error", "message": "保存できません。GitHub とつなげませんでした。" + str(exc)}
    return {"state": "connected", "login": stored["login"], "repo": stored["repo"]}


def cancel_github_device(session, user) -> None:
    """GitHub でログインしてつなぐの待ちをやめる (session の控えを消す)。世話役だけ。GitHub には何も送らない。"""
    _refuse_github(user, "GitHub とつなぐ")
    _drop_device(session)


def disconnect_github(user) -> None:
    """GitHub から外す。世話役だけ。.env と os.environ から 3 つの行 (トークン・送り先・口座の名前) を消す。"""
    _refuse_github(user, "GitHub から外す")
    try:
        for key in (github.TOKEN_ENV, github.REPO_ENV, github.LOGIN_ENV):
            github.remove_env_value(key)
    except github.GithubError as exc:
        raise ValueError("保存できません。" + f"GitHub から外せませんでした。{exc}") from None


MESSAGE_GITHUB_TEXT_EMPTY = "Issue の文が空です。1 行目に「# 題」、その下に本文を書いてください。"
MESSAGE_GITHUB_TEXT_NO_TITLE = "Issue の文の 1 行目に、題を「# 題」の形で書いてください。"


def send_to_github(feedback, user, text=None) -> str:
    """確認済みの下書きを GitHub の Issue にし、送ったにする。世話役だけ。作った Issue の URL を返す。

    題は Issue の文の 1 行目の「# 」を外した字、本文は Issue の文の全部、印は from-seizou と種類の表示。
    Issue の文は、text が None なら export_markdown (伏せ字の入った文)、字なら世話役が詳細の欄で直した文 (前後の空白を外す。依頼書 N323)。
    確認済みでない・つないでいない・直した文が空か題が無いときは、HTTP を呼ばずに ValueError。GitHub が断ったら ValueError で、状態は変えない。
    HTTP は atomic の外 (R17)。送った後に mark_sent (atomic) で URL を残す。
    訳: 世話役の直した文は、もう一度伏せずにそのまま送る。伏せ字の初めの形 (mask_text・_MONEY) は単価のように外へ出したくない数を
    伏せたままにしておくための物で、「1円ずれる」のように伏せると不具合の要点が読めなくなる数だけを、世話役が判断して戻すため。
    出所 = 本人の決め 11-07 09:0x〜10:1x (11-07 朝の通しで「1円」が伏せ字になった)。変えてよい条件 = 送る前の確かめの形を本人の決めで変えたとき。
    """
    _refuse_github(user, "GitHub に送る", sending=True)
    if feedback.status != "confirmed":
        raise ValueError("送れません。" + MESSAGE_GITHUB_CONFIRM_FIRST)
    token = github.read_env_value(github.TOKEN_ENV)
    if not token:
        raise ValueError("送れません。" + MESSAGE_GITHUB_NOT_CONNECTED)
    repo = github_status()["repo"]
    text = export_markdown(feedback) if text is None else _text(text)
    if not text:
        raise ValueError("送れません。" + MESSAGE_GITHUB_TEXT_EMPTY)
    title = text.splitlines()[0].removeprefix("# ").strip()
    if not title:
        raise ValueError("送れません。" + MESSAGE_GITHUB_TEXT_NO_TITLE)
    try:
        issue = github.create_issue(token, repo, title, text, [GITHUB_LABEL, feedback.get_kind_display()])
    except github.GithubError as exc:
        raise ValueError("送れません。" + str(exc)) from None
    try:
        mark_sent(feedback, user, sent_url=issue["html_url"])
    except ValueError as exc:
        raise ValueError(
            "保存できません。"
            + f"GitHub に Issue はできました ({issue['html_url']})。ただ、送ったにできませんでした ({exc})。"
            + "「送ったにする」の欄に、この URL を貼ってください。"
        ) from None
    return issue["html_url"]


def _closed_on(text) -> str:
    """GitHub の閉じた日時 (ISO 8601) を会社の日付 (YYYY-MM-DD) にする。読めなければ頭の 10 字か「不明」。"""
    raw = str(text or "")
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw[:10] or "不明"
    if timezone.is_naive(moment):
        return moment.date().isoformat()
    return timezone.localdate(moment).isoformat()


def _version_parts(text) -> tuple[int, ...]:
    """版の字を「.」で分け、各段の先頭の数字だけを int にした tuple (数字の無い段は 0。例 "0.1.1rc1" → (0, 1, 1))。"""
    parts = []
    for part in str(text or "").strip().split("."):
        digits = re.match(r"\d*", part.strip()).group()
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def version_at_least(current, needed) -> bool:
    """会社が入れている版 (current) が、直る版 (needed) と同じか新しいか。

    版の字を「.」で分け、各段の先頭の数字だけを int にして tuple で比べる ("0.1.1" と "0.1.1rc1" は同じ段の 1 と 1)。
    current が空 (package が入っていない) なら False。needed が空なら True。
    訳: 会社が直った版を入れる前に「直した」と知らせると、書いた人が画面で直っていないのを見て、知らせが信用を失うため、
    入れている版が直る版に届いたかを見る。出所 = 本人の決め 11-07 09:0x〜09:4x の 4 と 5 (版の 3 つ目の数を直しを外へ出すときに上げる)。
    変えてよい条件 = 版の番号の決まり (GOVERNANCE.md の世話役の節) を変えるときに、同じ決めに合わせて変える。
    """
    if not str(needed or "").strip():
        return True
    if not str(current or "").strip():
        return False
    return _version_parts(current) >= _version_parts(needed)


#: 閉じた理由のうち「見送った」にする物 → 直した場所の字に入れる理由の字 (GitHub の state_reason)
DISMISSED_REASONS = {"not_planned": "対応しない", "duplicate": "重複"}


def _save_fix_version(feedback, version) -> None:
    """直る版を書く (sync_github の版待ちの件。状態は送ったのまま。知らせない)。"""
    with transaction.atomic():
        feedback.fix_version = version
        feedback.save(update_fields=["fix_version", "updated_at"])


def sync_github(user=None) -> dict:
    """送った物の GitHub の Issue を 1 件ずつ読み、閉じた物を閉じた理由で分ける (依頼書 N322。決め 11-07 09:0x〜09:4x)。

    返す物は {"見た", "直した", "見送った", "版待ち", "版の印なし", "読めなかった"} の件数 (鍵の順もこれ)。
    見るのは、状態が送ったで、送った先が https://github.com/<owner>/<name>/issues/<番号> の形の物だけ (ほかの URL は飛ばす)。
    閉じた物の分け方 (理由は GitHub の state_reason):
    - not_planned (対応しない)・duplicate (重複) → 見送った。理由の場所は「GitHub の Issue #<番号> (閉じた日 YYYY-MM-DD。理由 対応しない)」。
      版を待たない。
    - completed でマイルストーン (直る版) あり → 会社が入れている版 (manual.package_version) が同じか新しければ直した
      (直した場所は「GitHub の Issue #<番号> (閉じた日 YYYY-MM-DD。直る版 0.1.1)」)。古ければ送ったのまま直る版を残し、版待ちに数える (知らせない)。
    - completed でマイルストーン無し → 送ったのまま版の印なしに数える (自動では直したにしない。世話役が版を入れた後に手で「直したと記録する」)。
    - 理由が空 (古い Issue と理由の無い閉じ方) と知らない字 → 今までどおり直した (「GitHub の Issue #<番号> (閉じた日 YYYY-MM-DD)」)。
    読めなかった物は数えて飛ばす (止めない)。HTTP は 1 件ずつ atomic の外 (R17)。トークンがあれば使う (無くても公開のリポジトリは読める)。
    user は画面の口なら request.user (世話役でなければ ValueError)。None は置き場の持ち主が打つ管理の命令 (seizou_feedback_sync) だけ。
    訳: 対応しないとして閉じた物と、直った版がまだ会社に入っていない物に「直しました」と知らせないため。
    出所 = 本人の決め 11-07 09:0x〜09:4x の 1・2・4。変えてよい条件 = 閉じた理由の分け方か版の決まりを、本人の決めで変えたとき。
    返事 (依頼書 N324。決め 11-07 09:1x〜09:4x): 最後に、送った・直した・見送ったの件の Issue の返事を読み (_sync_replies)、
    新しく表に入れた数を "返事" の鍵 (鍵の順は最後) にする。返事を読めなかった件も読めなかったに数える。
    """
    if user is not None:
        _refuse_github(user, "GitHub の状態を取り込む")
    token = github.read_env_value(github.TOKEN_ENV)
    counts = {"見た": 0, "直した": 0, "見送った": 0, "版待ち": 0, "版の印なし": 0, "読めなかった": 0}
    for row in list(Feedback.objects.filter(status="sent").order_by("pk")):
        match = _ISSUE_URL.fullmatch(row.sent_url or "")
        if match is None:
            continue
        owner, name, number = match.groups()
        counts["見た"] += 1
        try:
            state = github.issue_state(f"{owner}/{name}", int(number), token=token)
        except github.GithubError:
            counts["読めなかった"] += 1
            continue
        if state["state"] != "closed":
            continue
        closed = f"GitHub の Issue #{number} (閉じた日 {_closed_on(state['closed_at'])}"
        reason = state.get("state_reason") or ""
        try:
            if reason in DISMISSED_REASONS:
                _write_dismissed(row, f"{closed}。理由 {DISMISSED_REASONS[reason]})")
                counts["見送った"] += 1
                continue
            if reason == "completed":
                milestone = _text(state.get("milestone"))
                if not milestone:
                    counts["版の印なし"] += 1
                    continue
                if not version_at_least(manual.package_version(), milestone):
                    _save_fix_version(row, milestone[:FIX_VERSION_MAX_CHARS])
                    counts["版待ち"] += 1
                    continue
                _write_fixed(row, f"{closed}。直る版 {milestone})")
            else:
                _write_fixed(row, f"{closed})")
        except ValueError:
            counts["読めなかった"] += 1
            continue
        counts["直した"] += 1
    counts["返事"] = _sync_replies(token, counts)
    return counts


def _issues_cache_key(repo) -> str:
    return f"seizou-feedback-github-issues:{repo}"


def search_issues(words) -> dict:
    """送り先のリポジトリの Issue を言葉で探す。読むだけ (トークンは無くてよい。あれば使う)。

    返す物は {"issues": 並び, "error": 字, "repo": 送り先}。並びは、空白で分けた言葉の全部を題か本文 (先頭 300 字) に含む物を、
    更新の新しい順に ISSUES_SHOWN 件まで (言葉が空なら新しい順に ISSUES_SHOWN 件)。英字の大小は見ない。
    GitHub の返事は送り先ごとに django.core.cache に 5 分だけ控える (読めた時だけ)。読めなければ空の並びと日本語の error (止めない)。
    """
    repo = github_status()["repo"]
    key = _issues_cache_key(repo)
    rows = cache.get(key)
    if rows is None:
        try:
            rows = github.list_issues(repo, token=github.read_env_value(github.TOKEN_ENV))
        except github.GithubError:
            return {
                "issues": [],
                "error": f"GitHub の Issue を読めませんでした (送り先 {repo})。少し待ってから、もう一度「探す」を押してください。",
                "repo": repo,
            }
        cache.set(key, rows, ISSUES_CACHE_SECONDS)
    terms = [word.casefold() for word in str(words or "").split()]
    found = [row for row in rows if all(term in f"{row['title']}\n{row['body']}".casefold() for term in terms)]
    found.sort(key=lambda row: row["updated_at"], reverse=True)
    return {"issues": found[:ISSUES_SHOWN], "error": "", "repo": repo}


# ---- タブの帯の件数 (依頼書 N321) ----


def visible_count(user) -> int:
    """user に見えるフィードバックの件数 (送る画面と一覧の上のタブの帯の「送った件」の札)。読むだけ。

    絞りは一覧の画面 (views._visible_to) と同じ: 世話役 (can_manage) には全部、ほかの人には自分の書いた分だけ。
    pk の無い利用者 (ログインしていない人) は 0。
    """
    user_pk = getattr(user, "pk", None)
    if user_pk is None:
        return 0
    if can_manage(user):
        return Feedback.objects.count()
    return Feedback.objects.filter(created_by_id=user_pk).count()


# ---- Issue の返事 (依頼書 N324。決め 11-07 09:1x〜09:4x) ----
#
# 世話役 (作者) が Issue に書いた返事を、取り込みで読んで表 (FeedbackReply) に残し、送った件の詳細に出す。
# 書いた人には、ログインの画面からログインしたときに 1 回だけ知らせる (直したと同じ形。signals.py)。
# 会社の口座 (.env の SEIZOU_GITHUB_LOGIN) が書いた返事は表に残すが知らせない。
# 書いた人が詳細で書いた返事は下書きで残し、世話役が伏せ字を直してから会社のトークンで Issue に送る (送る前の確認は Issue と同じ)。
# HTTP は transaction.atomic の外 (R17)。

#: 返事を読む件の状態 (送った・直した・見送った)
REPLY_STATUSES = ("sent", "fixed", "dismissed")


def company_login() -> str:
    """会社の口座の名前 (.env の SEIZOU_GITHUB_LOGIN。つないでいなければ空の字)。読むだけ。"""
    return github.read_env_value(github.LOGIN_ENV)


def _github_time(text):
    """GitHub の日時 (ISO 8601。例 2026-11-06T03:00:00Z) を aware な datetime にする。読めなければ None。"""
    raw = str(text or "")
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timezone.is_naive(moment):
        return moment.replace(tzinfo=UTC)
    return moment


def import_replies(row, repo, number, token="") -> int:
    """件 (row) の Issue の返事を GitHub から読み、表に無い返事を足す。新しく入れた数を返す。

    返事の id (github_id) が表にある物は飛ばす (前の取り込みで読んだ物と、会社から送った返事)。
    1 件 = login・本文・GitHub に出た日時 (posted_at。送った日時 sent_at も同じ。読めない日時は今)。
    会社の口座 (company_login) が書いた返事は、知らせた日時 (seen_at) を今にして知らせない。ほかは空 (次のログインで知らせる)。
    読めなければ github.GithubError (呼び手が数える)。HTTP は atomic の外 (R17)。表に書くのは後の atomic の中だけ。
    """
    comments = github.list_comments(repo, number, token=token)
    if not comments:
        return 0
    known = set(FeedbackReply.objects.filter(github_id__in=[item["id"] for item in comments]).values_list("github_id", flat=True))
    own = company_login()
    now = timezone.now()
    new = []
    for item in comments:
        if item["id"] in known:
            continue
        known.add(item["id"])
        posted = _github_time(item["created_at"]) or now
        new.append(
            FeedbackReply(
                feedback=row,
                github_id=item["id"],
                login=item["login"][: FeedbackReply._meta.get_field("login").max_length],
                body=item["body"],
                posted_at=posted,
                sent_at=posted,
                seen_at=now if own and item["login"] == own else None,
            )
        )
    if not new:
        return 0
    with transaction.atomic():
        FeedbackReply.objects.bulk_create(new)
    return len(new)


def _sync_replies(token, counts) -> int:
    """sync_github の最後に呼ぶ。送った・直した・見送ったの件で、送った先が Issue の形の物の返事を 1 件ずつ読む。

    新しく入れた返事の数を返す。読めなかった件は counts["読めなかった"] に足して飛ばす (止めない)。
    """
    total = 0
    for row in list(Feedback.objects.filter(status__in=REPLY_STATUSES).order_by("pk")):
        match = _ISSUE_URL.fullmatch(row.sent_url or "")
        if match is None:
            continue
        owner, name, number = match.groups()
        try:
            total += import_replies(row, f"{owner}/{name}", int(number), token)
        except github.GithubError:
            counts["読めなかった"] += 1
    return total


def unseen_replies(user) -> list:
    """user が書いた件の返事のうち、GitHub から来た (書いた人が空) まだ知らせていない物の並び (GitHub に出た日時の新しい順)。読むだけ。

    pk の無い利用者 (ログインしていない人) は空の並び。
    """
    user_pk = getattr(user, "pk", None)
    if user_pk is None:
        return []
    rows = FeedbackReply.objects.filter(feedback__created_by_id=user_pk, seen_at__isnull=True, created_by__isnull=True)
    return list(rows.select_related("feedback").order_by("-posted_at", "-pk"))


def reply_notice(rows) -> str:
    """ログインのときの返事の知らせの文 (いちばん新しい返事の件の題と返事の数)。空の並びなら空の字。

    「ほか」は返事が 2 件以上の件にまたがるときに付ける。題は伏せない (書いた本人にだけ出す字のため)。
    toast は字だけを出すので link は入れず、行き先を字で書く。
    """
    rows = list(rows)
    if not rows:
        return ""
    more = "ほか " if len({row.feedback_id for row in rows}) > 1 else ""
    return (
        f"あなたのフィードバックに返事が届きました: 「{rows[0].feedback.title}」{more}({len(rows)} 件)。"
        "左のメニューの下の「フィードバック」の「送った件」から開いて読めます。"
    )


def mark_replies_seen(rows, user) -> int:
    """返事を知らせた日時を今にする (user が書いた件の返事で、まだ空の物だけ)。書いた行の数を返す。"""
    user_pk = getattr(user, "pk", None)
    pks = [row.pk for row in rows]
    if user_pk is None or not pks:
        return 0
    with transaction.atomic():
        return FeedbackReply.objects.filter(pk__in=pks, feedback__created_by_id=user_pk, seen_at__isnull=True).update(
            seen_at=timezone.now()
        )


#: 返事の字数の上限
REPLY_MAX_CHARS = 2000
MESSAGE_REPLY_EMPTY = "返事を入力してください。"
MESSAGE_REPLY_LONG = f"返事は {REPLY_MAX_CHARS} 字までです。短くしてください。"
MESSAGE_CANNOT_REPLY = "この件に返事を書けるのは、書いた人かアカウント管理の担当者だけです。"
MESSAGE_REPLY_NOT_SENT = "先に GitHub に送るか、送ったにしてください。"
MESSAGE_REPLY_ALREADY_SENT = "この返事は送った状態です。"
MESSAGE_REPLY_NOT_ISSUE = "この件は GitHub の Issue に送った物ではありません。"


def can_reply(feedback, user) -> bool:
    """返事を書けるか。件を書いた人か世話役。"""
    if can_manage(user):
        return True
    user_pk = getattr(user, "pk", None)
    return user_pk is not None and feedback.created_by_id == user_pk


def _reply_body(text) -> tuple[str, str]:
    """返事の文の前後の空白を外して確かめる。返す物は (本文, 断りの理由。通れば空の字)。

    断りの文の頭 (「保存できません。」か「送れません。」) は呼び手が付けて raise する (tests/test_wording.py が頭を見るため)。
    """
    body = _text(text)
    if not body:
        return body, MESSAGE_REPLY_EMPTY
    if len(body) > REPLY_MAX_CHARS:
        return body, MESSAGE_REPLY_LONG
    return body, ""


def add_reply(feedback, text, user):
    """書いた人か世話役が、送った件に返事の下書きを 1 件書く。作った行を返す。

    状態が送った・直した・見送ったの件だけ (下書きと確認済みはまだ Issue が無いので断る)。文は前後の空白を外して 2000 字まで。
    書いた人は user、知らせた日時は今 (会社の中で書いた物は知らせない)。GitHub には送らない (送るのは世話役の send_reply)。
    訳: 返事は書いた人の字のまま公開の Issue に出るので、Issue の文と同じく世話役の確認 (伏せ字) を通してから送るため、
    ここでは下書きとして残すだけにする。出所 = 本人の決め 11-07 09:1x〜09:4x (指示書2 の D)。
    変えてよい条件 = 書いた人が直接 Issue に返事を書ける形に変えるときは、本人の決めを取り直してから。
    """
    if not can_reply(feedback, user):
        raise ValueError("保存できません。" + MESSAGE_CANNOT_REPLY)
    if feedback.status not in REPLY_STATUSES:
        raise ValueError("送れません。" + MESSAGE_REPLY_NOT_SENT)
    body, problem = _reply_body(text)
    if problem:
        raise ValueError("保存できません。" + problem)
    with transaction.atomic():
        return FeedbackReply.objects.create(feedback=feedback, body=body, created_by=_author(user), seen_at=timezone.now())


def reply_draft_text(reply) -> str:
    """世話役が直す前の返事の初めの字 (取引先の名前と金額らしい数を伏せた本文)。読むだけ。"""
    return mask_text(reply.body)


def send_reply(reply, text, user) -> str:
    """世話役が直した返事の文を、会社のトークンで件の Issue に書き、送ったにする。Issue の返事の URL を返す。

    世話役だけ。送った返事・つないでいない会社・送った先が Issue の形でない件は、HTTP を呼ばずに断る。
    文は世話役が直した物 (前後の空白を外して 2000 字まで)。GitHub が断ったら ValueError で、返事は下書きのまま。
    HTTP は atomic の外 (R17)。送った後に atomic で本文・送った日時・GitHub の id・口座・GitHub に出た日時を書く。
    訳: 書いた人の返事は公開の Issue に出るので、取引先の名前や金額が出ないよう、世話役が伏せ字を確かめた文だけを送るため。
    出所 = 本人の決め 11-07 09:1x〜09:4x (指示書2 の D。Issue を送るときと同じ確認)。
    変えてよい条件 = 送る前の確認の形 (N323 の「送る前に直せる」) を本人の決めで変えたとき、同じ形に合わせる。
    """
    _refuse_github(user, "返事を GitHub に送る", sending=True)
    if reply.sent_at is not None:
        raise ValueError("送れません。" + MESSAGE_REPLY_ALREADY_SENT)
    token = github.read_env_value(github.TOKEN_ENV)
    if not token:
        raise ValueError("送れません。" + MESSAGE_GITHUB_NOT_CONNECTED)
    match = _ISSUE_URL.fullmatch(reply.feedback.sent_url or "")
    if match is None:
        raise ValueError("送れません。" + MESSAGE_REPLY_NOT_ISSUE)
    body, problem = _reply_body(text)
    if problem:
        raise ValueError("送れません。" + problem)
    owner, name, number = match.groups()
    try:
        answer = github.create_comment(token, f"{owner}/{name}", int(number), body)
    except github.GithubError as exc:
        raise ValueError("送れません。" + str(exc)) from None
    now = timezone.now()
    with transaction.atomic():
        reply.body = body
        reply.sent_at = now
        reply.github_id = answer["id"]
        reply.login = (company_login() or answer["login"])[: FeedbackReply._meta.get_field("login").max_length]
        reply.posted_at = _github_time(answer["created_at"]) or now
        reply.save(update_fields=["body", "sent_at", "github_id", "login", "posted_at"])
    return answer["html_url"]


def mark_reply_sent(reply, user):
    """つないでいない会社で、世話役が伏せ字の文を GitHub に手で写した後に、返事を送ったにする。世話役だけ。

    送った日時だけを今にする (本文と GitHub の id は変えない)。GitHub の id が空のままなので、次の取り込みで同じ返事が
    GitHub から読まれると、別の行で入ることがある (依頼書 N324 の記録に書いた)。送った返事は ValueError。
    """
    _refuse_non_manager(user, "返事を送った状態")
    if reply.sent_at is not None:
        raise ValueError("保存できません。" + MESSAGE_REPLY_ALREADY_SENT)
    with transaction.atomic():
        reply.sent_at = timezone.now()
        reply.save(update_fields=["sent_at"])
        return reply


def replies_of(feedback) -> list:
    """件の返事の並び (GitHub に出た日時の古い順。日時の無い物 = 下書きと手で写した物は後ろで、書いた日時の順)。読むだけ。

    保存前の行は空の並び。書いた人は select_related で一緒に読む (詳細で名前を出すため)。
    """
    if getattr(feedback, "pk", None) is None:
        return []
    rows = FeedbackReply.objects.filter(feedback=feedback).select_related("created_by")
    return list(rows.order_by(F("posted_at").asc(nulls_last=True), "created_at", "pk"))


# ---- うちもと自社の印 (依頼書 N323。決め 11-07 09:0x〜10:1x) ----

#: うちもの件の困りごと (つないだ会社。会社のトークンで 👍 を付けた)
ME_TOO_PROBLEM = "同じ困りごとの Issue #{number} に「うちも」を付けた"
#: うちもの件の困りごと (つないでいない会社。👍 を付けずに下書きで残す)
ME_TOO_DRAFT_PROBLEM = "同じ困りごとの Issue #{number} がある ({url})。GitHub とつないでいないので 👍 は付けていない。世話役がつないだ後に付ける"
#: うちもの件を見分ける困りごとの頭 (ME_TOO_PROBLEM と ME_TOO_DRAFT_PROBLEM の頭)
_ME_TOO_HEAD = "同じ困りごとの Issue #"
#: Issue の本文の種類の行 (export_markdown が書く「- 種類: <表示名>」)
_KIND_LINE = re.compile(r"^- 種類:[ \t]*([^\r\n]*?)[ \t\r]*$", re.M)


def issue_kind(body) -> str:
    """Issue の本文の「- 種類: <表示名>」の行を KIND_CHOICES の表示名で引いた種類の値。行が無いか、知らない表示名なら "common" (要望)。"""
    match = _KIND_LINE.search("" if body is None else str(body))
    labels = {label: value for value, label in Feedback.KIND_CHOICES}
    return labels.get(match.group(1), "common") if match else "common"


def _issue_number(url, repo):
    """URL が送り先 (repo = owner/name) の Issue の形なら番号、ほかは None。owner と name の英字の大小は見ない。"""
    match = _ISSUE_URL.fullmatch(url or "")
    if match is None:
        return None
    owner, name, number = match.groups()
    if f"{owner}/{name}".casefold() != str(repo or "").casefold():
        return None
    return int(number)


def our_issue_marks() -> dict[int, dict]:
    """みんなの Issue の行に付ける自社の印。{Issue の番号: {"sent": 自社が送った件の pk か None, "me_too": うちもの件の pk か None}}。読むだけ。

    送り先 (github_status の repo) の Issue の URL を送った先に持つ件を全部読む (状態は見ない。直した・見送った件も自社が送った物)。
    うちもの件は困りごとの頭 (「同じ困りごとの Issue #」= ME_TOO_PROBLEM の形) で見分け、ほかは自社が送った件。どちらも pk の小さい最初の 1 つ。
    世話役でない人にも返す (印は会社の物)。HTTP は呼ばない。
    """
    repo = github_status()["repo"]
    marks: dict[int, dict] = {}
    rows = Feedback.objects.filter(sent_url__startswith="https://github.com/").order_by("pk").values_list("pk", "sent_url", "problem")
    for pk, url, problem in rows:
        number = _issue_number(url, repo)
        if number is None:
            continue
        mark = marks.setdefault(number, {"sent": None, "me_too": None})
        key = "me_too" if problem.startswith(_ME_TOO_HEAD) else "sent"
        if mark[key] is None:
            mark[key] = pk
    return marks


def _issue_list(repo) -> list:
    """送り先の Issue の全部の並び (search_issues と同じ控え。控えが無ければ GitHub を読んで 5 分控える)。読めなければ github.GithubError。"""
    key = _issues_cache_key(repo)
    rows = cache.get(key)
    if rows is None:
        rows = github.list_issues(repo, token=github.read_env_value(github.TOKEN_ENV))
        cache.set(key, rows, ISSUES_CACHE_SECONDS)
    return rows


def me_too(number, user) -> dict:
    """みんなの Issue の 1 件に「うちも」を付け、押した人の件として残す (ログインした全員)。

    返す物は {"feedback": 作った行, "thumbs_up": 会社のトークンで 👍 を付けたか, "connected": GitHub とつないでいたか}。
    Issue は search_issues と同じ控えの並びから番号で引く (20 件に絞る前の全部)。件は、題が Issue の題、種類が本文の「- 種類:」の行 (issue_kind)、
    困りごとが ME_TOO_PROBLEM、状態が送った、送った先が Issue の URL。つないでいない会社は 👍 を付けず、困りごとを ME_TOO_DRAFT_PROBLEM (URL 入り)、
    状態を下書き、送った先を空にする。断る (日本語の ValueError。件を作らない) のは、ログインしていない・番号が正の整数でない・Issue が送り先に無い・
    同じ人の同じ Issue の件が既にある (ここまでは 👍 を付けない)・GitHub が断ったとき。
    HTTP (github.add_thumbs_up) は atomic の外 (R17)。👍 を付けてから件を作り、付けた後は search_issues の控えを消す (👍 の数を読み直す)。
    訳: GitHub の口の中でこの口だけは世話役でなくても押せる。👍 には文が入らず、取引先の名前や金額が外へ出ないため。
    GitHub の 👍 は 1 つの口座に 1 つなので、会社に同じ Issue の件 (送った物かうちも) が既にあれば GitHub を呼ばず、件だけ残す (会社の 👍 は 1 つ)。
    出所 = 本人の決め 11-07 09:0x〜10:1x (受け渡しの指示書のその 2 と指示書2 の C。👍 は GitHub の口座を持つ人しか押せなかった)。
    変えてよい条件 = 👍 でなく文を送る形 (Issue の返事など) にするときは、世話役だけに戻すかを本人の決めで取り直してから。
    """
    if getattr(user, "pk", None) is None:
        raise ValueError("保存できません。ログインしてから押してください。")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise ValueError("保存できません。Issue の番号は 1 以上の整数です。「探す」で探し直してください。")
    status = github_status()
    repo = status["repo"]
    try:
        rows = _issue_list(repo)
    except github.GithubError:
        raise ValueError(f"保存できません。GitHub の Issue を読めませんでした (送り先 {repo})。少し待ってから、もう一度押してください。") from None
    issue = next((row for row in rows if row.get("number") == number), None)
    url = (issue.get("html_url") or "") if issue is not None else ""
    # URL が送り先の Issue の形でない返事は、見つからない物として扱う (送った先の列と自社の印を、送り先の Issue の URL だけにする)。
    if issue is None or _issue_number(url, repo) != number:
        raise ValueError(f"保存できません。Issue #{number} が送り先に見つかりません。「探す」で探し直してください。")
    draft_problem = ME_TOO_DRAFT_PROBLEM.format(number=number, url=url)
    mine = Feedback.objects.filter(created_by_id=user.pk)
    if mine.filter(sent_url=url).exists() or mine.filter(problem=draft_problem).exists():
        raise ValueError("保存できません。この Issue には、あなたの「うちも」が既にあります。")
    connected = status["connected"]
    thumbs_up = False
    if connected and not Feedback.objects.filter(sent_url=url).exists():
        # ここは atomic の外 (R17)。会社の 👍 がまだ無いときだけ付ける。
        try:
            github.add_thumbs_up(github.read_env_value(github.TOKEN_ENV), repo, number)
        except github.GithubError as exc:
            raise ValueError("送れません。" + str(exc)) from None
        thumbs_up = True
        cache.delete(_issues_cache_key(repo))
    title = _text(issue.get("title"))[:TITLE_MAX_CHARS].strip() or f"Issue #{number}"
    with transaction.atomic():
        row = Feedback.objects.create(
            kind=issue_kind(issue.get("body")),
            title=title,
            problem=ME_TOO_PROBLEM.format(number=number) if connected else draft_problem,
            status="sent" if connected else "draft",
            sent_url=url if connected else "",
            created_by=_author(user),
        )
    return {"feedback": row, "thumbs_up": thumbs_up, "connected": connected}


# ---- みんなの Issue の盤 (依頼書 N325。決め 11-07 09:3x〜10:1x) ----
#
# 「みんなの Issue」のタブ (一覧の ?tab=issues) が、送り先のリポジトリの Issue を 5 つの列に分けて並べる。読むだけ (表に書かない)。
# 列は Issue の状態・閉じた理由・ラベル・マイルストーンから分ける (GitHub の Projects の盤は読まない。名前と並びは作者の盤と同じ)。
# 開いている Issue は全部 (100 件ずつ github.BOARD_PAGES ページまで)、閉じた Issue は閉じてから BOARD_CLOSED_DAYS 日の分だけを読み、
# 送り先ごとに django.core.cache に BOARD_CACHE_SECONDS 秒控える (探す search_issues の控えとは別の鍵)。HTTP は transaction.atomic の外 (R17)。

#: 盤の列 (鍵・表示名・意味の色)。並びは画面の左から。色の名前は core.status_colors の 5 つと同じ字
#: (「直した」は一覧の fixed と同じ success、「見送った」は dismissed と同じ error、「受け付けた」は「まだ」の ghost)
BOARD_COLUMNS = (
    ("inbox", "受け付けた", "ghost"),
    ("planned", "直す予定", "info"),
    ("working", "直している", "warning"),
    ("fixed", "直した", "success"),
    ("dismissed", "見送った", "error"),
)
#: 閉じた Issue を盤に出す日数 (閉じてから)
BOARD_CLOSED_DAYS = 30
#: 「直している」の列に入れる Issue のラベル (作者が GitHub で付ける)
BOARD_WORKING_LABEL = "直している"
#: 盤の控えの秒 (探すの控えと同じ 5 分。鍵は別)
BOARD_CACHE_SECONDS = ISSUES_CACHE_SECONDS
#: 盤の並べ替え (👍 の多い順・更新の新しい順)。初めは top
BOARD_SORTS = ("top", "new")


def board_column(issue) -> str:
    """Issue (github.list_issues の 1 件) を入れる盤の列の鍵 (BOARD_COLUMNS の鍵)。

    閉じた物は、閉じた理由が対応しない・重複 (DISMISSED_REASONS) なら dismissed、ほか (completed・空) は fixed。
    開いている物は、ラベルに BOARD_WORKING_LABEL があれば working、マイルストーンがあれば planned、ほかは inbox。
    訳: 工場の人が GitHub を開かずに、皆の要望がどこまで進んだかを、作者が見る GitHub の盤と同じ言葉で見るため。
    「見送った」の理由は、取り込み (sync_github) が件を見送ったにする理由と同じ 2 つにそろえる。
    出所 = 本人の決め 11-07 09:3x〜10:1x (受け渡しの指示書3。列の分け方は Projects を読まず Issue の情報から)。
    変えてよい条件 = 作者が GitHub の盤の列の名前か「直している」のラベルの名前を変えたとき (BOARD_COLUMNS と BOARD_WORKING_LABEL を一緒に直す)。
    """
    if issue.get("state") == "closed":
        return "dismissed" if issue.get("state_reason") in DISMISSED_REASONS else "fixed"
    if BOARD_WORKING_LABEL in (issue.get("labels") or []):
        return "working"
    if issue.get("milestone"):
        return "planned"
    return "inbox"


def _board_cache_key(repo) -> str:
    return f"seizou-feedback-github-board:{repo}"


def _board_rows(repo, token) -> list[dict]:
    """盤に並べる Issue (github.list_issues の形) の並び。読めなければ github.GithubError。

    開いている Issue を全部 (github.BOARD_PAGES ページまで) と、最近 BOARD_CLOSED_DAYS 日に更新された閉じた Issue (1 ページ) を読む。
    閉じた物は、閉じた日時 (closed_at) が BOARD_CLOSED_DAYS 日より前なら外す (closed_at が空か読めない物は残す。since で最近の物だけを読んでいるため)。
    番号が重なった物は先に読んだ方を残す。送り先ごとに django.core.cache に BOARD_CACHE_SECONDS 秒控える (読めた時だけ)。
    """
    from datetime import timedelta

    key = _board_cache_key(repo)
    rows = cache.get(key)
    if rows is not None:
        return rows
    since = timezone.now().astimezone(UTC) - timedelta(days=BOARD_CLOSED_DAYS)
    opened = github.list_issues(repo, token, state="open", pages=github.BOARD_PAGES)
    closed = github.list_issues(repo, token, state="closed", since=since.strftime("%Y-%m-%dT%H:%M:%SZ"), pages=1)
    rows, seen = [], set()
    for row in [*opened, *closed]:
        if row.get("number") in seen:
            continue
        closed_at = _github_time(row.get("closed_at")) if row.get("state") == "closed" else None
        if closed_at is not None and closed_at < since:
            continue
        seen.add(row.get("number"))
        rows.append(row)
    cache.set(key, rows, BOARD_CACHE_SECONDS)
    return rows


def board_kind(issue) -> str:
    """盤のカードの種類の札の字 (Feedback.KIND_CHOICES の表示名)。

    Issue のラベルに表示名と同じ字があればそれ (ラベルの並びの先の物)、無ければ本文の「- 種類: <表示名>」の行 (export_markdown が書く行)、
    どちらも無いか知らない字なら空の字。種類の値 (bug など) を返す issue_kind (依頼書 N323。「うちも」の件の種類) と違い、画面に出す表示名を返す。
    """
    names = [label for _, label in Feedback.KIND_CHOICES]
    for label in issue.get("labels") or []:
        if label in names:
            return label
    match = _KIND_LINE.search(str(issue.get("body") or ""))
    if match and match.group(1) in names:
        return match.group(1)
    return ""


def days_ago(iso, now=None) -> str:
    """更新の日時 (GitHub の ISO 8601 の字) から今日までの日数の字。「今日」「昨日」「n 日前」(会社の時刻の日付で数え、時刻は見ない)。

    読めない字は空の字。先の日付 (時計のずれ) は「今日」。now は aware な datetime (無ければ今)。
    """
    moment = _github_time(iso)
    if moment is None:
        return ""
    days = (timezone.localdate(now or timezone.now()) - timezone.localdate(moment)).days
    if days <= 0:
        return "今日"
    if days == 1:
        return "昨日"
    return f"{days} 日前"


def _own_mark(mark):
    """our_issue_marks の 1 件 ({"sent", "me_too"}) を盤の自社の印 {"kind": "sent" か "me_too", "pk": 自社の件の pk} にする。

    両方あれば「自社が送った」(sent) を先に見る (一覧の行の印と同じ)。どちらも無ければ None。
    """
    if not mark:
        return None
    if mark.get("sent") is not None:
        return {"kind": "sent", "pk": mark["sent"]}
    if mark.get("me_too") is not None:
        return {"kind": "me_too", "pk": mark["me_too"]}
    return None


def _openable_pks(user, marks) -> set:
    """自社の印の件のうち、user が詳細を開ける件の pk の集まり。読むだけ。

    世話役は全部、ほかの人は自分の書いた件だけ (詳細の画面の絞りと同じ。一覧の tag の feedback_github の openable と同じ見分け方)。
    """
    pks = {pk for mark in marks.values() for pk in mark.values() if pk is not None}
    if not pks or user is None:
        return set()
    if can_manage(user):
        return pks
    if getattr(user, "pk", None) is None:
        return set()
    return set(Feedback.objects.filter(pk__in=pks, created_by_id=user.pk).values_list("pk", flat=True))


def board_issues(words, *, sort="top", user=None) -> dict:
    """みんなの Issue の盤。読むだけ (トークンは無くてよい。あれば使う)。

    返す物は {"columns": 5 つの列 [{"key", "label", "tone", "count", "issues"}] (並びは BOARD_COLUMNS),
    "issues": 全部の列の Issue を 1 つに並べた物 (一覧の形の表が使う), "error": 字, "repo": 送り先, "sort": "top" か "new", "total": 件数}。
    言葉 (空白で分ける) があれば search_issues と同じ比べ方 (全部の言葉を題か本文に含む。英字の大小は見ない) で絞る。
    各 Issue に足す鍵: column (board_column)・column_label と column_tone (列の表示名と意味の色)・kind (board_kind)・
    fix_version (マイルストーンの名前)・days (days_ago)・own (自社の印。_own_mark)・
    detail_url (own があり、user が詳細を開ける件なら自社の件の詳細の道、ほかは空の字)。
    並べ替えは top = 👍 の多い順 (同じなら更新の新しい順)、new = 更新の新しい順 (ほかの値は top)。
    GitHub を読めなければ 5 つの列は空で、error に日本語の文 (止めない。search_issues と同じ形)。
    """
    from django.urls import reverse

    repo = github_status()["repo"]
    sort = sort if sort in BOARD_SORTS else "top"
    try:
        rows = _board_rows(repo, github.read_env_value(github.TOKEN_ENV))
        error = ""
    except github.GithubError:
        rows = []
        error = f"GitHub の Issue を読めませんでした (送り先 {repo})。少し待ってから、もう一度「みんなの Issue」を開いてください。"
    terms = [word.casefold() for word in str(words or "").split()]
    found = [row for row in rows if all(term in f"{row['title']}\n{row['body']}".casefold() for term in terms)]
    marks = our_issue_marks() if found else {}
    openable = _openable_pks(user, marks)
    names = {key: (label, tone) for key, label, tone in BOARD_COLUMNS}
    now = timezone.now()
    issues = []
    for row in found:
        column = board_column(row)
        own = _own_mark(marks.get(row["number"]))
        issues.append(
            {
                **row,
                "column": column,
                "column_label": names[column][0],
                "column_tone": names[column][1],
                "kind": board_kind(row),
                "fix_version": row.get("milestone") or "",
                "days": days_ago(row.get("updated_at"), now),
                "own": own,
                "detail_url": reverse("feedback:feedback_detail", args=[own["pk"]]) if own and own["pk"] in openable else "",
            }
        )
    issues.sort(key=lambda row: row["updated_at"], reverse=True)
    if sort == "top":
        # 並べ替えは安定なので、👍 が同じ物は更新の新しい順のまま残る。
        issues.sort(key=lambda row: row["thumbs_up"], reverse=True)
    columns = []
    for key, label, tone in BOARD_COLUMNS:
        members = [row for row in issues if row["column"] == key]
        columns.append({"key": key, "label": label, "tone": tone, "count": len(members), "issues": members})
    return {"columns": columns, "issues": issues, "error": error, "repo": repo, "sort": sort, "total": len(issues)}
