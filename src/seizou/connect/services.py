"""connect の公開する関数 (01 の 10.4)。ほかの部品と画面は、この関数だけを通してつながりと受信箱を書く。

- 引数の順は 対象 → 値 → user (01 の 10.1)。user は記録のために受ける。記録の列がまだ無いので、今は受けるだけ。
- 鍵の値は受け取らず、表にも持たない。つながりは鍵を入れる環境変数の名前だけを持ち、
  受けるときに os.environ から値を読む。鍵の値を例外の文・ログ・返す物に入れない。
- 署名の確かめは signing.verify をそのまま呼ぶ。ここで書き直さない。
- 受け口は受注を作らない (R10)。受け入れて受注にする関数は、受注の部品ができてから足す。
- 受信した注文に答えた後、send_reply が答えを「注文回答」にして相手の受け口へ返す。答えは 4 つ (受諾・条件付受諾・辞退・保留) で、
  JSON の reply_type に中小企業共通 EDI の注文回答タイプコードの値 (AP・CA・RE・AJ。core.edi_codes) をそのまま持つ。
  受け口は本文の kind で注文と注文回答を見分け、注文回答は元の発注に 1 回だけ当てる。
- 受入の後、send_acceptance が検収を相手へ送り、受け口は kind が acceptance の物を受注の明細に 1 回だけ当てる。
- 出荷の後、send_shipping_notice が出荷の知らせを相手へ送り、受け口は kind が shipping_notice の物を発注の明細に 1 回だけ当てる。
- 受注と発注の 1 件ごとの会話 (C104): post_chat_message が言葉を残して相手へ送り、受け口は kind が chat_message の物を元の注文に 1 回だけ結ぶ。
  会話は中小企業共通 EDI に無い seizou どうしだけの種類。言葉を直す関数と消す関数は作らない (ChatMessage の docstring)。
- 受信した注文の入口は 2 つ (直接 = 受け口 / 読み取り = intake の見張り)。読み取りの行は注文回答と出荷の知らせを相手へ送らない (相手は seizou とつながっていない)。差出人の確認待ち (取引先の空の行) は choose_sender で得意先を選んでから承認する。
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django.db import IntegrityError, transaction
from django.db.models import Q

from seizou.core import edi_codes
from seizou.core.exceptions import RuleViolation
from seizou.masters.services import PRICE_BASIS_LUMP, PRICE_BASIS_UNIT, PRICE_BASIS_VALUES, map_partner_item, resolve_partner_item
from seizou.orders.services import cancel_order, create_order, shipped_qty, update_order

from . import signing
from .models import Acceptance, Connection, Inbox
from .models import ShippingNotice
from .models import _qty_text

logger = logging.getLogger("seizou.connect")

_DIRECTIONS = frozenset(value for value, _label in Connection.DIRECTION_CHOICES)
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")

# 断る理由の日本語 (何が起きて、送る側が何をすればよいか)。
MESSAGE_MISSING_HEADER = (
    "署名の見出しがありません。"
    "webhook-id・webhook-timestamp・webhook-signature の 3 つを付けて送ってください。"
)
MESSAGE_INVALID_TIMESTAMP = "時刻の見出しが読めません。UNIX 秒の整数で送ってください。"
MESSAGE_OUT_OF_TOLERANCE = "送った時刻が 5 分の幅の外です。時計を合わせて送り直してください。"
MESSAGE_SIGNATURE_MISMATCH = (
    "登録した取引先の署名と合いません。つながりの登録と鍵を確かめてください。"
)
MESSAGE_INVALID_ID = "番号 (webhook-id) が uuid の形ではありません。"
MESSAGE_INVALID_BODY = "本文が JSON の形ではありません。"
#: 知らない種類の通知 ({kind} は本文の kind の字を 40 字で切った物。字でなければ決まった字)
MESSAGE_UNKNOWN_KIND = "知らない種類の通知です: {kind}"
#: 明細の無い注文 (送る側へ返す文。承認の時の MESSAGE_NO_LINES とは読み手が違う)
MESSAGE_RECEIVE_NO_LINES = "明細の無い注文は受けません。"

# 鍵と関係なく決まる誤り。1 つ目の鍵で出たら、ほかの鍵を試さずに断る。
_HEADER_ERRORS = {
    signing.SignatureError.MISSING_HEADER: MESSAGE_MISSING_HEADER,
    signing.SignatureError.INVALID_TIMESTAMP: MESSAGE_INVALID_TIMESTAMP,
    signing.SignatureError.TIMESTAMP_OUT_OF_TOLERANCE: MESSAGE_OUT_OF_TOLERANCE,
}


def _url_is_valid(url):
    """受け口の URL の形を見る。scheme が http か https、ホスト名が空でない、空白が無い、ポートが読める。

    ホスト名の点の有無は問わない (localhost や社内の PC の名前を通す)。
    """
    if not isinstance(url, str) or any(char.isspace() for char in url):
        return False
    try:
        parts = urlsplit(url)
        _ = parts.port  # 数でないポートと範囲の外のポートは ValueError になる
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def _check_connection(partner, url, secret_env, direction, *, exclude_pk=None):
    """つながりの値を確かめる (登録と編集で同じ)。見る順は 向き → 鍵の置き場の名前 → URL → 2 行目。

    送る向きで url が空でなければ、URL の形を確かめる。受ける向きの url は空でよい。
    同じ取引先と向きのつながりが既にあれば断る。exclude_pk は、直すときに自分の行を除くための引数。
    DB の一意の制約は持たない (表を変えないため)。誤りは日本語の文の ValueError。
    """
    if direction not in _DIRECTIONS:
        raise ValueError(
            f"保存できません。向き {direction!r} は使えません。送る (send) か受ける (receive) にしてください。"
        )
    if not isinstance(secret_env, str) or _ENV_NAME.fullmatch(secret_env) is None:
        # 値が渡されたときに文へ出さないよう、受けた文字列は文に入れない。
        raise ValueError(
            "保存できません。鍵の置き場の名前は、環境変数の名前 (英大文字で始まり、英大文字と数字と _ だけ) にしてください。"
            "鍵の値は渡さず、.env に置いてください。"
        )
    if direction == Connection.DIRECTION_SEND and url and not _url_is_valid(url):
        raise ValueError(
            f"保存できません。受け口の URL {url!r} が URL の形ではありません。http:// か https:// で始まる URL にしてください。"
        )
    same = Connection.objects.filter(partner=partner, direction=direction)
    if exclude_pk is not None:
        same = same.exclude(pk=exclude_pk)
    if same.exists():
        label = dict(Connection.DIRECTION_CHOICES)[direction]
        raise ValueError(
            f"保存できません。この取引先との「{label}」のつながりは既にあります。つながりの一覧の行から編集してください。"
        )


def register_connection(partner, url, secret_env, direction, user):
    """つながりを登録する。secret_env は鍵を入れる環境変数の名前で、値は受け取らない。

    向きは send (送る) か receive (受ける)。確かめは _check_connection。
    誤りは日本語の文の ValueError。
    """
    _check_connection(partner, url, secret_env, direction)
    with transaction.atomic():
        return Connection.objects.create(
            partner=partner, url=url, secret_env=secret_env, direction=direction
        )


def update_connection(connection, partner, url, secret_env, direction, user):
    """つながりを直す。引数は register_connection にそろえ、対象を先頭に置く (01 の 10.1)。

    確かめは登録と同じ。鍵の値は受け取らない。
    user は記録のために受ける (記録の列がまだ無いので、今は受けるだけ)。返す物は取り直した Connection。
    """
    _check_connection(partner, url, secret_env, direction, exclude_pk=connection.pk)
    with transaction.atomic():
        locked = Connection.objects.select_for_update().get(pk=connection.pk)
        locked.partner = partner
        locked.url = url
        locked.secret_env = secret_env
        locked.direction = direction
        locked.save()
        return locked


def receive(raw_body: bytes, headers: Mapping[str, str]):
    """受け口が受けた注文のデータを確かめ、受信箱に入れる (R11・R12)。

    raw_body は受けた本文の bytes (整形しない)。headers は見出しの名前 → 値の mapping。
    通れば Inbox を返す。同じ番号が既にあれば、新しい行を作らず 1 回目の行を返す。
    通らなければ断る理由の日本語の文字列を返し、受信箱には入れない。
    本文の kind が order_reply (注文回答) なら受信箱に入れず、元の発注に当てて ReplyReceipt を返す。
    本文の kind が acceptance (検収) なら受信箱に入れず、受注の明細に当てて AcceptanceReceipt を返す。
    本文の kind が shipping_notice (出荷の知らせ) なら受信箱に入れず、発注の明細に当てて ShippingNoticeReceipt を返す。
    本文の kind が chat_message (会話の言葉) なら受信箱に入れず、元の注文 (受信した注文か発注) に結んで ChatReceipt を返す。
    本文の kind が chat_file (会話の添付を取りに来た要求) なら何も残さず、返してよい添付なら ChatFileGrant を返す。
    kind が無い物は注文として入れる。知らない kind と、明細 (lines) の無い注文は断る (受信箱に残さない)。
    """
    msg_id = _header_value(headers, signing.HEADER_ID)

    partner = None
    verified_id = None
    for connection in Connection.objects.filter(
        direction=Connection.DIRECTION_RECEIVE
    ).select_related("partner"):
        secret = os.environ.get(connection.secret_env)
        if not secret:
            logger.warning(
                "受け口: 鍵が未設定のつながりを飛ばした (環境変数 %s)", connection.secret_env
            )
            continue
        try:
            verified_id = signing.verify(secret, headers, raw_body)
        except signing.SignatureError as exc:
            if exc.reason in _HEADER_ERRORS:
                return _refuse(_HEADER_ERRORS[exc.reason], msg_id)
            continue
        except ValueError:
            # 鍵の符号化が壊れている。送り手でなく設定の誤りなので、名前だけを出して次へ。
            logger.warning(
                "受け口: 鍵の形が読めないつながりを飛ばした (環境変数 %s。設定の誤り)",
                connection.secret_env,
            )
            continue
        partner = connection.partner
        break

    if partner is None:
        return _refuse(MESSAGE_SIGNATURE_MISMATCH, msg_id)

    try:
        message_id = uuid.UUID(verified_id)
    except ValueError:
        return _refuse(MESSAGE_INVALID_ID, msg_id)

    # 本文は署名が通った後で読む。本文の中の番号は見ない (見出しの番号が正)。
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except ValueError:
        return _refuse(MESSAGE_INVALID_BODY, msg_id)
    if not isinstance(payload, dict):
        return _refuse(MESSAGE_INVALID_BODY, msg_id)

    # 注文回答 (承認か断りの結果) は受信した注文の行にせず、元の発注に当てる。
    # kind が無い物は注文として入れる。知らない kind は断る。
    if payload.get("kind") == "order_reply":
        return _receive_reply(message_id, partner, payload)
    # 検収 (相手が受け入れた数の知らせ) も受信した注文の行にせず、元の注文から作った受注の明細に当てる。
    if payload.get("kind") == "acceptance":
        return _receive_acceptance(message_id, partner, payload)
    # 出荷の知らせ (相手が出荷した日と数) も受信した注文の行にせず、元の送った注文の発注の明細に当てる。
    if payload.get("kind") == "shipping_notice":
        return _receive_shipping_notice(message_id, partner, payload)
    # 会話の言葉 (seizou どうしだけの種類。EDI の文書でない) も受信した注文の行にせず、元の注文 (受信した注文か発注) に結ぶ。
    if payload.get("kind") == "chat_message":
        return _receive_chat_message(message_id, partner, payload)
    # 会話の添付を取りに来た要求 (seizou どうしだけの種類)。何も残さず、返してよい添付なら ChatFileGrant を返す (受け口の view が中身を返す)。
    if payload.get("kind") == "chat_file":
        return _receive_chat_file_request(message_id, partner, payload)
    kind = payload.get("kind")
    if kind is not None and kind != "order":
        # 鍵の値と本文は文に入れない。kind の字だけを 40 字で切って出す。
        shown = kind[:40] if isinstance(kind, str) else "(字でない値)"
        return _refuse(MESSAGE_UNKNOWN_KIND.format(kind=shown), msg_id)
    # 明細の無い注文は受信箱に入れずに断る (数の欄の検査 _order_lines_problem の前)。
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        return _refuse(MESSAGE_RECEIVE_NO_LINES, msg_id)

    # 明細の数が受注の明細の表の欄に入らない注文は、受信箱に入れずに断る (承認の後の 500 を防ぐ。精査 A1 の不具合 2)。
    # 変更の版の取り消した明細 (明細状態コード 2) は受注に入らないので見ない。
    problem = _order_lines_problem(_without_deleted_lines(payload))
    if problem:
        return _refuse(problem, msg_id)
    # 発注番号・品番・品名・単位・明細の備考の字が受ける表の欄に入らない注文も、受信箱に入れずに断る (数の断りの次。docs/edi-items.md)。
    problem = _order_text_problem(payload)
    if problem:
        return _refuse(problem, msg_id)
    # 注文の版とコード (中小企業共通 EDI の注文変更の方式 2)。無い鍵は第 1 版の新規 (今までの seizou との送り合いのため)。
    codes = _order_codes(payload)
    if isinstance(codes, str):
        return _refuse(codes, msg_id)
    revision_no, purpose_code = codes
    revises = None
    if purpose_code != edi_codes.PURPOSE_NEW:
        # 変更と取消の版。受け入れた後の送り直し (届いた返事が落ちて相手が「再送」を押した) を断らないよう、同じ番号の行は見ずに返す (R11)。
        existing = Inbox.objects.filter(message_id=message_id).first()
        if existing is not None:
            return existing
        revises = _revision_base(partner, payload)
        if revises is None:
            return _refuse(MESSAGE_REVISION_NO_BASE, msg_id)
        problem = _revision_problem(revises, payload, revision_no, purpose_code)
        if problem:
            return _refuse(f"注文の変更を受けられません。{problem}", msg_id)
    # R11: 番号の一意の制約に当て、2 回目は 1 回目の行を返す。
    # 入れ子の atomic で囲み、制約に当たっても外のトランザクションを壊さない。
    try:
        with transaction.atomic():
            return Inbox.objects.create(
                message_id=message_id,
                partner=partner,
                kind=Inbox.KIND_ORDER,
                payload=payload,
                revision_no=revision_no,
                purpose_code=purpose_code,
                revises=revises,
            )
    except IntegrityError:
        return Inbox.objects.get(message_id=message_id)


#: 注文の版とコードの断り (送る側へ返す文)
MESSAGE_ORDER_CODES = "注文書目的コードか明細状態コードが読めません。"
MESSAGE_ORDER_REVISION = "注文履歴番号が読めません。新規の注文は 1、変更と取消は 2 以上の整数で送ってください。"
MESSAGE_REVISION_NO_BASE = "注文の変更の元の注文がありません。注文のデータの番号 (order_id) を確かめてください。"
_PURPOSES = frozenset(value for value, _label in edi_codes.PURPOSE_CHOICES)
_LINE_STATUSES = frozenset(value for value, _label in edi_codes.LINE_STATUS_CHOICES)
_LINE_REASONS = frozenset(value for value, _label in edi_codes.LINE_REASON_CHOICES) | {""}


def _revision_number(value):
    """知らせの注文履歴番号 (order_revision_no) を読む。無ければ 1。1 以上の整数 (bool でない) でなければ None。"""
    if value is None:
        return 1
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _order_codes(payload):
    """注文の (注文履歴番号, 注文書目的コード) か、断る理由の文。

    無い鍵は第 1 版の新規 (版 1・目的 "9"・明細 "39"・理由 "")。コードの値が core.edi_codes の組の外か、目的が新規 ("9") なのに版が 1 でない
    (版は 1 以上の整数で bool でない) 物は断る。値は字のまま比べる (数にしない)。
    """
    purpose_code = payload.get("purpose_code", edi_codes.PURPOSE_NEW)
    # 字でない値 (数・list) は組と比べる前に断る (list は frozenset の in で TypeError になる)。
    if not isinstance(purpose_code, str) or purpose_code not in _PURPOSES:
        return MESSAGE_ORDER_CODES
    for raw in payload.get("lines") or ():
        if not isinstance(raw, dict):
            continue
        status = raw.get("line_status_code", edi_codes.LINE_NEW)
        reason = raw.get("line_status_reason_code", "")
        if not isinstance(status, str) or status not in _LINE_STATUSES:
            return MESSAGE_ORDER_CODES
        if not isinstance(reason, str) or reason not in _LINE_REASONS:
            return MESSAGE_ORDER_CODES
    revision_no = _revision_number(payload.get("order_revision_no"))
    if revision_no is None:
        return MESSAGE_ORDER_REVISION
    if purpose_code == edi_codes.PURPOSE_NEW and revision_no != 1:
        return MESSAGE_ORDER_REVISION
    if purpose_code != edi_codes.PURPOSE_NEW and revision_no < 2:
        return MESSAGE_ORDER_REVISION
    return revision_no, purpose_code


def _is_deleted(raw) -> bool:
    """注文の明細が取り消した明細 (明細状態コード 2) か。"""
    return isinstance(raw, dict) and raw.get("line_status_code") == edi_codes.LINE_DELETED


def _without_deleted_lines(payload):
    """取り消した明細 (明細状態コード 2) を除いた注文の写し (数の欄の検査のため。2 の行は受注に入らない)。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("lines"), list):
        return payload
    return {**payload, "lines": [raw for raw in payload["lines"] if not _is_deleted(raw)]}


def _revision_base(partner, payload):
    """変更と取消の版の元の行 (第 1 版の受信した注文)。注文の order_id (第 1 版の番号) で、署名の通った取引先から受け口で受けた第 1 版の行を引く。無ければ None。"""
    order_id = payload.get("order_id")
    if not isinstance(order_id, str):
        return None
    try:
        number = uuid.UUID(order_id)
    except ValueError:
        return None
    return (
        Inbox.objects.select_related("order")
        .filter(message_id=number, partner=partner, revises=None, source=Inbox.SOURCE_DIRECT)
        .first()
    )


def _line_number(raw):
    """注文の明細の行番号 (1 以上の整数で bool でない)。読めなければ None。"""
    value = raw.get("line_no") if isinstance(raw, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _decimal_or_none(value):
    """注文の数の字を Decimal に読む。読めなければ None (読めない数は受け入れの時に断る)。"""
    try:
        return _payload_decimal(value, "")
    except ValueError:
        return None


#: 受注の明細を指す物 (指示書・入荷の引当・検収) の逆の関係の名前。部品の無い会社では属性が無く、無い扱い
_LINE_REFERENCES = ("workorders", "receiving_allocations", "acceptances")


def _referenced(row) -> bool:
    """受注の明細を指示書・入荷の引当・検収のどれかが指しているか (読むだけ。connect はほかの部品を import しない)。"""
    for name in _LINE_REFERENCES:
        related = getattr(row, name, None)
        if related is not None and related.exists():
            return True
    return False


def _revision_problem(base, payload, revision_no, purpose_code):
    """変更と取消の版 (payload) を元の行 (base = 第 1 版の受信した注文) の受注に当てられない訳の字。当てられれば None。

    受け口 (receive)・受け入れ (_accept_revision)・受信した注文の画面 (connect_tags.revision_problem) が同じ見方で呼ぶ。
    見る順: 元の行が断った → 未処理なら None (受けて待たせる。受け入れの時にもう一度見る) → 受け入れてある版以下の版 → 受注が取消・出荷済 →
    取消の版で受注が一部出荷 → 変更の版の明細ごと (行番号で受注の明細を引く): 品番が違う・取り消す明細に出荷済みがある・
    取り消す明細を指示書か入荷の引当か検収が指す・出荷済みのある明細の単価が違う・数が出荷済みより少ない。受注に無い行番号の明細は断らない (受け入れの時に足す)。
    訳: 出荷し終えた受注と、出した物の記録 (納品書・請求書) と食い違う変更を受注に当てず、作り始めた物の記録 (指示書・入荷の引当) と届いた検収が指す受注の明細を消さないため。出所 = 決め EDIT-1 (11-06 23:2x)・中小企業共通 EDI に合わせた決め EDIT-2 (11-06 23:2x)・決め C96-1 (11-07 06:33。一部を出荷した受注への取消を受けない)・決め C96-4 (11-07 06:37。指示書・入荷の引当・検収・出荷のある受注の明細を消す変更を受けない)。
    変えてよい条件 = EDIT-1・EDIT-2・C96-1・C96-4 の答えが変わったとき。
    """
    if base.status == Inbox.STATUS_REJECTED:
        return "第 1 版の注文は断ってあるので、変える受注がありません。"
    if base.status == Inbox.STATUS_RECEIVED:
        return None
    order = base.order
    if order is None:
        return "第 1 版の注文から作った受注が無いので、変える受注がありません。"
    current = base.current_revision_no()
    if revision_no <= current:
        return f"受注 {order.order_no} は第 {current} 版まで受け入れてあります。"
    if order.status == order.STATUS_CANCELLED:
        return f"受注 {order.order_no} は取り消してあります。"
    if order.status == order.STATUS_SHIPPED:
        return f"受注 {order.order_no} は出荷済なので変えられません (送った注文を直せるのは、受注を出荷し終えるまで)。"
    if purpose_code == edi_codes.PURPOSE_CANCEL:
        if order.status == order.STATUS_PARTIAL:
            return f"受注 {order.order_no} は一部を出荷済みなので、取消を受けられません。"
        return None
    rows = {row.line_no: row for row in order.lines.all()}
    from seizou.core.templatetags.seizou_tags import yen

    for raw in payload.get("lines") or ():
        number = _line_number(raw)
        if number is None:
            return "明細の行番号が読めません。"
        row = rows.get(number)
        if row is None:
            continue
        part_no = raw.get("part_no")
        part_no = part_no.strip() if isinstance(part_no, str) else ""
        if part_no != row.customer_part_no:
            shown = row.customer_part_no or "—"
            return f"明細 {number} の品番が、受注の明細 {number} (相手の品番 {shown}) と違います。"
        shipped = shipped_qty(row)
        if _is_deleted(raw):
            if shipped > 0:
                return f"明細 {number} は出荷済みの物があるので取り消せません。"
            if _referenced(row):
                return f"明細 {number} は、受注の明細に指示書か入荷の引当か検収があるので、受注から消せません。"
            continue
        if shipped <= 0:
            continue
        unit_price = _decimal_or_none(raw.get("unit_price"))
        if unit_price is not None and unit_price != row.unit_price:
            return (
                f"明細 {number} は出荷済みの物があるので単価は変えられません "
                f"(注文の単価 {yen(unit_price)}・受注の単価 {yen(row.unit_price)})。"
            )
        qty = _decimal_or_none(raw.get("qty"))
        if qty is not None and qty < shipped:
            return f"明細 {number} の数 ({_qty_text(qty)}) が出荷済みの数 ({_qty_text(shipped)}) より少なくなります。"
    return None


@dataclass(frozen=True)
class ReplyReceipt:
    """受け口が注文回答を元の発注に当てた (か、同じ番号の 2 回目だった) 印。

    受け口の view は message_id だけを読む (注文は Inbox、注文回答はこの印。どちらも message_id を持つ)。
    """

    message_id: uuid.UUID
    purchase_order: object


@dataclass(frozen=True)
class AcceptanceReceipt:
    """受け口が検収を受注の明細に付けた (か、同じ番号の 2 回目だった) 印。

    受け口の view は message_id だけを読む (ReplyReceipt と同じ)。acceptance は付けた Acceptance の行。
    """

    message_id: uuid.UUID
    acceptance: object


@dataclass(frozen=True)
class ShippingNoticeReceipt:
    """受け口が出荷の知らせを発注の明細に付けた (か、同じ知らせの 2 回目だった) 印。

    受け口の view は message_id だけを読む (ReplyReceipt と同じ)。message_id は付けた行の番号
    (同じ明細 × 同じ出荷番号の 2 回目は 1 回目の行の番号)。notice は付けた ShippingNotice の行。
    """

    message_id: uuid.UUID
    notice: object


@dataclass(frozen=True)
class ChatReceipt:
    """受け口が会話の言葉を元の注文 (受信した注文か発注) に結んだ (か、同じ番号の 2 回目だった) 印。

    受け口の view は message_id だけを読む (ReplyReceipt と同じ)。chat_message は作った ChatMessage の行 (2 回目は 1 回目の行)。
    """

    message_id: uuid.UUID
    chat_message: object


@dataclass(frozen=True)
class ChatFileGrant:
    """受け口が、会話の添付を取りに来た相手に返してよいと決めた印 (受け口の view はファイルの中身を返す)。

    message_id は要求の番号 (見出しの webhook-id)。chat_file は返す ChatFile の行 (自社が書いた言葉の添付)。
    """

    message_id: uuid.UUID
    chat_file: object


MESSAGE_REPLY_SHAPE = "注文回答の形が読めません。order_no・order_id・reply_type を字で送ってください。"
MESSAGE_REPLY_TYPE = "注文回答のタイプ (reply_type) が AP・CA・RE・AJ のどれでもありません。"
MESSAGE_REPLY_LINES = (
    "条件付受諾 (CA) の注文回答の明細 (lines) が読めません。line_no (数)・reply_type (AP・CA・RE) と、"
    "CA の明細の reply_unit_price・reply_qty (0 より大きい数の字)・reply_due_date (YYYY-MM-DD か null) を送ってください。"
)
MESSAGE_REPLY_OTHER_PARTNER = "注文回答の元の発注は、その取引先への物ではありません。"
MESSAGE_REPLY_ORDER_ID = "注文回答の元の注文のデータの番号が、送った物と合いません。"
#: 注文回答のタイプの値 (中小企業共通 EDI の注文回答タイプコード) と、明細の回答タイプの値 (注文回答明細回答タイプコード。保留 AJ は無い)
REPLY_TYPES = frozenset(code for code, _label in edi_codes.ORDER_REPLY_TYPES)
REPLY_LINE_TYPES = frozenset(code for code, _label in edi_codes.ORDER_REPLY_LINE_TYPES)

# 検収 (EDI の 4 段目) の断る理由と、送らなかった理由の日本語。
MESSAGE_ACCEPTANCE_SHAPE = "検収の形が読めません。order_id・line_no・accepted_on・qty を送ってください。"
MESSAGE_ACCEPTANCE_UNKNOWN_ORDER = (
    "検収の元の注文 (受信した注文) がありません。注文のデータの番号を確かめてください。"
)
MESSAGE_ACCEPTANCE_OTHER_PARTNER = "検収の元の注文は、その取引先からの物ではありません。"
MESSAGE_ACCEPTANCE_NOT_ACCEPTED = (
    "検収の元の注文は受け入れていない (受注になっていない) ので、検収を受けられません。"
    "受け入れの後に送り直してください。"
)
MESSAGE_ACCEPTANCE_QTY = "検収の数が 0 より大きい数の形ではありません。"
MESSAGE_ACCEPTANCE_QTY_DIGITS = "検収の数は、整数の部分 10 桁・小数 2 桁までにしてください。"
MESSAGE_ACCEPTANCE_DATE = "検収日が日付 (YYYY-MM-DD) の形ではありません。"
MESSAGE_ACCEPTANCE_NOT_SENT_ORDER = (
    "seizou どうしで送った発注ではないので、検収を送っていません。"
    "相手に送った発注の受入だけが検収になります。"
)
MESSAGE_ACCEPTANCE_NO_CONNECTION = (
    "取引先との接続 (送る向き) が無いので検収を送っていません。"
    "設定の画面の「取引先との接続」で、この取引先に送る向きの接続を登録してください。"
)

# 出荷の知らせ (EDI の 3 段目) の断る理由と、送らなかった理由の日本語。
MESSAGE_SHIPPING_SHAPE = (
    "出荷の知らせの形が読めません。order_id・shipment_no・shipped_at・qty を字で、line_no を数で送ってください。"
)
MESSAGE_SHIPPING_UNKNOWN_ORDER = (
    "出荷の知らせの元の注文 (送った注文) がありません。注文のデータの番号を確かめてください。"
)
MESSAGE_SHIPPING_OTHER_PARTNER = "出荷の知らせの元の発注は、その取引先への物ではありません。"
MESSAGE_SHIPPING_QTY = "出荷の知らせの数が 0 より大きい数の形ではありません。"
MESSAGE_SHIPPING_QTY_DIGITS = "出荷の知らせの数は、整数の部分 10 桁・小数 2 桁までにしてください。"
MESSAGE_SHIPPING_CANCELLED = "出荷の知らせの元の発注は取り消してあるので、受けられません。発注した会社に確かめてください。"
MESSAGE_SHIPPING_DATE = "出荷日が日付 (YYYY-MM-DD) の形ではありません。"
MESSAGE_SHIPPING_NOT_EDI_ORDER = (
    "受信した注文で作った受注ではないので、出荷の知らせを送っていません。"
    "seizou どうしで受信した注文を受け入れた受注の出荷だけが、相手への出荷の知らせになります。"
)
#: 会話の言葉の受け口の断り (送る側へ返す文)
MESSAGE_CHAT_SHAPE = (
    "会話の言葉の形が読めません。order_id・sender_role・written_at・body を字で、言葉は 2000 字まで (添付が無いときは 1 字から) で送ってください。"
)
MESSAGE_CHAT_UNKNOWN_ORDER = "会話の元の注文がありません。注文のデータの番号を確かめてください。"
MESSAGE_CHAT_OTHER_PARTNER = "会話の元の注文は、その取引先との物ではありません。"
#: 会話の言葉の上限の字数 (EDI に会話は無く、字数の決まりも無いので作品で決めた。docs/nights/20261106/N311.md)
CHAT_BODY_MAX = 2000
#: 会話の送り手の立場 (buyer = 発注した側 / supplier = 受けた側)
CHAT_ROLES = ("buyer", "supplier")
#: 会話の添付を取りに行く要求の断り (送る側へ返す文。その添付の有る無しを、ほかの取引先に教えない)
MESSAGE_CHAT_FILE_SHAPE = "添付の要求の形が読めません。file_id を字で送ってください。"
MESSAGE_CHAT_FILE_UNKNOWN = "その添付はありません。"
MESSAGE_CHAT_FILE_MISSING = "添付のファイルが見つかりません。書いた会社に確かめてください。"
_SHA256 = re.compile(r"[0-9a-f]{64}")
MESSAGE_SHIPPING_NO_CONNECTION = (
    "取引先との接続 (送る向き) が無いので出荷の知らせを送っていません。"
    "設定の画面の「取引先との接続」で、この取引先に送る向きの接続を登録してください。"
)


def _receive_reply(message_id, partner, payload):
    """署名の通った注文回答を、元の発注に 1 回だけ当てる。返す物は ReplyReceipt か、断る理由の文。

    引く順は 発注番号 (order_no) → 発注の取引先が署名の通ったつながりの取引先と同じ → その発注の
    送った注文に order_id の番号がある。同じ回答の番号の 2 回目は何もせずに通す (R11 の考え)。
    発注を書くのは purchasing の services だけ (受け口に利用者はいないので user は None)。
    答えは reply_type の 4 つ: 受諾 AP → mark_accepted・条件付受諾 CA → mark_conditional (明細の答えは lines)・
    辞退 RE → mark_rejected・保留 AJ → mark_on_hold。CA の明細は atomic に入る前に _reply_lines で形を見る。
    断るときは全部を戻す (作成 → 送った も残さない)。
    訳: 相手の 4 つの答えを、答えを待っている発注にだけ当て、形の違う答えを当てずに相手へ返すため。出所 = 中小企業共通 EDI の注文回答タイプコードの項目と決め PRICE-4e (11-07 00:0x)・PRICE-4f (11-06 23:1x)。
    変えてよい条件 = 送り合いの形を変えるとき。
    """
    # connect は purchasing の model を頭で import しない (輪になるため)。
    from django.apps import apps

    from seizou.purchasing import services as purchasing_services

    from .models import Outbox

    purchase_order_model = apps.get_model("purchasing", "PurchaseOrder")
    msg_id = str(message_id)
    order_no = payload.get("order_no")
    order_id = payload.get("order_id")
    reply_type = payload.get("reply_type")
    # str でない値を filter に渡すと TypeError になるので、先に形を見る。
    if not all(isinstance(value, str) and value for value in (order_no, order_id, reply_type)):
        return _refuse(MESSAGE_REPLY_SHAPE, msg_id)
    if reply_type not in REPLY_TYPES:
        return _refuse(MESSAGE_REPLY_TYPE, msg_id)
    # 注文履歴番号は形だけ見る (あれば 1 以上の整数)。どの版への答えかは、送った注文の行の版で決める。
    if _revision_number(payload.get("order_revision_no")) is None:
        return _refuse(MESSAGE_REPLY_SHAPE, msg_id)
    try:
        order_message_id = uuid.UUID(order_id)
    except ValueError:
        return _refuse(MESSAGE_REPLY_ORDER_ID, msg_id)
    reason = payload.get("reason")
    reason = reason if isinstance(reason, str) else ""
    lines = None
    if reply_type == edi_codes.REPLY_CONDITIONAL:
        lines = _reply_lines(payload)
        if lines is None:
            return _refuse(MESSAGE_REPLY_LINES, msg_id)

    with transaction.atomic():
        # 同じ回答が同時に 2 つ届いても、2 つ目は 1 つ目が終わるまで待ち、番号で見分ける。
        purchase_order = (
            purchase_order_model.objects.select_for_update().filter(po_no=order_no).first()
        )
        if purchase_order is None:
            return _refuse(
                f"注文回答の元の発注 {order_no[:35]} がありません。発注番号を確かめてください。", msg_id
            )
        if purchase_order.partner_id != partner.pk:
            return _refuse(MESSAGE_REPLY_OTHER_PARTNER, msg_id)
        outbox = (
            Outbox.objects.filter(purchase_order=purchase_order, message_id=order_message_id).order_by("pk").first()
        )
        if outbox is None:
            return _refuse(MESSAGE_REPLY_ORDER_ID, msg_id)
        # 版 2 から (変更と取消) の答えは送った注文の行に書き、発注の状態と第 1 版の答えの列は変えない。
        if outbox.revision_no >= 2:
            _record_revision_reply(outbox, message_id, reply_type, reason)
            # 条件付受諾 (CA) の明細の答えは、第 1 版と同じく発注の明細に写す (答えの写し方を 1 か所にする)。
            if reply_type == edi_codes.REPLY_CONDITIONAL and lines is not None:
                try:
                    purchasing_services.record_line_replies(purchase_order, lines)
                except ValueError as exc:
                    transaction.set_rollback(True)
                    return _refuse(str(exc), msg_id)
            return ReplyReceipt(message_id=message_id, purchase_order=purchase_order)
        if purchase_order.reply_message_id == message_id:
            return ReplyReceipt(message_id=message_id, purchase_order=purchase_order)
        # 取消の版を送った後に第 1 版の答えが届いた (行き違い): 状態を変えずに第 1 版の答えの列だけを書いて受ける
        # (取り消した発注は「送った」にできないので、今の道では相手に誤った断りが返り、相手が答えを送り直しても通らないため)。
        if purchase_order.status == purchase_order.STATUS_CANCELLED and purchase_order.revision_no >= 2:
            updated = purchasing_services.record_reply_after_cancel(
                purchase_order, None, reason=reason, reply_message_id=message_id
            )
            return ReplyReceipt(message_id=message_id, purchase_order=updated)
        try:
            # 回答が来た = 相手は注文を受け取っている。「作成」なら先に「送った」にする
            # (送った時の返事が落ちた場合の救い)。「取消」は mark_sent が断る。
            if purchase_order.status != purchase_order.STATUS_SENT:
                purchasing_services.mark_sent(purchase_order, None)
            if reply_type == edi_codes.REPLY_ACCEPTED:
                updated = purchasing_services.mark_accepted(
                    purchase_order, None, reply_message_id=message_id
                )
            elif reply_type == edi_codes.REPLY_CONDITIONAL:
                updated = purchasing_services.mark_conditional(
                    purchase_order, None, lines=lines, reply_message_id=message_id
                )
            elif reply_type == edi_codes.REPLY_PENDING:
                updated = purchasing_services.mark_on_hold(
                    purchase_order, None, reply_message_id=message_id
                )
            else:
                updated = purchasing_services.mark_rejected(
                    purchase_order, None, reason=reason, reply_message_id=message_id
                )
        except (ValueError, IntegrityError) as exc:
            transaction.set_rollback(True)
            return _refuse(str(exc), msg_id)
    return ReplyReceipt(message_id=message_id, purchase_order=updated)


def _reply_lines(payload):
    """条件付受諾 (CA) の注文回答の明細 (lines) を読み、purchasing.services.record_line_replies に渡す並びにする。形が違えば None。

    見る事: 空でない並び・どれも dict・line_no は bool でない 1 以上の整数で重ならない・reply_type は明細の回答タイプ (AP・CA・RE)・
    CA の明細は reply_unit_price と reply_qty が字で、Decimal に読めて 0 より大きく、発注の明細の単価と数量の列の桁に入る (_fits_field)・
    reply_due_date は null か日付 (YYYY-MM-DD) の字。AP と RE の明細のほかの鍵は読まない。
    connect は purchasing の model を頭で import しない (app の登録から引く)。
    訳: 形の違う明細の答えを表に入れる前に断り、桁を超える数を表に入れて後で 500 になるのを防ぐため。出所 = 中小企業共通 EDI の注文回答明細回答タイプコードの項目と決め PRICE-4f (11-06 23:1x)。
    変えてよい条件 = 送り合いの形を変えるとき。
    """
    from django.apps import apps

    line_model = apps.get_model("purchasing", "PurchaseOrderLine")
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        return None
    rows = []
    seen = set()
    for raw in raw_lines:
        if not isinstance(raw, dict):
            return None
        number = raw.get("line_no")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1 or number in seen:
            return None
        seen.add(number)
        line_type = raw.get("reply_type")
        if not isinstance(line_type, str) or line_type not in REPLY_LINE_TYPES:
            return None
        row = {"line_no": number, "reply_type": line_type, "unit_price": None, "qty": None, "due_date": None}
        if line_type == edi_codes.REPLY_CONDITIONAL:
            for key, name in (("reply_unit_price", "unit_price"), ("reply_qty", "qty")):
                value = raw.get(key)
                if not isinstance(value, str):
                    return None
                try:
                    figure = Decimal(value.strip())
                except InvalidOperation:
                    return None
                if not _fits_field(figure, line_model._meta.get_field(name)):
                    return None
                row[name] = figure
            due = raw.get("reply_due_date")
            if due is not None:
                if not isinstance(due, str):
                    return None
                try:
                    row["due_date"] = date.fromisoformat(due)
                except ValueError:
                    return None
        rows.append(row)
    return rows


def _record_revision_reply(outbox, message_id, reply_type, reason):
    """版 2 から (変更と取消) の注文回答を、その版の最初の送った注文の行 (outbox) に書く。返す物は取り直した行。

    reply_type は EDI の注文回答タイプコードの値 (core.edi_codes)。同じ回答の番号 (reply_message_id) の 2 回目は何もしない。
    別の番号の答えは上書きする (後から届いた本当の答えを残すため)。断らない (ValueError を投げない)。
    発注の状態と第 1 版の答えの列 (PurchaseOrder.replied_at ほか) は変えない (変更への答えを待つ間も受入と出荷の知らせを今のまま受けるため)。
    """
    from django.utils import timezone

    from .models import Outbox

    locked = Outbox.objects.select_for_update().get(pk=outbox.pk)
    if locked.reply_message_id == message_id:
        return locked
    limit = Outbox._meta.get_field("reply_note").max_length
    locked.reply_type = reply_type
    locked.reply_note = (reason if isinstance(reason, str) else "")[:limit]
    locked.replied_at = timezone.now()
    locked.reply_message_id = message_id
    locked.save(update_fields=["reply_type", "reply_note", "replied_at", "reply_message_id"])
    return locked


def _receive_acceptance(message_id, partner, payload):
    """署名の通った検収を、元の受信した注文から作った受注の明細に 1 回だけ付ける。返す物は AcceptanceReceipt か、断る理由の文。

    引く順は 形 → 元の注文の番号 (order_id) で受信した注文 → 受信した注文の取引先が署名の通ったつながりの取引先と同じ
    → 受信した注文が受け入れた (受注がある) 物 → その受注の明細の行番号 (line_no)。
    同じ検収の番号の 2 回目は 1 回目の行を返す (R11 の考え)。受け口に利用者はいないので user は None。
    """
    msg_id = str(message_id)
    order_id = payload.get("order_id")
    order_no = payload.get("order_no")
    line_no = payload.get("line_no")
    accepted_on = payload.get("accepted_on")
    qty = payload.get("qty")
    # str でない値を filter と Decimal に渡さないよう、先に形を見る。line_no は JSON の整数 (bool は int の子なので外す)。
    if not all(isinstance(value, str) and value for value in (order_id, accepted_on, qty)):
        return _refuse(MESSAGE_ACCEPTANCE_SHAPE, msg_id)
    if isinstance(line_no, bool) or not isinstance(line_no, int) or line_no < 1:
        return _refuse(MESSAGE_ACCEPTANCE_SHAPE, msg_id)
    # 検収が指す注文の版 (無ければ 1 = 今までの seizou との送り合い)。前の版への検収も止めずに受け、版の番号を残す。
    order_revision_no = _revision_number(payload.get("order_revision_no"))
    if order_revision_no is None:
        return _refuse(MESSAGE_ACCEPTANCE_SHAPE, msg_id)
    order_no = order_no if isinstance(order_no, str) else ""
    try:
        order_message_id = uuid.UUID(order_id)
    except ValueError:
        return _refuse(MESSAGE_ACCEPTANCE_UNKNOWN_ORDER, msg_id)
    try:
        qty_value = _payload_decimal(qty, "検収の数")
    except ValueError:
        return _refuse(MESSAGE_ACCEPTANCE_QTY, msg_id)
    if not qty_value > 0:
        return _refuse(MESSAGE_ACCEPTANCE_QTY, msg_id)
    try:
        accepted_date = date.fromisoformat(accepted_on)
    except ValueError:
        return _refuse(MESSAGE_ACCEPTANCE_DATE, msg_id)

    inbox = (
        Inbox.objects.select_related("order", "partner").filter(message_id=order_message_id).first()
    )
    if inbox is None:
        return _refuse(MESSAGE_ACCEPTANCE_UNKNOWN_ORDER, msg_id)
    if inbox.partner_id != partner.pk:
        return _refuse(MESSAGE_ACCEPTANCE_OTHER_PARTNER, msg_id)
    if inbox.status != Inbox.STATUS_ACCEPTED or inbox.order is None:
        return _refuse(MESSAGE_ACCEPTANCE_NOT_ACCEPTED, msg_id)
    line = inbox.order.lines.filter(line_no=line_no).first()
    if line is None:
        return _refuse(f"検収の明細の行番号 {line_no} が受注 {inbox.order.order_no} にありません。", msg_id)

    with transaction.atomic():
        try:
            acceptance = record_acceptance(
                inbox,
                line,
                qty_value,
                accepted_date,
                None,
                message_id=message_id,
                buyer_order_no=order_no,
                buyer_line_no=line_no,
                order_revision_no=order_revision_no,
            )
        except ValueError as exc:
            transaction.set_rollback(True)
            return _refuse(str(exc), msg_id)
    return AcceptanceReceipt(message_id=message_id, acceptance=acceptance)


def record_acceptance(inbox, order_line, qty, accepted_on, user, *, message_id, buyer_order_no, buyer_line_no, order_revision_no=1):
    """検収を受注の明細に 1 行で記録する (受ける側の書く関数)。返す物は Acceptance。

    数が 0 以下・表の欄の桁 (整数の部分 10 桁・小数 2 桁) に入らない・検収日が日付でない・その受信した注文から作った受注の明細でないときは日本語の ValueError。
    同じ番号 (message_id) の 2 回目は新しい行を作らず 1 回目の行を返す (R11 の考え。番号の一意の制約に当てる)。
    user は記録のために受ける (受け口からは None)。order_revision_no は検収が指す注文の版 (相手の発注の注文履歴番号)。
    """
    try:
        number = qty if isinstance(qty, Decimal) else Decimal(str(qty))
        positive = number.is_finite() and number > 0
    except InvalidOperation:
        positive = False
    if not positive:
        raise ValueError("検収の数は 0 より大きい数にしてください。")
    if not _fits_field(number, Acceptance._meta.get_field("qty")):
        raise ValueError(MESSAGE_ACCEPTANCE_QTY_DIGITS)
    if not isinstance(accepted_on, date):
        raise ValueError("検収日は日付にしてください。")
    if order_line.order_id != inbox.order_id:
        raise ValueError("検収の明細は、その受信した注文から作った受注の明細ではありません。")
    limit = Acceptance._meta.get_field("buyer_order_no").max_length
    # 入れ子の atomic で囲み、制約に当たっても外のトランザクションを壊さない (receive の Inbox と同じ)。
    try:
        with transaction.atomic():
            return Acceptance.objects.create(
                message_id=message_id,
                inbox=inbox,
                order_line=order_line,
                buyer_order_no=(buyer_order_no or "")[:limit],
                buyer_line_no=buyer_line_no,
                accepted_on=accepted_on,
                qty=number,
                order_revision_no=order_revision_no,
            )
    except IntegrityError:
        return Acceptance.objects.get(message_id=message_id)


def _receive_shipping_notice(message_id, partner, payload):
    """署名の通った出荷の知らせを、元の送った注文の発注の明細に 1 回だけ付ける。返す物は ShippingNoticeReceipt か、断る理由の文。

    引く順は 形 → 元の注文の番号 (order_id) で送った注文 (Outbox) → その発注の取引先が署名の通ったつながりの取引先と同じ
    → その発注の明細の行番号 (line_no)。取り消した発注への知らせは断る (送れずに「作成」のまま取り消した発注にも、
    断られた後に取り消した発注にも、送った注文の行がある。精査 A1 の不具合 4)。
    同じ番号か、同じ明細 × 同じ相手の出荷番号の 2 回目は 1 回目の行を返す (R11 の考え)。
    相手の発注番号 (order_no) は引く鍵でも記録する列でもないので読まない。受け口に利用者はいないので user は None。
    """
    # 冒頭の import の並びを変えないため、send_order と同じく関数の中で import する。
    from .models import Outbox

    msg_id = str(message_id)
    order_id = payload.get("order_id")
    line_no = payload.get("line_no")
    supplier_order_no = payload.get("supplier_order_no")
    shipment_no = payload.get("shipment_no")
    shipped_at = payload.get("shipped_at")
    qty = payload.get("qty")
    # str でない値を filter と Decimal に渡さないよう、先に形を見る。line_no は JSON の整数 (bool は int の子なので外す)。
    if not all(isinstance(value, str) and value for value in (order_id, shipment_no, shipped_at, qty)):
        return _refuse(MESSAGE_SHIPPING_SHAPE, msg_id)
    if isinstance(line_no, bool) or not isinstance(line_no, int) or line_no < 1:
        return _refuse(MESSAGE_SHIPPING_SHAPE, msg_id)
    # 知らせが指す注文の版 (無ければ 1 = 今までの seizou との送り合い)。前の版への知らせも止めずに受け、版の番号を残す。
    order_revision_no = _revision_number(payload.get("order_revision_no"))
    if order_revision_no is None:
        return _refuse(MESSAGE_SHIPPING_SHAPE, msg_id)
    if len(shipment_no) > ShippingNotice._meta.get_field("shipment_no").max_length:
        return _refuse(MESSAGE_SHIPPING_SHAPE, msg_id)
    supplier_order_no = supplier_order_no if isinstance(supplier_order_no, str) else ""
    try:
        order_message_id = uuid.UUID(order_id)
    except ValueError:
        return _refuse(MESSAGE_SHIPPING_UNKNOWN_ORDER, msg_id)
    try:
        qty_value = _payload_decimal(qty, "出荷の数")
    except ValueError:
        return _refuse(MESSAGE_SHIPPING_QTY, msg_id)
    if not qty_value > 0:
        return _refuse(MESSAGE_SHIPPING_QTY, msg_id)
    try:
        shipped_date = date.fromisoformat(shipped_at)
    except ValueError:
        return _refuse(MESSAGE_SHIPPING_DATE, msg_id)

    # 同じ発注の送り直しは同じ番号で Outbox に複数行ある (get でなく最初の 1 行)。
    outbox = (
        Outbox.objects.select_related("purchase_order")
        .filter(message_id=order_message_id)
        .order_by("pk")
        .first()
    )
    if outbox is None:
        return _refuse(MESSAGE_SHIPPING_UNKNOWN_ORDER, msg_id)
    purchase_order = outbox.purchase_order
    if purchase_order.partner_id != partner.pk:
        return _refuse(MESSAGE_SHIPPING_OTHER_PARTNER, msg_id)
    if purchase_order.status == purchase_order.STATUS_CANCELLED:
        return _refuse(MESSAGE_SHIPPING_CANCELLED, msg_id)
    line = purchase_order.lines.filter(line_no=line_no).first()
    if line is None:
        return _refuse(
            f"出荷の知らせの明細の行番号 {line_no} が発注 {purchase_order.po_no} にありません。", msg_id
        )

    with transaction.atomic():
        try:
            notice = record_shipping_notice(
                line,
                qty_value,
                shipped_date,
                None,
                message_id=message_id,
                shipment_no=shipment_no,
                supplier_order_no=supplier_order_no,
                order_revision_no=order_revision_no,
            )
        except ValueError as exc:
            transaction.set_rollback(True)
            return _refuse(str(exc), msg_id)
    return ShippingNoticeReceipt(message_id=notice.message_id, notice=notice)


def record_shipping_notice(line, qty, shipped_at, user, *, message_id, shipment_no, supplier_order_no="", order_revision_no=1):
    """出荷の知らせを発注の明細に 1 行で記録する (受ける側の書く関数)。返す物は ShippingNotice。

    数が 0 以下・表の欄の桁 (整数の部分 10 桁・小数 2 桁) に入らない・出荷日が日付でない・相手の出荷番号が 1〜35 字の字でないときは日本語の ValueError。
    同じ番号 (message_id) か、同じ明細 × 同じ相手の出荷番号の 2 回目は新しい行を作らず 1 回目の行を返す
    (R11 の考え。番号の一意の制約と、自然な鍵の一意の制約に当てる)。user は記録のために受ける (受け口からは None)。
    order_revision_no は知らせが指す注文の版 (相手の受注が合っていた注文履歴番号)。
    """
    try:
        number = qty if isinstance(qty, Decimal) else Decimal(str(qty))
        positive = number.is_finite() and number > 0
    except InvalidOperation:
        positive = False
    if not positive:
        raise ValueError("出荷の知らせの数は 0 より大きい数にしてください。")
    if not _fits_field(number, ShippingNotice._meta.get_field("qty")):
        raise ValueError(MESSAGE_SHIPPING_QTY_DIGITS)
    if not isinstance(shipped_at, date):
        raise ValueError("出荷日は日付にしてください。")
    limit = ShippingNotice._meta.get_field("shipment_no").max_length
    if not isinstance(shipment_no, str) or not shipment_no or len(shipment_no) > limit:
        raise ValueError("相手の出荷番号は 1〜35 字の字にしてください。")
    # 入れ子の atomic で囲み、制約に当たっても外のトランザクションを壊さない (record_acceptance と同じ)。
    try:
        with transaction.atomic():
            return ShippingNotice.objects.create(
                message_id=message_id,
                line=line,
                shipment_no=shipment_no,
                supplier_order_no=(supplier_order_no or "")[:limit],
                shipped_at=shipped_at,
                qty=number,
                order_revision_no=order_revision_no,
            )
    except IntegrityError:
        existing = (
            ShippingNotice.objects.filter(Q(message_id=message_id) | Q(line=line, shipment_no=shipment_no))
            .order_by("pk")
            .first()
        )
        if existing is None:
            raise
        return existing


def _receive_chat_message(message_id, partner, payload):
    """署名の通った会話の言葉を、元の注文 (自社が受けた側なら受信した注文、発注した側なら発注) に 1 回だけ結ぶ。返す物は ChatReceipt か、断る理由の文。

    会話は中小企業共通 EDI に無い seizou どうしだけの種類 (kind = chat_message)。EDI の電文に直す物に入れない。
    引く順は 形 → 送り手の立場 (sender_role) で引く表を決める → 元の注文の番号 (order_id) → その注文の取引先が署名の通ったつながりの取引先と同じ。
    sender_role が buyer (相手が発注した側) なら、自社が受け口で受けた第 1 版の受信した注文。supplier (相手が受けた側) なら、
    自社が送った第 1 版の注文 (同じ発注の送り直しは同じ番号で複数行なので最初の 1 行) の発注。立場を字で持つのは、1 つの DB に 2 社の役を置く試験で
    両方の表に同じ番号があるため。相手の発注番号 (order_no) は引く鍵でも記録する列でもないので読まない。author は字なら 100 字で切り、字でなければ空。
    知らない鍵は読み捨てる (後の版が足す鍵のため)。同じ番号の 2 回目は 1 回目の行を返す (R11 の考え。添付の行も作らない)。
    添付 (attachments) は項目だけを受け、言葉の行と同じ atomic で ChatFile の行を作る (ファイルは空。受け手の画面が開いた時に fetch_chat_file が取りに行く)。
    言葉は 2000 字まで。空か空白だけの言葉は、添付が 1 つ以上あるときだけ通す (添付だけの言葉のため)。
    相手から会話が届いた知らせ (メール) を足すのは、行を作った後の atomic の外 (同じ番号の 2 回目では呼ばない)。
    断る処理を足さない: 取消した発注・断った受信した注文・出荷し終えた受注への言葉も受ける。
    訳: 形の読めない言葉と、その取引先と結び付かない注文への言葉を受けないため (同じ番号の 2 回目は 1 件にまとめる = R11 の考え)。出所 = RULES.md R11・R12 と AI の案 (今の受け口の断りと同じ作り = 出荷の知らせ・検収の受け口。記録 = docs/nights/20261106/N311.md)。添付は項目だけを受け、形と大きさは今の添付と同じ決まり (attachments.services の ALLOWED・MAX_BYTES) で見る (出所 = 本人の決め CHAT-3 の「上限は今の添付と同じ 20MB」)。
    変えてよい条件 = 送る JSON の形を変える時に、両方の会社の seizou を同じ版にしてから。
    """
    # 冒頭の import の並びを変えないため、send_order と同じく関数の中で import する。
    from datetime import datetime

    from django.utils import timezone

    from .models import ChatFile, ChatMessage, Outbox

    msg_id = str(message_id)
    order_id = payload.get("order_id")
    sender_role = payload.get("sender_role")
    written_at = payload.get("written_at")
    body = payload.get("body")
    # str でない値を uuid と fromisoformat に渡さないよう、先に形を見る。
    if not all(isinstance(value, str) for value in (order_id, sender_role, written_at, body)):
        return _refuse(MESSAGE_CHAT_SHAPE, msg_id)
    files = _chat_file_items(payload.get("attachments"))
    if files is None:
        return _refuse(MESSAGE_CHAT_SHAPE, msg_id)
    if sender_role not in CHAT_ROLES or len(body) > CHAT_BODY_MAX or (not body.strip() and not files):
        return _refuse(MESSAGE_CHAT_SHAPE, msg_id)
    try:
        order_message_id = uuid.UUID(order_id)
        written = datetime.fromisoformat(written_at)
    except ValueError:
        return _refuse(MESSAGE_CHAT_SHAPE, msg_id)
    if written.tzinfo is None or written.utcoffset() is None:
        return _refuse(MESSAGE_CHAT_SHAPE, msg_id)
    author = payload.get("author")
    author = author[: ChatMessage._meta.get_field("author_name").max_length] if isinstance(author, str) else ""

    if sender_role == "buyer":
        inbox = Inbox.objects.filter(
            message_id=order_message_id, source=Inbox.SOURCE_DIRECT, revises=None
        ).first()
        if inbox is None:
            return _refuse(MESSAGE_CHAT_UNKNOWN_ORDER, msg_id)
        if inbox.partner_id != partner.pk:
            return _refuse(MESSAGE_CHAT_OTHER_PARTNER, msg_id)
        target = {"inbox": inbox}
    else:
        outbox = (
            Outbox.objects.select_related("purchase_order")
            .filter(message_id=order_message_id, revision_no=1)
            .order_by("pk")
            .first()
        )
        if outbox is None:
            return _refuse(MESSAGE_CHAT_UNKNOWN_ORDER, msg_id)
        if outbox.purchase_order.partner_id != partner.pk:
            return _refuse(MESSAGE_CHAT_OTHER_PARTNER, msg_id)
        target = {"purchase_order": outbox.purchase_order}

    # 入れ子の atomic で囲み、制約に当たっても外のトランザクションを壊さない (record_acceptance と同じ)。
    try:
        with transaction.atomic():
            chat = ChatMessage.objects.create(
                message_id=message_id,
                direction=ChatMessage.DIRECTION_RECEIVED,
                author_name=author,
                body=body,
                written_at=written,
                received_at=timezone.now(),
                **target,
            )
            for item in files:
                ChatFile.objects.create(message=chat, **item)
    except IntegrityError:
        chat = ChatMessage.objects.get(message_id=message_id, direction=ChatMessage.DIRECTION_RECEIVED)
    return ChatReceipt(message_id=message_id, chat_message=chat)


def _chat_file_items(value):
    """会話の言葉の添付の項目 (attachments) を読む。鍵が無ければ空の並び。読めなければ None (言葉ごと断る)。

    各要素は id (uuid の字)・name (1〜100 字で、拡張子が添付ファイルの形式の表にある)・size (1 から 20MB までの整数。bool は外す)・
    sha256 (16 進の 64 字) を見る。mime は読まず、自社の表 (attachments.services.ALLOWED) の値を入れる。同じ id が 2 つあれば読めない物にする。
    返す物は ChatFile の欄の辞書の並び (file_id・name・mime・size・sha256)。
    """
    from seizou.attachments import services as attachment_services

    if value is None:
        return []
    if not isinstance(value, list):
        return None
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            return None
        file_id, name, size, sha256 = raw.get("id"), raw.get("name"), raw.get("size"), raw.get("sha256")
        if not isinstance(file_id, str) or not isinstance(name, str) or not isinstance(sha256, str):
            return None
        try:
            number = uuid.UUID(file_id)
        except ValueError:
            return None
        entry = attachment_services.ALLOWED.get(attachment_services.extension_of(name))
        if entry is None or not name or len(name) > attachment_services.MAX_NAME_LENGTH:
            return None
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= attachment_services.MAX_BYTES:
            return None
        if _SHA256.fullmatch(sha256) is None:
            return None
        if any(item["file_id"] == number for item in items):
            return None
        items.append({"file_id": number, "name": name, "mime": entry[0], "size": size, "sha256": sha256})
    return items


def _receive_chat_file_request(message_id, partner, payload):
    """会話の添付を取りに来た要求に、返してよい添付かを決める。返す物は ChatFileGrant か、断る理由の文。何も残さない。

    会話の添付の取りに行く先は「送り手 (自社) の受け口 + 添付の識別 ID」の組 (中小企業共通 EDI 本編 6.4.1 (2) の URL 記載方式の項目を元にした。
    送る JSON と表に URL の字を持たない = ChatFile の docstring)。アクセス制御: 取引先との接続の鍵の署名が通り (receive が確かめる)、
    その添付が「その取引先との会話」で自社が書いた言葉の物のときだけ返す。無い添付と、ほかの取引先との会話の添付は同じ字で断る
    (有る無しをほかの取引先に教えない)。有効期限は設けない (添付は消さない。1 回の要求が通る幅は署名の時刻の ±5 分 = signing.verify)。
    取り直しは受け手の画面の「取り直す」が同じ要求を出し直す (送り手の側で何もしない)。kind = chat_file は EDI の文書の種類でない。
    訳: 取引先との接続の署名が通った相手に、その相手との会話で自社が書いた添付だけを返すため (EDI 本編 6.4.1 の「アクセス制御」)。出所 = 本人の決め (CHAT-3) と EDI 本編 6.4.1。
    変えてよい条件 = 変えない (ほかの会社の添付を返さないため)。
    """
    from .models import ChatFile, ChatMessage

    msg_id = str(message_id)
    file_id = payload.get("file_id")
    if not isinstance(file_id, str):
        return _refuse(MESSAGE_CHAT_FILE_SHAPE, msg_id)
    try:
        number = uuid.UUID(file_id)
    except ValueError:
        return _refuse(MESSAGE_CHAT_FILE_SHAPE, msg_id)
    chat_file = (
        ChatFile.objects.select_related("message__purchase_order", "message__inbox")
        .filter(file_id=number, message__direction=ChatMessage.DIRECTION_SENT)
        .first()
    )
    if chat_file is None:
        return _refuse(MESSAGE_CHAT_FILE_UNKNOWN, msg_id)
    message = chat_file.message
    owner = message.purchase_order if message.purchase_order_id is not None else message.inbox
    if owner is None or owner.partner_id != partner.pk:
        return _refuse(MESSAGE_CHAT_FILE_UNKNOWN, msg_id)
    if not chat_file.file or not chat_file.file.storage.exists(chat_file.file.name):
        return _refuse(MESSAGE_CHAT_FILE_MISSING, msg_id)
    return ChatFileGrant(message_id=message_id, chat_file=chat_file)


def record_inbox(partner, payload, user, *, message_id=None, received_at=None, source=Inbox.SOURCE_DIRECT, extraction=None, sender_name=""):
    """受け口を通さずに、受信した注文を受信箱に 1 行入れる (作り物の種と試験と MCP の試しと、見張るフォルダの読み取りのため)。

    署名の確かめ (R12) を通らないので、受け口 (receive) からは呼ばない。相手に何も送らない。
    user は記録のために受ける。同じ番号の 2 回目は 1 回目の行を返す。
    received_at を渡すと、届いた日時をその値にする (timezone 付きの datetime。作り物の種の「届いた日時」のため。渡さなければ今)。同じ番号の 2 回目は 1 回目の行をそのまま返す (届いた日時も書き直さない)。
    source は入口 (Inbox.SOURCE_CHOICES の値。既定は直接)。読み取り (intake の extract_to_inbox) は元の読み取り (extraction) と読み取った差出人の字 (sender_name。列の長さで切る) を渡す。
    partner が None (取引先の空の行 = 差出人の確認待ち) でよいのは、入口が読み取りのときだけ。ほかは日本語の ValueError。
    """
    # 冒頭の import の並びを変えないため、send_reply と同じく関数の中で import する。
    import datetime

    from django.utils import timezone

    if not isinstance(payload, dict):
        raise ValueError("注文のデータは辞書 (send_order が作る形) にしてください。")
    if received_at is not None and (
        not isinstance(received_at, datetime.datetime) or timezone.is_naive(received_at)
    ):
        raise ValueError("届いた日時は timezone 付きの日時にしてください。")
    if source not in {value for value, _label in Inbox.SOURCE_CHOICES}:
        raise ValueError(f"入口 {source!r} は使えません。直接 (direct) か読み取り (intake) にしてください。")
    if partner is None and source != Inbox.SOURCE_INTAKE:
        raise ValueError("取引先がありません。取引先の空の行は、注文書の読み取り (入口 = 読み取り) からだけ入れます。")
    # 読み取りの返事の検査 (intake の _check_reply) は差出人の字数を見ないので、列の長さで切る。
    limit = Inbox._meta.get_field("sender_name").max_length
    sender_name = sender_name.strip()[:limit] if isinstance(sender_name, str) else ""
    values = {} if received_at is None else {"received_at": received_at}
    if message_id is None:
        message_id = uuid.uuid4()
    # R11: 番号の一意の制約に当て、2 回目は 1 回目の行を返す (receive と同じ形)。
    # 入れ子の atomic で囲み、制約に当たっても外のトランザクションを壊さない。
    try:
        with transaction.atomic():
            return Inbox.objects.create(
                message_id=message_id,
                partner=partner,
                kind=Inbox.KIND_ORDER,
                payload=payload,
                source=source,
                extraction=extraction,
                sender_name=sender_name,
                **values,
            )
    except IntegrityError:
        return Inbox.objects.get(message_id=message_id)


# 受け入れと断るの日本語 (何が起きて、何をすればよいか)。payload の全文は文に入れない。
MESSAGE_ALREADY_ACCEPTED = (
    "同じ番号の注文のデータは 1 回だけ登録する (2 回届いても 1 件)。"
    "受け入れは済んでいるので、受注の一覧を見てください。"
)
MESSAGE_ACCEPT_REJECTED = (
    "この注文のデータは断り済みです。"
    "受け入れるなら、送る側に新しい注文のデータを送ってもらってください。"
)
MESSAGE_NO_LINES = (
    "注文のデータに明細がありません。この注文は断り、送る側に送り直してもらってください。"
)
MESSAGE_NO_REASON = "断る理由がありません。理由を書いてから断ってください。"
MESSAGE_REPLY_SEED = "作り物の種の読み込みなので注文回答を送っていません (相手の記録は相手の種が持つ)。"
MESSAGE_NO_SENDER = "差出人の取引先が決まっていません。受信した注文の詳細の「差出人を選ぶ」で得意先を選んでから受け入れてください。"
MESSAGE_REPLY_INTAKE = "注文書の読み取りから入った注文なので、注文回答は送りません。"
MESSAGE_SENDER_DIRECT = "受け口から受信した注文の取引先は署名で決まります (R12)。"


def accept(inbox, item_mapping, user, *, send=True, accepted_at=None):
    """受信箱の注文のデータを受け入れて、受注 (入口 = 受信箱) を作る (R10・R11)。

    item_mapping は相手の品番の字 → 自社の Item。無い品番は部品の取引先の表 (masters) で引く。
    item_mapping で選んだ品番が表に無ければ、その場で表に対応を足す。
    対応の追加・受注の作成・受信箱の状態の更新は 1 つのトランザクションで行う (どれかが失敗したら全部戻る)。
    受け入れ済みの 2 回目は RuleViolation("R11")、断った物と形の違う物は日本語の ValueError。
    得意先の役でない取引先は create_order の ValueError がそのまま出る。返す物は作った Order。
    send が偽なら注文回答を送らず (送る口を呼ばない)、番号も付けず、返事の欄に MESSAGE_REPLY_SEED を書く (作り物の種の読み込みのため)。
    取引先の空の行 (差出人の確認待ち) は、表に書く前に MESSAGE_NO_SENDER の ValueError で断る (choose_sender で選んでから)。
    読み取りの行 (入口 = 読み取り) は、単価の無い明細を品目の標準単価に、注文日が無ければ届いた日にし、受注の入口を「読み取り」にする。
    承認した日時 (accepted_at) を状態と受注と同じトランザクションで書く。
    accepted_at を渡すと、承認した日時をその値にする (timezone 付きの datetime。作り物の種の「承認した日時」のため。渡さなければ今)。timezone の無い日時と日時でない値は、状態を見る前に日本語の ValueError。
    明細の process_name と quote_no は、受注の明細の工程名と見積の番号に写す (字でなければ空。C100)。
    訳: 受信した注文を人が受け入れたときだけ受注にし (R10)、同じ注文を 2 回受け入れても受注を 1 件にするため (R11。2 回届いても 1 件)。出所 = RULES.md R10・R11。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    return _accept(inbox, item_mapping, user, send=send, accepted_at=accepted_at)


#: 単価を直して受け入れたときの知らせ (入力の土台が「保存しました。」の代わりに出す)
MESSAGE_CONDITIONAL_SAVED = "単価を直して受け入れました。直した値で受注を作り、相手に条件付きの答えを返しました。相手が発注を直すのを待ちます。"
#: 単価を直して受け入れるで、どの値も直していなかったときの知らせ
MESSAGE_CONDITIONAL_UNCHANGED = "どの値も直していないので、受け入れるとして相手に返しました。"
#: 保留したときの知らせ
MESSAGE_HOLD_SAVED = "保留にしました。答えが決まったら、受信した注文の詳細から受け入れるか断ってください。"


def _restart_reply(locked):
    """前に保留 (AJ) を答えた行なら、注文回答の番号・送った日時・相手の返事を空に戻し、戻した列の名前の並びを返す。

    送り直しは同じ番号を使い回す (send_reply) が、保留の後の答えは別の知らせなので新しい番号にする
    (同じ番号は相手の受け口が 2 回目と見なして当てない = R11)。まだ答えていない行は何もせず空の並びを返す。
    """
    if not locked.reply_type:
        return []
    locked.reply_message_id = None
    locked.reply_sent_at = None
    locked.reply_response = ""
    return ["reply_message_id", "reply_sent_at", "reply_response"]


def _answer_payload(answers, count):
    """単価を直して受け入れるの答えを、_order_lines_problem が読む注文の形 ({"lines": [{"qty", "unit_price"}, …]}) にする。"""
    lines = []
    for number in range(1, count + 1):
        answer = answers.get(number) or {}
        row = {}
        for key in ("qty", "unit_price"):
            if answer.get(key) is not None:
                row[key] = format(answer[key], "f")
        lines.append(row)
    return {"lines": lines}


def _number_text(value):
    """注文回答の明細に書く数の字 (指数の形にしない)。"""
    return format(value, "f")


def _accept(inbox, item_mapping, user, *, answers=None, send=True, accepted_at=None):
    """受信した注文を受け入れて受注を作る (accept と accept_with_conditions の中身)。返す物は作った Order。

    answers が None なら受諾 (AP)。answers があれば、明細ごとの回答単価・回答数量・回答納期で受注の明細を作り、
    相手の注文と違う明細があれば条件付受諾 (CA) と明細ごとの答え、どの明細も同じなら受諾 (AP) で返す。
    前に保留 (AJ) を答えた行は、注文回答の番号と送った日時と返事を空に戻してから答えを書く (_restart_reply)。
    訳: 受信した注文を人が受け入れたときだけ受注にし (R10)、同じ注文を 2 回受け入れても受注を 1 件にするため (R11。2 回届いても 1 件)。出所 = RULES.md R10・R11。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    # 冒頭の import の並びを変えないため、send_reply と同じく関数の中で import する。
    import datetime

    from django.utils import timezone

    if accepted_at is not None and (
        not isinstance(accepted_at, datetime.datetime) or timezone.is_naive(accepted_at)
    ):
        raise ValueError("受け入れられません。承認した日時は timezone 付きの日時にしてください。")
    # 変更と取消の版 (元の行 revises がある行) は、受注を作らず元の受注を変える。
    if inbox.revises_id is not None:
        return _accept_revision(inbox, item_mapping, user, send=send, accepted_at=accepted_at)

    with transaction.atomic():
        locked = Inbox.objects.select_for_update().get(pk=inbox.pk)
        if locked.status == Inbox.STATUS_ACCEPTED:
            raise RuleViolation("R11", "受け入れられません。" + MESSAGE_ALREADY_ACCEPTED)
        if locked.status == Inbox.STATUS_REJECTED:
            raise ValueError("受け入れられません。" + MESSAGE_ACCEPT_REJECTED)
        if locked.partner_id is None:
            raise ValueError("受け入れられません。" + MESSAGE_NO_SENDER)
        intake_row = locked.source == Inbox.SOURCE_INTAKE

        payload = locked.payload if isinstance(locked.payload, dict) else {}
        raw_lines = payload.get("lines")
        if not isinstance(raw_lines, list) or not raw_lines:
            raise ValueError("受け入れられません。" + MESSAGE_NO_LINES)
        problem = _order_lines_problem(payload)
        if problem:
            raise ValueError(f"受け入れられません。{problem}この注文は断り、送る側に送り直してもらってください。")
        # 受け口を通らない行 (record_inbox の道) の字も、受注と取引先の品番の表に書く前に止める (数の断りの次)。
        problem = _order_text_problem(payload)
        if problem:
            raise ValueError(f"受け入れられません。{problem}この注文は断り、送る側に送り直してもらってください。")
        if answers is not None:
            # 直した値も受注の明細の表の欄に入るかを、受信した注文の数と同じ検査で見る (0 以下は create_order が R02 で止める)。
            problem = _order_lines_problem(_answer_payload(answers, len(raw_lines)), head="直した値の")
            if problem:
                raise ValueError(f"受け入れられません。{problem}直した値を確かめてください。")
        mapping = dict(item_mapping or {})
        items = {}
        lines = []
        reply_lines = []
        for number, raw in enumerate(raw_lines, start=1):
            part_no, part_name = _part_of(raw, number)
            if part_no not in items:
                items[part_no] = _item_for(locked.partner, part_no, part_name, mapping, user)
            item = items[part_no]
            unit = raw.get("unit")
            note = raw.get("note")
            # 工程名と見積の番号 (C100。どちらも任意の項目。字でなければ空の字にし、断らない)
            process_name = raw.get("process_name")
            quote_no = raw.get("quote_no")
            if intake_row and raw.get("unit_price") is None:
                # 読み取りの単価の無い明細は品目の標準単価 (intake の _unit_price と同じ扱い。intake の関数は呼ばない)。
                if not item.standard_price:
                    raise ValueError(
                        f"受け入れられません。品番 {part_no} の単価が、注文書にも部品マスターの標準単価にもありません。"
                        "部品マスターに標準単価を入れてから受け入れてください。"
                    )
                unit_price = item.standard_price
            else:
                unit_price = _payload_decimal(raw.get("unit_price"), f" {number} 行目の単価")
            qty = _payload_decimal(raw.get("qty"), f" {number} 行目の数量")
            due_date = _payload_date(raw.get("due_date"), f" {number} 行目の納期")
            if answers is not None:
                answer = answers.get(number) or {}
                new_price = unit_price if answer.get("unit_price") is None else answer["unit_price"]
                new_qty = qty if answer.get("qty") is None else answer["qty"]
                new_due = due_date if answer.get("due_date") is None else answer["due_date"]
                line_no = raw.get("line_no")
                line_no = line_no if isinstance(line_no, int) and not isinstance(line_no, bool) else number
                if (new_price, new_qty, new_due) != (unit_price, qty, due_date):
                    reply_lines.append(
                        {
                            "line_no": line_no,
                            "reply_type": edi_codes.REPLY_CONDITIONAL,
                            "reply_unit_price": _number_text(new_price),
                            "reply_qty": _number_text(new_qty),
                            "reply_due_date": new_due.isoformat() if new_due else None,
                        }
                    )
                else:
                    reply_lines.append({"line_no": line_no, "reply_type": edi_codes.REPLY_ACCEPTED})
                unit_price, qty, due_date = new_price, new_qty, new_due
            lines.append(
                {
                    "item": item,
                    "qty": qty,
                    "unit": unit if isinstance(unit, str) and unit else item.unit,
                    "unit_price": unit_price,
                    "price_basis": _payload_price_basis(raw.get("price_basis"), f" {number} 行目の単価の単位"),
                    "due_date": due_date,
                    "note": note if isinstance(note, str) else "",
                    "customer_part_no": part_no,
                    "process_name": process_name if isinstance(process_name, str) else "",
                    "quote_no": quote_no if isinstance(quote_no, str) else "",
                }
            )

        order_date = _payload_date(payload.get("order_date"), "発注日")
        if order_date is None and intake_row:
            # 読み取りの行で注文日が無ければ届いた日 (intake の accept_extracted の「読んだ日」と同じ扱い)。
            order_date = timezone.localtime(locked.received_at).date()
        if order_date is None:
            raise ValueError(
                "受け入れられません。注文のデータに発注日がありません。この注文は断り、送る側に送り直してもらってください。"
            )
        order_no = payload.get("order_no")
        order = create_order(
            locked.partner,
            lines,
            user,
            order_date=order_date,
            customer_order_no=order_no if isinstance(order_no, str) else "",
            source="intake" if intake_row else "inbox",
        )
        conditional = any(row["reply_type"] == edi_codes.REPLY_CONDITIONAL for row in reply_lines)
        fields = ["status", "order", "accepted_at", "reply_type", "reply_lines", *_restart_reply(locked)]
        locked.status = Inbox.STATUS_ACCEPTED
        locked.order = order
        locked.accepted_at = timezone.now() if accepted_at is None else accepted_at
        locked.reply_type = edi_codes.REPLY_CONDITIONAL if conditional else edi_codes.REPLY_ACCEPTED
        locked.reply_lines = reply_lines if conditional else []
        if not send:
            locked.reply_response = MESSAGE_REPLY_SEED
            fields = [*dict.fromkeys([*fields, "reply_response"])]
        locked.save(update_fields=fields)

    # 注文回答を相手へ返す。HTTP は atomic の外 (R17)。届かなくても例外にしない (受け入れは成立する)。
    if send:
        send_reply(locked, user)
    if answers is not None:
        order.saved_message = MESSAGE_CONDITIONAL_SAVED if conditional else MESSAGE_CONDITIONAL_UNCHANGED
    return order


def accept_with_conditions(inbox, item_mapping, answers, user, *, send=True):
    """受信した注文の単価・数量・納期を明細ごとに直して受け入れ、直した値で受注を作る。返す物は作った Order。

    中小企業共通 EDI の条件付受諾の項目を元にした (ガイドブック 3.2.2。回答納期・回答数量・回答単価を返し、発注者が合わせて注文を変更する)。
    回答した値で受注を作る。R10 (人が受け入れるまで受注にならない) と R11 は accept と同じ。
    answers は {明細の並びの番号 (1 から): {"unit_price": Decimal か None, "qty": Decimal, "due_date": date か None}}。
    unit_price が None (金額の閲覧の無い人の form) なら相手の単価のまま、due_date が None なら注文の納期のまま。
    相手の注文と違う明細は条件付受諾 (CA) と回答単価・回答数量・回答納期の 3 つ、同じ明細は受諾 (AP) で返す。
    どの値も直していなければ受諾として返す (条件付のまま返すと、相手の発注が違う値の無い条件付になり、何を直すか分からないため。決め C97-6 (11-07 06:46))。
    返す受注の saved_message に、条件付で返したか受諾で返したかの知らせの字を付ける。
    訳: 相手の注文の単価・数量・納期のままでは受けられないときに、直した値で受注を作って相手に返すため。出所 = 決め PRICE-4e (11-07 00:0x)。
    変えてよい条件 = 条件付受諾の範囲の決めが変わったとき。
    """
    return _accept(inbox, item_mapping, user, answers=answers or {}, send=send)


def hold(inbox, user, *, send=True):
    """受信した注文に保留 (AJ) と答え、相手へ返す。受注は作らず、状態は「未処理」のまま。返す物は Inbox。

    中小企業共通 EDI の保留 (AJ) の項目を元にした。受注を作らない。
    答え直した時に前の答えの中身を残すのは変更の履歴の仕組みの仕事 (電子帳簿保存法。EDI の取引データを全部残す = ガイドブック 3.2.2)。
    保留を 2 回押しても断らない (2 回目は新しい知らせとして、新しい番号で送る = _restart_reply)。
    送るのは atomic の外 (R17)。返す受信した注文の saved_message に、保留にした知らせの字を付ける。
    訳: すぐ答えられない注文に、受注を作らずに保留と相手へ知らせるため。「未処理」の物だけを保留にする (受け入れた・断った注文に保留を返すと記録が食い違うため)。出所 = 決め PRICE-4e (11-07 00:0x)。
    変えてよい条件 = 保留の扱いの決めが変わったとき。
    """
    with transaction.atomic():
        locked = Inbox.objects.select_for_update().get(pk=inbox.pk)
        if locked.status != Inbox.STATUS_RECEIVED:
            raise ValueError(
                f"保留できません。この注文のデータは「{locked.get_status_display()}」です。"
                "保留できるのは「未処理」の物だけです。"
            )
        fields = ["reply_type", "reply_lines", *_restart_reply(locked)]
        locked.reply_type = edi_codes.REPLY_PENDING
        locked.reply_lines = []
        locked.save(update_fields=fields)

    # 注文回答を相手へ返す。HTTP は atomic の外 (R17)。届かなくても例外にしない (保留は成立する)。
    saved = send_reply(locked, user) if send else locked
    saved.saved_message = MESSAGE_HOLD_SAVED
    return saved


def _accept_revision(inbox, item_mapping, user, *, send=True, accepted_at=None):
    """変更と取消の版 (元の行 revises がある受信した注文) を受け入れ、元の行の受注を変える。返す物は変えた受注 (保存の後は受注の詳細へ移る)。

    状態の見方は accept と同じ (受け入れ済みは R11・断った物は ValueError)。元の行が未処理なら、先に第 1 版を受け入れるよう断る。
    当てられない版 (_revision_problem) は、受け口で受けた後に出荷が進んだときのため、受け入れる時にもう一度見て断る。
    変更 (目的 "4") は、注文の明細を行番号で受注の明細に当てる: 取り消した明細 (2) は受注から消し、ほかの明細は数・単価・納期・備考を注文の値にし、
    受注に無い行番号は足す (行番号は相手の番号のまま。出荷の知らせと検収は行番号で明細を指す)。注文に無い受注の明細 (受ける側が足した行) は今のまま。
    受注を変えるのは orders.services.update_order。取消 (目的 "1") は orders.services.cancel_order。行を「受け入れた」にし (受注 order は空のまま)、
    atomic の外で注文回答を返す。send が偽なら注文回答を送らず、返事の欄に MESSAGE_REPLY_SEED を書く (accept と同じ)。
    訳: 受信した注文を人が受け入れたときだけ受注を変え (R10)、同じ版を 2 回受け入れないため (R11)。受ける側が手で直した明細も注文の値で上書きするのは、中小企業共通 EDI の注文変更 (方式 2) が変更の後の注文の全体を送るため。出所 = RULES.md R10・R11 と決め C96-3 (11-07 06:35。受け入れる前の画面で受注と違う値を全部出す)。
    変えてよい条件 = C96-3 の答えが変わったとき (R10・R11 は変えない)。
    """
    from django.utils import timezone

    with transaction.atomic():
        locked = Inbox.objects.select_for_update().get(pk=inbox.pk)
        if locked.status == Inbox.STATUS_ACCEPTED:
            raise RuleViolation("R11", "受け入れられません。" + MESSAGE_ALREADY_ACCEPTED)
        if locked.status == Inbox.STATUS_REJECTED:
            raise ValueError("受け入れられません。" + MESSAGE_ACCEPT_REJECTED)
        base = Inbox.objects.select_for_update().select_related("order").get(pk=locked.revises_id)
        if base.status == Inbox.STATUS_RECEIVED:
            raise ValueError(f"受け入れられません。先に第 1 版の注文 (受信した注文 {base.pk}) を受け入れてください。")
        payload = locked.payload if isinstance(locked.payload, dict) else {}
        problem = _revision_problem(base, payload, locked.revision_no, locked.purpose_code)
        if problem:
            raise ValueError(f"受け入れられません。{problem}この変更は断ってください。")
        if locked.purpose_code == edi_codes.PURPOSE_CANCEL:
            order = cancel_order(base.order, user, reason=f"得意先の注文の取消 (注文履歴番号 {locked.revision_no})")
        else:
            order = update_order(base.order, {}, _revision_lines(base, payload, item_mapping, user), user)
        locked.status = Inbox.STATUS_ACCEPTED
        locked.accepted_at = timezone.now() if accepted_at is None else accepted_at
        fields = ["status", "accepted_at"]
        if not send:
            locked.reply_response = MESSAGE_REPLY_SEED
            fields.append("reply_response")
        locked.save(update_fields=fields)

    # 注文回答を相手へ返す。HTTP は atomic の外 (R17)。届かなくても例外にしない (受け入れは成立する)。
    if send:
        send_reply(locked, user)
    return order


def _revision_lines(base, payload, item_mapping, user):
    """変更の版の注文を受注の明細に当てる update_order の lines (受注の明細の全部と、足す明細)。

    受注の明細は行番号の順に、注文にあって 2 の行は渡さず (消す)、2 でない行は数・単価・納期・備考を注文の値で、注文に無い行は今の値のまま渡す。
    注文にあって受注に無い行番号の明細 (2 を除く) は、部品を第 1 版の受け入れと同じ _item_for で引き、相手の行番号 (line_no) で足す。
    """
    mapping = dict(item_mapping or {})
    raw_lines = [raw for raw in payload.get("lines") or () if isinstance(raw, dict)]
    by_number = {_line_number(raw): raw for raw in raw_lines}
    rows = list(base.order.lines.order_by("line_no", "pk"))
    lines = []
    for row in rows:
        raw = by_number.get(row.line_no)
        if raw is None:
            lines.append({"order_line": row})
        elif not _is_deleted(raw):
            lines.append({"order_line": row, **_revision_values(raw, row.line_no)})
    known = {row.line_no for row in rows}
    items = {}
    for raw in raw_lines:
        number = _line_number(raw)
        if number in known or _is_deleted(raw):
            continue
        part_no, part_name = _part_of(raw, number)
        if part_no not in items:
            items[part_no] = _item_for(base.partner, part_no, part_name, mapping, user)
        item = items[part_no]
        unit = raw.get("unit")
        lines.append(
            {
                "item": item,
                "unit": unit if isinstance(unit, str) and unit else item.unit,
                "customer_part_no": part_no,
                "line_no": number,
                **_revision_values(raw, number),
            }
        )
    return lines


def _revision_values(raw, number):
    """注文の明細 1 行の数・単価・納期・備考 (受注に当てる値)。読めない数と日付は日本語の ValueError。"""
    note = raw.get("note")
    return {
        "qty": _payload_decimal(raw.get("qty"), f" {number} 行目の数量"),
        "unit_price": _payload_decimal(raw.get("unit_price"), f" {number} 行目の単価"),
        "due_date": _payload_date(raw.get("due_date"), f" {number} 行目の納期"),
        "note": note if isinstance(note, str) else "",
    }


def reject(inbox, reason, user):
    """受信箱の注文のデータを、理由を書いて断る。断れるのは状態が「届いた」の物だけ。返す物は Inbox。"""
    with transaction.atomic():
        locked = Inbox.objects.select_for_update().get(pk=inbox.pk)
        if locked.status != Inbox.STATUS_RECEIVED:
            raise ValueError(
                f"断れません。この注文のデータは「{locked.get_status_display()}」です。"
                "断れるのは「未処理」の物だけです。"
            )
        text = reason.strip() if isinstance(reason, str) else ""
        if not text:
            raise ValueError("断れません。" + MESSAGE_NO_REASON)
        limit = Inbox._meta.get_field("reason").max_length
        if len(text) > limit:
            raise ValueError(f"断れません。断る理由が {limit} 字を超えています。{limit} 字までに縮めてください。")
        fields = ["status", "reason", "reply_type", "reply_lines", *_restart_reply(locked)]
        locked.status = Inbox.STATUS_REJECTED
        locked.reason = text
        locked.reply_type = edi_codes.REPLY_REJECTED
        locked.reply_lines = []
        locked.save(update_fields=fields)

    # 注文回答を相手へ返す。HTTP は atomic の外 (R17)。届かなくても例外にしない (断りは成立する)。
    send_reply(locked, user)
    return locked


def choose_sender(inbox, partner, user):
    """差出人の確認待ちの受信した注文 (入口 = 読み取り) に、差出人の得意先を選ぶ。返す物は Inbox。

    選べるのは入口が読み取りで状態が「届いた」の行だけ。取引先が当たった行の付け直しもできる (名前の当て違いを直すため)。
    入口が直接の行は署名で取引先が決まるので断る (R12)。得意先でない取引先も断る。誤りは日本語の ValueError。
    user は記録のために受ける。
    """
    with transaction.atomic():
        locked = Inbox.objects.select_for_update().get(pk=inbox.pk)
        if locked.source == Inbox.SOURCE_DIRECT:
            raise ValueError("保存できません。" + MESSAGE_SENDER_DIRECT)
        if locked.status != Inbox.STATUS_RECEIVED:
            raise ValueError("保存できません。差出人を選べるのは「未処理」の注文だけです。")
        if partner is None:
            raise ValueError("保存できません。差出人の取引先を選んでください。")
        if not partner.is_customer:
            raise ValueError(
                f"保存できません。取引先 {partner} は得意先ではありません。取引先の種類が得意先か両方の取引先を選んでください。"
            )
        locked.partner = partner
        locked.save(update_fields=["partner"])
    logger.info("差出人を選んだ: 受信した注文 %s 取引先 %s", locked.pk, partner.code)
    return locked


MESSAGE_REPLY_BEFORE_DECISION = (
    "この受信した注文は、まだ受け入れも断りもしていません (保留とも答えていません)。"
    "受け入れる・単価を直して受け入れる・保留する・断るのどれかで答えてから送ってください。"
)
MESSAGE_REPLY_NO_CONNECTION = (
    "取引先との接続 (送る向き) が無いので注文回答を送っていません。"
    "設定の画面の「取引先との接続」で、この取引先に送る向きの接続を登録してください。"
)


def send_reply(inbox, user):
    """4 つの答え (受諾・条件付受諾・辞退・保留) を注文回答にし、署名して、注文を送ってきた相手の受け口へ送る。

    - 送れるのは、種類が注文で、受け入れたか断った後か、保留を答えた行だけ。それ以外は日本語の ValueError。
    - 答えは受信した注文の reply_type (EDI の注文回答タイプコードの値) を送る。条件付受諾 (CA) は明細の答え (reply_lines) も送る。
    - 番号は 1 回目に付け、送り直すときに使い回す (相手は同じ番号を 1 件にまとめる = R11)。
    - つながりは受信した注文の取引先の、向き「送る」の物。無ければ HTTP を送らず、返事の欄に書いて返す。
    - 読み取りの行 (入口 = 読み取り) は相手へ送らず、返事の欄に MESSAGE_REPLY_INTAKE を書く。
    - 相手への HTTP は transaction.atomic の外 (R17)。送る所は send_order と同じ _deliver。
    - 届かない・断られた・鍵が無い・つながりが無いのどれでも例外にしない (承認と断りは成立する)。
      返事は reply_response に書き、相手が受け取ったときだけ reply_sent_at を書く。
    - 鍵の値・本文・見出しの値をログと返す物に出さない。返す物は取り直した Inbox。
    """
    # 冒頭の import の並びを変えないため、send_order と同じく関数の中で import する。
    from django.utils import timezone

    with transaction.atomic():
        locked = Inbox.objects.select_for_update().get(pk=inbox.pk)
        pending = locked.status == Inbox.STATUS_RECEIVED and locked.reply_type == edi_codes.REPLY_PENDING
        if locked.kind != Inbox.KIND_ORDER or (locked.status == Inbox.STATUS_RECEIVED and not pending):
            raise ValueError("送れません。" + MESSAGE_REPLY_BEFORE_DECISION)
        if locked.reply_message_id is None:
            locked.reply_message_id = uuid.uuid4()
            locked.save(update_fields=["reply_message_id"])
    message_id = locked.reply_message_id

    # 読み取りの行は相手の seizou とつながっていないので、つながりを引かず HTTP を送らない。
    connection = None
    if locked.source != Inbox.SOURCE_INTAKE:
        connection = _send_connection(locked.partner)
    if locked.source == Inbox.SOURCE_INTAKE:
        reply, kind = MESSAGE_REPLY_INTAKE, "intake"
    elif connection is None:
        reply, kind = MESSAGE_REPLY_NO_CONNECTION, "no_connection"
    else:
        accepted = locked.status == Inbox.STATUS_ACCEPTED
        # 答えのタイプは表の値。空 (答えのタイプを持つ前の行) なら状態から読む (受け入れた = 受諾・断った = 辞退)。
        reply_type = locked.reply_type or (edi_codes.REPLY_ACCEPTED if accepted else edi_codes.REPLY_REJECTED)
        order_data = locked.payload if isinstance(locked.payload, dict) else {}
        order_no = order_data.get("order_no")
        # 変更の版の行は受注を持たない (第 1 版の行の受注を変える) ので、受注番号は元の行の受注から引く。
        order = locked.order if locked.order_id else (locked.revises.order if locked.revises_id else None)
        reply_data = {
            "id": str(message_id),
            "kind": "order_reply",
            "sent_at": timezone.localtime().isoformat(),
            "order_id": str(locked.message_id),
            "order_no": order_no if isinstance(order_no, str) else "",
            "order_revision_no": locked.revision_no,
            "reply_type": reply_type,
            "lines": locked.reply_lines if reply_type == edi_codes.REPLY_CONDITIONAL else [],
            "supplier_order_no": order.order_no if accepted and order else "",
            "reason": locked.reason if reply_type == edi_codes.REPLY_REJECTED else "",
            "supplier": _party(),
        }
        raw = _encode(reply_data)
        # R17: ここは atomic の外。
        reply, kind = _deliver(connection, message_id, raw)

    with transaction.atomic():
        saved = Inbox.objects.select_for_update().get(pk=locked.pk)
        saved.reply_response = reply[: Inbox._meta.get_field("reply_response").max_length]
        fields = ["reply_response"]
        if kind == "received":
            saved.reply_sent_at = timezone.now()
            fields.append("reply_sent_at")
        saved.save(update_fields=fields)

    logger.info("注文回答: 受信した注文 %s 番号 %s 返事の種類 %s", saved.pk, message_id, kind)
    return saved


def send_acceptance(receipt, user):
    """受入を「検収」にし、署名して、注文を送った相手 (協力企業の seizou) の受け口へ送る。

    - 送るのは、送った注文 (Outbox) の行がある発注の受入だけ。無ければ HTTP を送らず、返事の欄に書いて返す。
    - 番号は送る前に保存し (届かなくても残る)、送り直すときに使い回す (相手は同じ番号を 1 件にまとめる = R11)。
    - つながりは発注の取引先の、向き「送る」の物 (注文と同じ向き)。無ければ HTTP を送らず、返事の欄に書いて返す。
    - 相手への HTTP は transaction.atomic の外 (R17)。送る所は send_order・send_reply と同じ _deliver。
    - 届かない・断られた・鍵が無い・つながりが無いのどれでも例外にしない (受入は成立する)。
      返事は受入の acceptance_response に、相手が受け取ったときだけ acceptance_sent_at に書く。
      受入の列は purchasing.services の assign_acceptance_number・record_acceptance_response が書く。
    - 鍵の値・本文・見出しの値をログと返す物に出さない。返す物は取り直した Receipt。
    """
    # connect と purchasing の循環を避け、冒頭の import の並びを変えないため、関数の中で import する。
    from django.apps import apps
    from django.utils import timezone

    from seizou.purchasing import services as purchasing_services

    from .models import Outbox

    # 数量と受入日は表の値で送る (受入を作った直後の物は、数量が渡した桁のままのため)。
    receipt_model = apps.get_model("purchasing", "Receipt")
    current = receipt_model.objects.select_related("line__purchase_order__partner").get(pk=receipt.pk)
    line = current.line
    purchase_order = line.purchase_order

    # 元の注文の番号は第 1 版の番号 (相手の受信した注文の第 1 版の行を引く鍵)。版は発注の今の版。
    first = Outbox.objects.filter(purchase_order=purchase_order, revision_no=1).order_by("pk").first()
    if first is None:
        return purchasing_services.record_acceptance_response(
            current, MESSAGE_ACCEPTANCE_NOT_SENT_ORDER, user
        )
    message_id = purchasing_services.assign_acceptance_number(current, user)

    connection = _send_connection(purchase_order.partner)
    if connection is None:
        return purchasing_services.record_acceptance_response(
            current, MESSAGE_ACCEPTANCE_NO_CONNECTION, user
        )

    acceptance_data = {
        "id": str(message_id),
        "kind": "acceptance",
        "sent_at": timezone.localtime().isoformat(),
        "order_id": str(first.message_id),
        "order_no": purchase_order.po_no,
        "order_revision_no": purchase_order.revision_no,
        "line_no": line.line_no,
        "accepted_on": current.received_on.isoformat(),
        "qty": str(current.qty),
        "buyer": _party(),
    }
    raw = _encode(acceptance_data)
    # R17: ここは atomic の外。送る所は send_order・send_reply と同じ _deliver。
    reply, kind = _deliver(connection, message_id, raw)

    saved = purchasing_services.record_acceptance_response(current, reply, user, sent=(kind == "received"))
    logger.info("検収を送った: 受入 %s 番号 %s 返事の種類 %s", current.pk, message_id, kind)
    return saved


def send_shipping_notice(shipment, user):
    """出荷を「出荷の知らせ」にし、署名して、注文を送ってきた相手 (得意先の seizou) の受け口へ送る。

    - 送るのは、受信した注文 (Inbox) で作った受注の出荷だけ。それ以外は HTTP を送らず、返事の欄に書いて返す。
    - 番号は送る前に保存し (届かなくても残る)、送り直すときに使い回す (相手は同じ番号を 1 件にまとめる = R11)。
    - つながりは受信した注文の取引先の、向き「送る」の物 (注文回答と同じ向き)。無ければ HTTP を送らず、返事の欄に書いて返す。
    - 相手への HTTP は transaction.atomic の外 (R17)。送る所は send_order・send_reply・send_acceptance と同じ _deliver。
    - 届かない・断られた・鍵が無い・つながりが無いのどれでも例外にしない (出荷は成立する)。
      返事は出荷の notice_response に、相手が受け取ったときだけ notice_sent_at に書く。
      出荷の列は shipping.services の assign_notice_number・record_notice_response が書く。
    - 鍵の値・本文・見出しの値をログと返す物に出さない。返す物は取り直した Shipment。
    """
    # connect と shipping の循環を避け、冒頭の import の並びを変えないため、関数の中で import する。
    from django.apps import apps
    from django.utils import timezone

    from seizou.shipping import services as shipping_services

    # 数量と出荷日は表の値で送る (出荷を作った直後の物は、数量が渡した桁のままのため。send_acceptance と同じ)。
    shipment_model = apps.get_model("shipping", "Shipment")
    current = shipment_model.objects.select_related("order_line__order").get(pk=shipment.pk)
    line = current.order_line
    order = line.order

    # 受信した注文は受注の 1 対 1 の逆の関係。無ければ RelatedObjectDoesNotExist (AttributeError の子) で None。
    inbox = getattr(order, "inbox_message", None)
    # 読み取りの行から作った受注は、相手の seizou とつながっていないので送らない (出荷の詳細の送り直しもここを通る)。
    if inbox is None or inbox.source != Inbox.SOURCE_DIRECT:
        return shipping_services.record_notice_response(current, MESSAGE_SHIPPING_NOT_EDI_ORDER, user)
    message_id = shipping_services.assign_notice_number(current, user)

    connection = (
        Connection.objects.filter(partner_id=inbox.partner_id, direction=Connection.DIRECTION_SEND)
        .order_by("pk")
        .first()
    )
    if connection is None:
        return shipping_services.record_notice_response(current, MESSAGE_SHIPPING_NO_CONNECTION, user)

    order_data = inbox.payload if isinstance(inbox.payload, dict) else {}
    order_no = order_data.get("order_no")
    notice_data = {
        "id": str(message_id),
        "kind": "shipping_notice",
        "sent_at": timezone.localtime().isoformat(),
        "order_id": str(inbox.message_id),
        "order_no": order_no if isinstance(order_no, str) else "",
        "order_revision_no": inbox.current_revision_no(),
        "line_no": line.line_no,
        "supplier_order_no": order.order_no,
        "shipment_no": current.shipment_no,
        "shipped_at": current.shipped_at.isoformat(),
        "qty": str(current.qty),
        "supplier": _party(),
    }
    raw = _encode(notice_data)
    # R17: ここは atomic の外。送る所は send_order・send_reply・send_acceptance と同じ _deliver。
    reply, kind = _deliver(connection, message_id, raw)

    saved = shipping_services.record_notice_response(current, reply, user, sent=(kind == "received"))
    logger.info("出荷の知らせを送った: 出荷 %s 番号 %s 返事の種類 %s", current.pk, message_id, kind)
    return saved


def _part_of(raw, number):
    """明細 1 行の相手の品番と品名。形が違えば日本語の ValueError。"""
    part_no = raw.get("part_no") if isinstance(raw, dict) else None
    if not isinstance(part_no, str) or not part_no.strip():
        raise ValueError(
            f"受け入れられません。注文のデータの {number} 行目に相手の品番がありません。"
            "この注文は断り、送る側に送り直してもらってください。"
        )
    part_name = raw.get("part_name")
    return part_no.strip(), part_name if isinstance(part_name, str) else ""


def _item_for(partner, part_no, part_name, item_mapping, user):
    """相手の品番に対応する自社の品目。選んだ品目が表に無い対応なら、表に足す。"""
    known = resolve_partner_item(partner, part_no)
    chosen = item_mapping.get(part_no)
    if chosen is None:
        if known is None:
            raise ValueError(f"受け入れられません。品番 {part_no} の自社の部品を選んでください。")
        return known
    if known is None:
        map_partner_item(partner, chosen, part_no, user, partner_item_name=part_name)
    return chosen


def _payload_decimal(value, label):
    """payload の数の字を Decimal に直す (float を通さない)。読めなければ日本語の ValueError。"""
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            number = None
        if number is not None and number.is_finite():
            return number
    raise ValueError(
        f"注文のデータの{label}が数の形ではありません。この注文は断り、送る側に送り直してもらってください。"
    )


def _payload_price_basis(value, label):
    """payload の単価の単位を、部品の単位 (unit) か式 (lump) にして返す。鍵が無い・null・空の字は部品の単位。ほかは日本語の ValueError。
    読めない値を部品の単位として受けると、式のつもりの明細の金額が数量倍になる。

    訳: 単価の単位を送らない相手 (前の版の seizou) の注文を今と同じ意味で受け、読めない値の注文は数量・単価・納期と同じく断って送り直してもらうため。出所 = 作者の決め PRICE-1b と、今の受け入れの決まり (_payload_decimal・_payload_date)。
    変えてよい条件 = 単価の単位の種類を足すとき。
    """
    if value is None or value == "":
        return PRICE_BASIS_UNIT
    if isinstance(value, str) and value in PRICE_BASIS_VALUES:
        return value
    raise ValueError(
        f"注文のデータの{label}が「部品の単位」(unit) か「式」(lump) ではありません。この注文は断り、送る側に送り直してもらってください。"
    )


def _fits_field(number, field):
    """number (Decimal) が 0 より大きく、表の欄 field (DecimalField) の桁に入るか。

    整数の部分は max_digits − decimal_places 桁まで、小数は decimal_places 桁まで (末尾の 0 は数えない = 10.000 は通す)。
    is_finite() だけでは 1e20 と 0.001 が通り、表に入れた後で読むと 500 になる (精査 A1 の不具合 1・2・3・5)。
    訳: 受信した注文の数が表の欄の桁に入るかを見て、桁を超える数を表に入れた後で読むと 500 になるのを防ぐため。出所 = AI の案 (docs/nights/20261103/N74.md。精査 A1 の不具合 1・2・3・5)。
    変えてよい条件 = Issue「決まりの提案」でこの訳に対して議論してから。
    """
    if not isinstance(number, Decimal) or not number.is_finite() or number <= 0:
        return False
    if number >= Decimal(10) ** (field.max_digits - field.decimal_places):
        return False
    return number == number.quantize(Decimal(1).scaleb(-field.decimal_places))


def _order_lines_problem(payload, head="注文の"):
    """注文のデータの明細の数量・単価・金額 (数量 × 単価) (単価の単位が式の行は見ない) が、受注の明細の表の欄に入らないときの日本語の文。入れば None。

    head は文の頭の字 (既定は「注文の」。単価を直して受け入れるの直した値は「直した値の」)。

    見るのは読める数 (字か整数で、有限の数) のうち 0 より大きい物だけ。読めない数・0 と負・明細の無い注文・形の違う行は見ない
    (承認の時に accept と create_order が止める = N39 の決めと R02)。受け口 (receive) と承認 (accept) が呼ぶ。
    connect は orders の model を頭で import しない (app の登録から引く)。
    """
    from django.apps import apps

    order_line = apps.get_model("orders", "OrderLine")
    amount = order_line._meta.get_field("amount")
    amount_whole = amount.max_digits - amount.decimal_places
    lines = payload.get("lines") if isinstance(payload, dict) else None
    for number, raw in enumerate(lines if isinstance(lines, list) else [], start=1):
        if not isinstance(raw, dict):
            continue
        figures = {}
        for key, label in (("qty", "数量"), ("unit_price", "単価")):
            value = raw.get(key)
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                continue
            try:
                figure = Decimal(str(value).strip())
            except InvalidOperation:
                continue
            if not figure.is_finite() or figure <= 0:
                # 0 と負は今までどおり承認の時に create_order が R02 で止める (表に入らないので 500 にならない。
                # tests/test_r10_inbox_accept.py の数量 0 の注文が受信箱に入る形を変えない)。
                continue
            field = order_line._meta.get_field(key)
            if not _fits_field(figure, field):
                whole = field.max_digits - field.decimal_places
                return (
                    f"{head} {number} 行目の{label}は、0 より大きく、"
                    f"整数の部分 {whole} 桁・小数 {field.decimal_places} 桁までの数にしてください。"
                )
            figures[key] = figure
        if (
            len(figures) == 2
            and raw.get("price_basis") != PRICE_BASIS_LUMP
            and figures["qty"] * figures["unit_price"] >= Decimal(10) ** amount_whole
        ):
            return f"{head} {number} 行目の金額 (数量 × 単価) が、整数の部分 {amount_whole} 桁を超えます。数量と単価を確かめてください。"
    return None


def _order_text_problem(payload):
    """注文のデータの発注番号・品番・品名・単位・明細の備考が、受ける表の欄の字数に入らないときの日本語の文。入れば None。

    受ける欄は 発注番号 = orders.Order.customer_order_no・品番 = orders.OrderLine.customer_part_no と
    masters.PartnerItem.partner_part_no の小さい方・品名 = masters.PartnerItem.partner_item_name・
    単位 = orders.OrderLine.unit・備考 = orders.OrderLine.note。上限は欄の max_length を app の登録から引く
    (connect は orders と masters の model を頭で import しない。_order_lines_problem と同じ)。
    見る順は 頭の発注番号 → 明細を上から 1 行ずつ 品番・品名・単位・備考。最初に見つけた 1 つだけを返す。
    字数は len() で数える (1 字を 1 と数え、バイトで数えない)。品番は前後の空白を外して数える (受け入れは外して表に入れる = _part_of)。
    字でない値と null と形の違う行は見ない (品番は受け入れの _part_of が断り、ほかは受け入れで使わない)。
    受け口 (receive) と承認 (accept) が、数の欄の検査 _order_lines_problem の次に呼ぶ。字数の表は docs/edi-items.md。
    訳: 受けた注文の字が受注と取引先の品番の表の欄に入らず、受け入れの保存で止まる (置き場のデータベースによっては誤りになる) のと、品番を切って入れて別の部品に当たるのを防ぎ、送った側に直す所を返すため。出所 = 作者の決め (中小企業共通 EDI 本編 7.3.3 は、業務アプリが受け取れる桁数を公開し、送る側がそれを守る形。受け取れる字数の表 = docs/edi-items.md)。
    変えてよい条件 = Issue「決まりの提案」でこの訳に対して議論してから。変えたら docs/edi-items.md の「受け取れる字数を超えたとき」も直す。
    """
    from django.apps import apps

    if not isinstance(payload, dict):
        return None
    order = apps.get_model("orders", "Order")
    order_line = apps.get_model("orders", "OrderLine")
    partner_item = apps.get_model("masters", "PartnerItem")
    order_no = payload.get("order_no")
    limit = order._meta.get_field("customer_order_no").max_length
    if isinstance(order_no, str) and len(order_no) > limit:
        return f"注文の発注番号は {limit} 字までにしてください。"
    part_no_limit = min(
        order_line._meta.get_field("customer_part_no").max_length,
        partner_item._meta.get_field("partner_part_no").max_length,
    )
    checks = (
        ("part_no", "品番", part_no_limit),
        ("part_name", "品名", partner_item._meta.get_field("partner_item_name").max_length),
        ("unit", "単位", order_line._meta.get_field("unit").max_length),
        ("note", "備考", order_line._meta.get_field("note").max_length),
    )
    lines = payload.get("lines")
    for number, raw in enumerate(lines if isinstance(lines, list) else [], start=1):
        if not isinstance(raw, dict):
            continue
        for key, label, limit in checks:
            value = raw.get(key)
            if not isinstance(value, str):
                continue
            text = value.strip() if key == "part_no" else value
            if len(text) > limit:
                return f"注文の {number} 行目の{label}は {limit} 字までにしてください。"
    return None


def _payload_date(value, label):
    """payload の日付の字 (YYYY-MM-DD) を date に直す。null と空は None。読めなければ日本語の ValueError。"""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(
        f"注文のデータの{label}が日付 (YYYY-MM-DD) の形ではありません。"
        "この注文は断り、送る側に送り直してもらってください。"
    )


def _header_value(headers: Mapping[str, str], name: str):
    """見出しの値を大小文字を区別せずに引く。無ければ None。"""
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _refuse(message: str, msg_id):
    """断った事をログに 1 行残し、断る理由を返す。本文と鍵の値はログに出さない。"""
    logger.warning("受け口で断った: %s (webhook-id=%r)", message, msg_id)
    return message


def _unreachable_reply(exc) -> str:
    """届かなかったときの相手の返事の欄の文 (例外の英語の名前を画面に出さない)。"""
    import requests

    if isinstance(exc, requests.Timeout):
        return "届きません: 相手の返事が無く、時間切れです (10 秒)。相手が立っているかを確かめてください。"
    if isinstance(exc, requests.ConnectionError):
        return "届きません: 相手につながりません。つながりの URL と、相手が立っているかを確かめてください。"
    return "届きません: 送る途中で誤りが起きました。もう 1 度送り直してください。"


def _send_connection(partner):
    """取引先 partner の、向き「送る」のつながりを 1 つ引く (pk の小さい物)。無ければ None。

    send_order・send_reply・send_acceptance が呼ぶ。無いときの扱い (例外か返事の欄の文か) は呼び手が決める。
    """
    return (
        Connection.objects.filter(partner=partner, direction=Connection.DIRECTION_SEND)
        .order_by("pk")
        .first()
    )


def _party() -> dict:
    """送り手 (自社) の辞書。鍵の名前 (buyer か supplier) は呼び手が送る物ごとに決める。"""
    # 冒頭の import の並びを変えないため、送る 4 本と同じく関数の中で import する。
    from seizou.core.conf import company_id
    from seizou.core.services import company

    return {"id": company_id(), "name": company().name}


def _encode(data) -> bytes:
    """送る本文の bytes。鍵の並びを固定し、空白を入れず、日本語をそのまま UTF-8 にする。署名はこの bytes のまま。"""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _post_signed(connection, message_id, raw, **options):
    """本文の bytes を署名して相手の受け口へ POST する。requests.post を呼ぶのはこの 1 か所だけ。

    返す物は (返事, 送れなかった訳)。送れたら (requests の返事, None)、送れなかったら (None, (日本語の文, 種類))。
    種類は unreachable (届かない) / no_secret / bad_secret。鍵が無い・鍵の形が違うときは送らない。鍵の値・本文・見出しの値を返す文に入れない。
    options は requests.post に渡す (待ちの timeout と、添付の中身を少しずつ読む stream)。呼び手は transaction.atomic の外から呼ぶ (R17)。
    _deliver (注文・注文回答・検収・出荷の知らせ・会話の言葉) と fetch_chat_file (会話の添付を取りに行く) が呼ぶ。
    """
    # 試験が requests.post を差し替えるため、関数の中で import する。
    import requests

    secret = os.environ.get(connection.secret_env)
    if not secret:
        reply = (
            f"鍵の置き場 {connection.secret_env} に値が無いので送っていません。.env に置いてください。"
        )
        return None, (reply, "no_secret")
    try:
        signed = signing.headers_for(secret, str(message_id), raw)
    except ValueError:
        # 鍵の形 (接頭辞つきの base64) の誤り。文に鍵の値を入れない。
        reply = (
            f"鍵の置き場 {connection.secret_env} の値が鍵の形 (接頭辞つきの base64) でないので"
            "送っていません。.env の値を確かめてください。"
        )
        return None, (reply, "bad_secret")

    try:
        answer = requests.post(
            connection.url,
            data=raw,
            headers={**signed, "Content-Type": "application/json"},
            **options,
        )
    except requests.RequestException as exc:
        return None, (_unreachable_reply(exc), "unreachable")
    return answer, None


def _deliver(connection, message_id, raw) -> tuple[str, str]:
    """本文の bytes を署名して相手の受け口へ送り、返事を (日本語の文, 種類) にして返す。

    send_order と send_reply が呼ぶ。requests.post を呼ぶのは _post_signed の 1 か所だけで、ここはそれを呼ぶ。
    send_acceptance (検収) と send_shipping_notice (出荷の知らせ) と会話の言葉 (post_chat_message) も、ここから送る。
    呼び手は transaction.atomic の外から呼ぶ (R17)。待ちは 10 秒。
    種類は received (受け取った) / rejected (断られた) / unreadable / unreachable / no_secret / bad_secret。
    鍵が無い・鍵の形が違うときは送らない。鍵の値・本文・見出しの値を返す文に入れない。
    """
    answer, failed = _post_signed(connection, message_id, raw, timeout=10)
    if failed is not None:
        return failed
    if answer.status_code == 200:
        return "受け取った", "received"
    if answer.status_code == 400:
        # 返事の JSON の message (200 字まで)。読めなければ決まった文。
        try:
            refused = answer.json().get("message", "")
        except (ValueError, AttributeError):
            refused = ""
        if not isinstance(refused, str) or not refused:
            refused = "理由が読めません"
        return f"断られた: {refused[:200]}", "rejected"
    return f"返事が読めません (HTTP {answer.status_code})", "unreadable"


def send_order(purchase_order, user):
    """発注を注文のデータにし、署名して相手の受け口へ送る。送った物は Outbox に 1 行残して返す。

    - つながりは発注の取引先の、向き「送る」の物。無ければ ValueError。発注が「取消」でも ValueError。
    - 本文は鍵の並びを固定した JSON の bytes。署名はこの bytes のまま (整形し直さない)。
    - 明細の price_basis は単価の単位 (unit = 部品の単位 1 つあたり・lump = 式。金額に数量を掛けない)。中小企業共通 EDI の注文単価は
      1 単位あたりの単価で、式に当たる数量単位コードと単価コードは無いので、seizou の項目として送る。
    - 明細の process_name は発注の明細の工程の名前 (中小企業共通 EDI の工程名を元にした鍵)、quote_no は単価表から写した見積の番号
      (同じく (明細行参照) 見積回答書ID を元にした鍵)。どちらも無ければ空の字 (C100)。
    - 相手への HTTP は transaction.atomic の外で送る (R17)。待ちは 10 秒。
    - 相手の返事は日本語の短い文で Outbox.response に書く。200 (受け取った) なら発注を「送った」にする。
      鍵が無い・断られた・届かないときは例外にせず、発注は「作成」のまま (人が「送り直す」を押す)。
    - 送り直しは 1 回目の番号 (webhook-id) を使い回す。相手の受け口は同じ番号を 1 件にまとめる (R11) ので、
      2 つのタブから送り直しても相手の受信箱に 2 行できない (初見テスト 2 回目の所見)。Outbox には送るたびに 1 行足す。
    - 届かないときの返事は日本語 (例外の英語の名前を画面に出さない)。
    - 鍵の値・本文・見出しの値をログと返す物に出さない。
    - 版 (中小企業共通 EDI の注文変更の方式 2): 番号は同じ発注の同じ版 (revision_no) の最初の行の番号を使い回し、無ければ新しい uuid4
      (版ごとに新しい番号。同じ版の送り直しは同じ番号)。本文の order_id は第 1 版の番号 (注文キー。第 1 版は id と同じ)、
      order_revision_no は発注の今の版、purpose_code は注文書目的コード。明細は取り消した明細 (2) も含めて全部の行を行番号の順に載せ、
      明細状態コードと明細状態理由コードを付ける。届いたときに発注を「送った」にするのは第 1 版だけ (変更の版は発注の状態を変えない)。
      取消の発注は、取消を送る版 (目的 "1") だけ送れる。
    訳: 同じ版の送り直しを相手が 1 件にまとめ (R11)、版が変わったら新しい知らせとして届けるため (中小企業共通 EDI のガイドブック 3.2.1 の注文変更方式 2)。出所 = 中小企業共通 EDI と決め PRICE-4f (11-06 23:1x)。
    変えてよい条件 = 方式 1 に替えると決めたとき。
    """
    # connect と purchasing の循環を避け、冒頭の import の並びを変えないため、関数の中で import する。
    from django.utils import timezone

    from seizou.purchasing import services as purchasing_services

    from .models import Outbox

    partner = purchase_order.partner
    if (
        purchase_order.status == purchase_order.STATUS_CANCELLED
        and purchase_order.purpose_code != edi_codes.PURPOSE_CANCEL
    ):
        raise ValueError(f"送れません。発注 {purchase_order.po_no} は取り消してあります。取消していない発注を選んでください。")
    connection = _send_connection(partner)
    if connection is None:
        raise ValueError(
            f"送れません。取引先 {partner.name} へのつながりがありません。つながりを登録してください。"
        )

    revision_no = purchase_order.revision_no
    same = Outbox.objects.filter(purchase_order=purchase_order, revision_no=revision_no).order_by("pk").first()
    message_id = same.message_id if same is not None else uuid.uuid4()
    original = (
        same
        if revision_no == 1
        else Outbox.objects.filter(purchase_order=purchase_order, revision_no=1).order_by("pk").first()
    )
    order_id = original.message_id if original is not None else message_id
    lines = [
        {
            "line_no": line.line_no,
            "part_no": line.item.part_no,
            "part_name": line.item.name,
            "qty": str(line.qty),
            "unit": line.unit,
            "unit_price": str(line.unit_price),
            "price_basis": line.price_basis,
            "due_date": line.due_date.isoformat() if line.due_date else None,
            "note": line.note,
            "line_status_code": line.line_status_code,
            "line_status_reason_code": line.line_status_reason_code,
            "process_name": line.process.name if line.process_id else "",
            "quote_no": line.quote_no,
        }
        for line in purchase_order.lines.select_related("item", "process").order_by("line_no")
    ]
    order_data = {
        "id": str(message_id),
        "kind": "order",
        "sent_at": timezone.localtime().isoformat(),
        "order_id": str(order_id),
        "order_revision_no": revision_no,
        "purpose_code": purchase_order.purpose_code,
        "order_no": purchase_order.po_no,
        "order_date": purchase_order.order_date.isoformat(),
        "buyer": _party(),
        "supplier": {"code": partner.code, "name": partner.name},
        "note": purchase_order.note,
        "lines": lines,
    }
    raw = _encode(order_data)

    with transaction.atomic():
        outbox = Outbox.objects.create(
            message_id=message_id,
            connection=connection,
            purchase_order=purchase_order,
            response="送っています",
            revision_no=revision_no,
            purpose_code=purchase_order.purpose_code,
        )

    # R17: ここは atomic の外。送る所は send_reply と同じ _deliver。
    reply, kind = _deliver(connection, message_id, raw)

    with transaction.atomic():
        outbox.response = reply[: Outbox._meta.get_field("response").max_length]
        outbox.save(update_fields=["response"])
        # 変更と取消の版は発注の状態を変えない (取消は送る前に「取消」にしてある)。
        if kind == "received" and revision_no == 1:
            purchasing_services.mark_sent(purchase_order, user)

    logger.info(
        "注文を送った: 発注 %s 番号 %s 返事の種類 %s", purchase_order.po_no, message_id, kind
    )
    return outbox


# --- 受注と発注の 1 件ごとの会話 (C104) ---
# 意図: 受注と発注のそれぞれで相手と話し、両方の会社の seizou に同じ会話を残すため (本人の決め CHAT-0・CHAT-1 = a)。
# 相手に届かなかった言葉は「届いていない」と出し、送り直せる (注文の送り直しと同じ作り = 番号の使い回しと受け口の 1 件にまとめる)。

#: 会話が使えない訳の字 (見る順は NOT_CONNECTED → NOT_LINKED → NO_SEND → NOT_SENT)。
#: NOT_CONNECTED は本人の決め CHAT-2 の添え書きの字のまま。ほかの 3 つは、その字が事実と合わない取引のために足した字 (AI の案)。
CHAT_REASON_NOT_CONNECTED = "この取引先は seizou とつながっていないので、会話は使えません。社内の書き込みはメモに書いてください。"
CHAT_REASON_NOT_LINKED = (
    "この注文は seizou どうしの送り合いで届いた物ではないので、相手の発注と結び付いていません。"
    "会話は使えません。社内の書き込みはメモに書いてください。"
)
CHAT_REASON_NO_SEND = (
    "この取引先へ送る向きの接続が無いので、会話は使えません。"
    "設定の画面の「取引先との接続」で送る向きの接続を登録すると使えます。社内の書き込みはメモに書いてください。"
)
CHAT_REASON_NOT_SENT = (
    "この発注はまだ相手の seizou へ送っていないので、会話は使えません。発注を送った後に使えます。社内の書き込みはメモに書いてください。"
)


@dataclass(frozen=True)
class ChatPeer:
    """会話の相手と、会話が使えるか (chat_peer の返す物)。

    purchase_order と inbox は会話の結び付く先 (片方だけ。受注は元の第 1 版の受信した注文)。order_id は元の注文のデータの番号
    (発注した側は第 1 版の送った注文の番号、受けた側は受信した注文の番号。無ければ None)。sender_role は自社の立場 (buyer / supplier)。
    connection は取引先への向き「送る」の接続 (無ければ None)。reason は使えない訳の字 (空 = 使える)。
    """

    purchase_order: object
    inbox: object
    partner: object
    order_id: uuid.UUID | None
    order_no: str
    sender_role: str
    connection: object
    reason: str


def _chat_anchor(target):
    """会話の結び付く先。返す物は (発注, 受信した注文) で、片方か両方が None。

    target は発注・受注・受信した注文のどれか。受注は元の受信した注文 (inbox_message) を引く (無ければ両方 None)。
    受信した注文が変更の版の行 (revises がある) なら、元の第 1 版の行にする (会話は第 1 版の注文に 1 つ)。
    """
    if target._meta.label_lower == "purchasing.purchaseorder":
        return target, None
    # 受信した注文の無い受注は RelatedObjectDoesNotExist (AttributeError の子) なので getattr の既定で None。
    inbox = target if isinstance(target, Inbox) else getattr(target, "inbox_message", None)
    if inbox is not None and inbox.revises_id is not None:
        inbox = inbox.revises
    return None, inbox


def chat_peer(target):
    """会話の相手と、会話が使えるかを読む (読むだけ)。target は発注・受注・受信した注文のどれか。返す物は ChatPeer。

    使えない訳 (reason) は上から最初に当たった物: 取引先に接続が 1 つも無い (受信した注文の取引先が空も含む) → 受注と受信した注文で、
    seizou どうしの受け口で受けた注文でない (受信した注文の無い受注・入口が読み取り) → 取引先へ向き「送る」の接続が無い
    → 発注で、送った注文の行が無い。発注・受信した注文・受注の状態 (取消・断った・出荷し終えた) は見ない (断る処理を足さない)。
    訳: 社内のつもりで書いた字が、相手とつながった後に相手へ届く事故を防ぐため、seizou どうしでつながっていない取引では会話を使えなくする。出所 = 本人の決め 11-06 23:4x〜23:5x (CHAT-2・CHAT-4)「社内だけは事故の元かもな / 繋がっていないで伝達用に打っていたら繋がっていた場合筒抜けでしょう？」。
    変えてよい条件 = 本人が決め直したとき。(訳の字の NOT_LINKED・NO_SEND・NOT_SENT は AI の案。字だけの違い。記録 = docs/nights/20261106/N311.md)
    """
    from .models import Outbox

    purchase_order, inbox = _chat_anchor(target)
    first = None
    if purchase_order is not None:
        partner = purchase_order.partner
        # 元の注文の番号は第 1 版の番号 (相手の受信した注文の第 1 版の行を引く鍵。send_acceptance と同じ引き方)。
        first = Outbox.objects.filter(purchase_order=purchase_order, revision_no=1).order_by("pk").first()
        order_id = first.message_id if first is not None else None
        order_no = purchase_order.po_no
        sender_role = "buyer"
        linked = True
    else:
        partner = inbox.partner if inbox is not None else getattr(target, "customer", None)
        order_id = inbox.message_id if inbox is not None else None
        order_no = inbox.buyer_order_no() if inbox is not None else ""
        sender_role = "supplier"
        linked = inbox is not None and inbox.source == Inbox.SOURCE_DIRECT
    connection = _send_connection(partner) if partner is not None else None
    if partner is None or (connection is None and not Connection.objects.filter(partner=partner).exists()):
        reason = CHAT_REASON_NOT_CONNECTED
    elif not linked:
        reason = CHAT_REASON_NOT_LINKED
    elif connection is None:
        reason = CHAT_REASON_NO_SEND
    elif purchase_order is not None and first is None:
        reason = CHAT_REASON_NOT_SENT
    else:
        reason = ""
    return ChatPeer(
        purchase_order=purchase_order,
        inbox=inbox,
        partner=partner,
        order_id=order_id,
        order_no=order_no,
        sender_role=sender_role,
        connection=connection,
        reason=reason,
    )


def chat_messages_for(target):
    """その注文の会話の行を古い順に返す (読むだけ)。target は発注・受注・受信した注文のどれか。結び付く物が無ければ空。"""
    from .models import ChatMessage

    purchase_order, inbox = _chat_anchor(target)
    if purchase_order is not None:
        return purchase_order.chat_messages.select_related("author")
    if inbox is not None:
        return inbox.chat_messages.select_related("author")
    return ChatMessage.objects.none()


def _full_name(user) -> str:
    """送る JSON の author に入れる氏名 (get_full_name の前後の空白を落とした字)。ログインの名前は会社の外へ出さないので使わない。"""
    get_full_name = getattr(user, "get_full_name", None)
    return get_full_name().strip() if callable(get_full_name) else ""


def _chat_payload(message, peer, author) -> dict:
    """会話の言葉の送る JSON (seizou だけの種類。EDI の文書でない)。送り手の会社の辞書は立場の名前の鍵 (buyer / supplier) に置く。

    添付があるときだけ attachments の鍵を足す (項目は名前・形式・大きさ・中身の印と識別 ID。取りに行く先の URL は入れない = ChatFile の docstring)。
    """
    from django.utils import timezone

    data = {
        "id": str(message.message_id),
        "kind": "chat_message",
        "sent_at": timezone.localtime().isoformat(),
        "order_id": str(peer.order_id),
        "order_no": peer.order_no,
        "sender_role": peer.sender_role,
        peer.sender_role: _party(),
        "author": author,
        "written_at": timezone.localtime(message.written_at).isoformat(),
        "body": message.body,
    }
    files = [
        {"id": str(item.file_id), "name": item.name, "mime": item.mime, "size": item.size, "sha256": item.sha256}
        for item in message.files.order_by("pk")
    ]
    if files:
        data["attachments"] = files
    return data


def _check_chat_file(file):
    """書くときに付ける添付を見る (今の添付ファイルの決まり = attachments.services の ALLOWED・MAX_BYTES・MAX_NAME_LENGTH と断りの字)。

    返す物は (元の名前, 形式)。通らなければ「送れません。」+ 今の添付の字の ValueError (行もファイルも残さない)。中身の先頭のバイトは見ない。
    訳: 会話の添付の形式と大きさを、今の添付と同じ決まりで止めるため (決まりを 2 か所に書かない)。出所 = 本人の決め CHAT-3 (11-06 23:5x) の「上限は今の添付と同じ 20MB」。
    変えてよい条件 = 今の添付の決まり (attachments.services) を変えたとき (ここは同じ表を読むので一緒に変わる)。
    """
    from seizou.attachments import services as attachment_services

    name = os.path.basename(getattr(file, "name", "") or "")
    entry = attachment_services.ALLOWED.get(attachment_services.extension_of(name))
    if entry is None:
        raise ValueError("送れません。" + attachment_services.MESSAGE_BAD_EXTENSION)
    size = getattr(file, "size", None) or 0
    if size <= 0:
        raise ValueError("送れません。" + attachment_services.MESSAGE_EMPTY)
    if size > attachment_services.MAX_BYTES:
        raise ValueError("送れません。" + attachment_services.MESSAGE_TOO_LARGE)
    if len(name) > attachment_services.MAX_NAME_LENGTH:
        raise ValueError("送れません。" + attachment_services.MESSAGE_NAME_TOO_LONG)
    return name, entry[0]


def _send_chat(message, peer, author, delivered_text):
    """自社が書いた言葉を相手の受け口へ送り、返事を行に書く。返す物は取り直した ChatMessage に知らせの字を付けた物。

    呼び手は transaction.atomic の外から呼ぶ (R17)。送る所は send_order と同じ _deliver (番号は行の message_id を使い回す)。
    届いた (received) ときだけ sent_at を書く。届かなければ saved_level = "warning" (黄の知らせ。閉じるまで残る)。
    """
    from django.utils import timezone

    from .models import ChatMessage

    raw = _encode(_chat_payload(message, peer, author))
    # R17: ここは atomic の外。
    reply, kind = _deliver(peer.connection, message.message_id, raw)
    with transaction.atomic():
        saved = ChatMessage.objects.select_for_update().get(pk=message.pk)
        saved.response = reply[: ChatMessage._meta.get_field("response").max_length]
        fields = ["response"]
        if kind == "received":
            saved.sent_at = timezone.now()
            fields.append("sent_at")
        saved.save(update_fields=fields)
    if kind == "received":
        saved.saved_message = delivered_text
    else:
        saved.saved_message = (
            f"書いた言葉は残しましたが、相手に届いていません。{saved.response} 「会話」の欄の「送り直す」で送れます。"
        )
        saved.saved_level = "warning"
    logger.info("会話を送った: 行 %s 番号 %s 返事の種類 %s", saved.pk, saved.message_id, kind)
    return saved


def post_chat_message(target, body, user, file=None):
    """会話に言葉を書き、相手の seizou へ送る。target は発注・受注・受信した注文のどれか (受注は元の受信した注文に結ぶ)。

    - 会話が使えない取引 (chat_peer の reason) は「送れません。」+ 訳の ValueError で、行を作らず HTTP を送らない。
    - 言葉と添付の両方が空 (言葉は空白だけも空)・言葉が 2000 字を超えるときも ValueError。受けた字は変えずに残す (送ったとおりに残すため。画面の欄の前後の空白は form が落とす)。
    - 添付 (file) は 1 つまで。形式と大きさは今の添付と同じ決まり (_check_chat_file)。中身の印 (SHA-256) を作り、言葉の行と同じ atomic で
      ChatFile の行を作ってファイルを storage に置く (attachments.services.add_attachment と同じ形)。送る JSON には項目だけを載せる。
    - 行は atomic の中で作り (返事の欄は「送っています」)、atomic の外で相手へ送る (R17)。返事の字を書き、届いたときだけ相手に届いた日時を書く。
    - 届かなくても例外にしない (書いた言葉は残る)。返す物の saved_message が知らせの字、届かなければ saved_level = "warning"。
    - 鍵の値・本文・見出しの値をログと返す物に出さない。言葉を直す関数と消す関数は作らない (ChatMessage の docstring)。
    訳: 会話が使えない取引と、空の言葉と 2000 字を超える言葉を送らないため。出所 = 本人の決め 11-06 23:4x〜23:5x (CHAT-2) と AI の案 (2000 字。根拠の資料は無い。中小企業共通 EDI に会話は無く、字数の決まりも無い。記録 = docs/nights/20261106/N311.md)。
    変えてよい条件 = 字数は、相手の seizou も同じ字数を受けられる版になってから (受け口が同じ字数で断るため)。
    添付は形と大きさを今の添付と同じ決まり (attachments.services の ALLOWED・MAX_BYTES) で見る (出所 = 本人の決め CHAT-3 の「上限は今の添付と同じ 20MB」)。
    """
    import hashlib

    from django.utils import timezone

    from .models import ChatFile, ChatMessage

    peer = chat_peer(target)
    if peer.reason:
        raise ValueError("送れません。" + peer.reason)
    text = body if isinstance(body, str) else ""
    if not text.strip() and file is None:
        raise ValueError("送れません。言葉か添付がありません。言葉を書くか添付を付けてから「送る」を押してください。")
    if len(text) > CHAT_BODY_MAX:
        raise ValueError("送れません。言葉が 2000 字を超えています。分けて送ってください。")
    file_values = None
    if file is not None:
        name, mime = _check_chat_file(file)
        digest = hashlib.sha256()
        for chunk in file.chunks():
            digest.update(chunk)
        file_values = {"file_id": uuid.uuid4(), "name": name, "mime": mime, "size": file.size, "sha256": digest.hexdigest()}
    author = _full_name(user)
    signed_in = getattr(user, "is_authenticated", False)
    login_name = user.get_username() if signed_in else ""
    with transaction.atomic():
        message = ChatMessage.objects.create(
            message_id=uuid.uuid4(),
            purchase_order=peer.purchase_order,
            inbox=peer.inbox,
            direction=ChatMessage.DIRECTION_SENT,
            author=user if signed_in else None,
            author_name=(author or login_name)[: ChatMessage._meta.get_field("author_name").max_length],
            body=text,
            written_at=timezone.now(),
            response="送っています",
        )
        if file_values is not None:
            ChatFile.objects.create(message=message, file=file, **file_values)
    return _send_chat(message, peer, author, "相手に送りました。")


def resend_chat_message(message, user):
    """相手に届いていない自社の言葉を、同じ番号・同じ書いた日時・同じ言葉で相手へ送り直す。返す物と知らせの字は post_chat_message と同じ形。

    相手の受け口は同じ番号を 1 件にまとめる (_receive_chat_message) ので、2 つのタブから押しても相手に 2 行できない。
    相手から届いた言葉・もう届いた言葉・会話が使えなくなった取引は「送れません。」の ValueError。
    訳: 相手から届いた言葉と、もう届いた言葉を送り直さないため (送り直しは、届いていない自社の言葉を同じ番号でもう 1 度送る物)。出所 = 本人の決め 11-06 23:2x (CHAT-1 の添え書き「相手に届かなかった言葉は「届いていない」と出し、送り直せる」) と AI の案 (記録 = docs/nights/20261106/N311.md)。
    変えてよい条件 = 本人が決め直したとき。
    """
    from .models import ChatMessage

    current = ChatMessage.objects.select_related("purchase_order", "inbox", "author").get(pk=message.pk)
    if current.direction != ChatMessage.DIRECTION_SENT:
        raise ValueError("送れません。相手から届いた言葉は送り直せません。")
    if current.sent_at is not None:
        raise ValueError("送れません。この言葉は相手に届いています。送り直しは要りません。")
    peer = chat_peer(current.purchase_order or current.inbox)
    if peer.reason:
        raise ValueError("送れません。" + peer.reason)
    return _send_chat(current, peer, _full_name(current.author), "送り直しました。相手に届きました。")


def _chat_file_partner(chat_file):
    """添付の言葉が結び付いた注文の取引先 (発注した側は発注の、受けた側は受信した注文の取引先)。"""
    message = chat_file.message
    owner = message.purchase_order if message.purchase_order_id is not None else message.inbox
    return owner.partner if owner is not None else None


def _write_fetch(chat_file, response, content=None):
    """取りに行った結果を添付の行に書く。content (bytes) があればファイルを storage に置き、受け取った日時を書く。返す物は取り直した ChatFile。"""
    from django.core.files.base import ContentFile
    from django.utils import timezone

    from .models import ChatFile

    with transaction.atomic():
        saved = ChatFile.objects.select_for_update().get(pk=chat_file.pk)
        fields = ["fetch_response"]
        if content is not None:
            saved.file.save(saved.name, ContentFile(content), save=False)
            saved.fetched_at = timezone.now()
            fields += ["file", "fetched_at"]
        saved.fetch_response = response[: ChatFile._meta.get_field("fetch_response").max_length]
        saved.save(update_fields=fields)
    return saved


def fetch_chat_file(chat_file, user):
    """相手から届いた言葉の添付を、相手の seizou へ署名つきの要求で取りに行き、自社の側にも保存する。返す物は取り直した ChatFile。

    - 相手から届いた言葉の添付で、まだ受け取っていない物だけ。ほかは「保存できません。」の ValueError。
    - atomic の中で取り直して「受け取っています」を書く (2 つの画面から同時に取りに行くのを減らすため)。
    - 接続は言葉の取引先の向き「送る」の物。無ければ取りに行かず、理由を書いて返す。
    - 要求は atomic の外 (R17)。本文は {"id", "kind": "chat_file", "file_id"} を署名して、相手の受け口 (自社が登録した接続の URL) へ POST する
      (取りに行く先は「相手の受け口 + 添付の識別 ID」の組。届いた字に書かれた URL へは行かない = ChatFile の docstring)。
      待ちは、つなぐまで 10 秒・読むのは 60 秒。中身は少しずつ読み、20MB (attachments.services.MAX_BYTES) を超えたら読むのをやめる。
    - 200 で、読んだ大きさと中身の印 (SHA-256) が相手の書いた値と同じときだけファイルを置き「受け取った」を書く。ほかは「まだ受け取っていない: <理由>」。
    - 受け口の中では取りに行かない (送り手は 10 秒で返事を待つのをやめるため)。受け手の画面が開いた時と「取り直す」で呼ぶ。
    訳: 相手の seizou に置いた添付を、署名つきの要求で取りに行って自社の側にも残し、中身が相手の書いた大きさと印に合う時だけ受け取ったとするため。出所 = 本人の決め (CHAT-3 = a「推奨で」11-06 23:5x) と中小企業共通 EDI 本編 6.4.1 の URL 記載方式。
    変えてよい条件 = 本人が添付の方式を決め直したとき。
    """
    import hashlib

    from seizou.attachments import services as attachment_services

    from .models import ChatFile, ChatMessage

    with transaction.atomic():
        locked = ChatFile.objects.select_for_update().select_related("message").get(pk=chat_file.pk)
        if locked.message.direction != ChatMessage.DIRECTION_RECEIVED or locked.fetched_at is not None:
            raise ValueError("保存できません。この添付は自社が付けた物か、もう受け取った物です。会話の欄を開き直してください。")
        locked.fetch_response = ChatFile.FETCHING
        locked.save(update_fields=["fetch_response"])
    current = ChatFile.objects.select_related("message__purchase_order__partner", "message__inbox__partner").get(pk=locked.pk)
    partner = _chat_file_partner(current)
    connection = _send_connection(partner) if partner is not None else None
    if connection is None:
        return _write_fetch(
            current,
            "まだ受け取っていない: 取引先との接続 (送る向き) が無いので取りに行けません。設定の画面の「取引先との接続」で登録してください。",
        )

    request_id = uuid.uuid4()
    raw = _encode({"id": str(request_id), "kind": "chat_file", "file_id": str(current.file_id)})
    # R17: ここは atomic の外。requests.post を呼ぶのは _post_signed の 1 か所。
    answer, failed = _post_signed(connection, request_id, raw, timeout=(10, 60), stream=True)
    if failed is not None:
        return _write_fetch(current, f"まだ受け取っていない: {failed[0]}")
    try:
        if answer.status_code == 400:
            try:
                refused = answer.json().get("message", "")
            except (ValueError, AttributeError):
                refused = ""
            if not isinstance(refused, str) or not refused:
                refused = "理由が読めません"
            return _write_fetch(current, f"まだ受け取っていない: 断られた: {refused[:200]}")
        if answer.status_code != 200:
            return _write_fetch(current, f"まだ受け取っていない: 返事が読めません (HTTP {answer.status_code})")
        content = bytearray()
        for chunk in answer.iter_content(chunk_size=64 * 1024):
            content.extend(chunk)
            if len(content) > attachment_services.MAX_BYTES:
                break
    finally:
        close = getattr(answer, "close", None)
        if callable(close):
            close()
    if len(content) != current.size or hashlib.sha256(content).hexdigest() != current.sha256:
        return _write_fetch(
            current, "まだ受け取っていない: 届いた中身が、相手の書いた大きさか中身の印と合いません。取り直してください。"
        )
    saved = _write_fetch(current, ChatFile.FETCHED, bytes(content))
    logger.info("会話の添付を受け取った: 添付 %s 識別 %s", saved.pk, saved.file_id)
    return saved
