"""connect の公開する関数 (01 の 10.4)。ほかの部品と画面は、この関数だけを通してつながりと受信箱を書く。

- 引数の順は 対象 → 値 → user (01 の 10.1)。user は記録のために受ける。記録の列がまだ無いので、今は受けるだけ。
- 鍵の値は受け取らず、表にも持たない。つながりは鍵を入れる環境変数の名前だけを持ち、
  受けるときに os.environ から値を読む。鍵の値を例外の文・ログ・返す物に入れない。
- 署名の確かめは signing.verify をそのまま呼ぶ。ここで書き直さない。
- 受け口は受注を作らない (R10)。受け入れて受注にする関数は、受注の部品ができてから足す。
- 承認 (受け入れ) と断りの後、send_reply が結果を「注文回答」にして相手の受け口へ返す。
  受け口は本文の kind で注文と注文回答を見分け、注文回答は元の発注に 1 回だけ当てる。
- 受入の後、send_acceptance が検収を相手へ送り、受け口は kind が acceptance の物を受注の明細に 1 回だけ当てる。
- 出荷の後、send_shipping_notice が出荷の知らせを相手へ送り、受け口は kind が shipping_notice の物を発注の明細に 1 回だけ当てる。
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

from seizou.core.exceptions import RuleViolation
from seizou.masters.services import map_partner_item, resolve_partner_item
from seizou.orders.services import create_order

from . import signing
from .models import Acceptance, Connection, Inbox
from .models import ShippingNotice

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
            f"向き {direction!r} は使えません。送る (send) か受ける (receive) にしてください。"
        )
    if not isinstance(secret_env, str) or _ENV_NAME.fullmatch(secret_env) is None:
        # 値が渡されたときに文へ出さないよう、受けた文字列は文に入れない。
        raise ValueError(
            "鍵の置き場の名前は、環境変数の名前 (英大文字で始まり、英大文字と数字と _ だけ) にしてください。"
            "鍵の値は渡さず、.env に置いてください。"
        )
    if direction == Connection.DIRECTION_SEND and url and not _url_is_valid(url):
        raise ValueError(
            f"受け口の URL {url!r} が URL の形ではありません。http:// か https:// で始まる URL にしてください。"
        )
    same = Connection.objects.filter(partner=partner, direction=direction)
    if exclude_pk is not None:
        same = same.exclude(pk=exclude_pk)
    if same.exists():
        label = dict(Connection.DIRECTION_CHOICES)[direction]
        raise ValueError(
            f"この取引先との「{label}」のつながりは既にあります。つながりの一覧の行から編集してください。"
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

    # 注文回答 (承認か断りの結果) は届いた注文の行にせず、元の発注に当てる。
    # kind が無い物と知らない kind は、今までどおり注文として入れる。
    if payload.get("kind") == "order_reply":
        return _receive_reply(message_id, partner, payload)
    # 検収 (相手が受け入れた数の知らせ) も届いた注文の行にせず、元の注文から作った受注の明細に当てる。
    if payload.get("kind") == "acceptance":
        return _receive_acceptance(message_id, partner, payload)
    # 出荷の知らせ (相手が出荷した日と数) も届いた注文の行にせず、元の送った注文の発注の明細に当てる。
    if payload.get("kind") == "shipping_notice":
        return _receive_shipping_notice(message_id, partner, payload)

    # R11: 番号の一意の制約に当て、2 回目は 1 回目の行を返す。
    # 入れ子の atomic で囲み、制約に当たっても外のトランザクションを壊さない。
    try:
        with transaction.atomic():
            return Inbox.objects.create(
                message_id=message_id,
                partner=partner,
                kind=Inbox.KIND_ORDER,
                payload=payload,
            )
    except IntegrityError:
        return Inbox.objects.get(message_id=message_id)


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


MESSAGE_REPLY_SHAPE = "注文回答の形が読めません。order_no・order_id・result を字で送ってください。"
MESSAGE_REPLY_RESULT = "注文回答の結果が accepted か rejected ではありません。"
MESSAGE_REPLY_OTHER_PARTNER = "注文回答の元の発注は、その取引先への物ではありません。"
MESSAGE_REPLY_ORDER_ID = "注文回答の元の注文のデータの番号が、送った物と合いません。"
_REPLY_RESULTS = ("accepted", "rejected")

# 検収 (EDI の 4 段目) の断る理由と、送らなかった理由の日本語。
MESSAGE_ACCEPTANCE_SHAPE = "検収の形が読めません。order_id・line_no・accepted_on・qty を送ってください。"
MESSAGE_ACCEPTANCE_UNKNOWN_ORDER = (
    "検収の元の注文 (届いた注文) がありません。注文のデータの番号を確かめてください。"
)
MESSAGE_ACCEPTANCE_OTHER_PARTNER = "検収の元の注文は、その取引先からの物ではありません。"
MESSAGE_ACCEPTANCE_NOT_ACCEPTED = (
    "検収の元の注文は承認していない (受注になっていない) ので、検収を受けられません。"
    "承認の後に送り直してください。"
)
MESSAGE_ACCEPTANCE_QTY = "検収の数が 0 より大きい数の形ではありません。"
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
MESSAGE_SHIPPING_DATE = "出荷日が日付 (YYYY-MM-DD) の形ではありません。"
MESSAGE_SHIPPING_NOT_EDI_ORDER = (
    "届いた注文で作った受注ではないので、出荷の知らせを送っていません。"
    "seizou どうしで届いた注文を承認した受注の出荷だけが、相手への出荷の知らせになります。"
)
MESSAGE_SHIPPING_NO_CONNECTION = (
    "取引先との接続 (送る向き) が無いので出荷の知らせを送っていません。"
    "設定の画面の「取引先との接続」で、この取引先に送る向きの接続を登録してください。"
)


def _receive_reply(message_id, partner, payload):
    """署名の通った注文回答を、元の発注に 1 回だけ当てる。返す物は ReplyReceipt か、断る理由の文。

    引く順は 発注番号 (order_no) → 発注の取引先が署名の通ったつながりの取引先と同じ → その発注の
    送った注文に order_id の番号がある。同じ回答の番号の 2 回目は何もせずに通す (R11 の考え)。
    発注を書くのは purchasing の services だけ (受け口に利用者はいないので user は None)。
    断るときは全部を戻す (作成 → 送った も残さない)。
    """
    # connect は purchasing の model を頭で import しない (輪になるため)。
    from django.apps import apps

    from seizou.purchasing import services as purchasing_services

    from .models import Outbox

    purchase_order_model = apps.get_model("purchasing", "PurchaseOrder")
    msg_id = str(message_id)
    order_no = payload.get("order_no")
    order_id = payload.get("order_id")
    result = payload.get("result")
    # str でない値を filter に渡すと TypeError になるので、先に形を見る。
    if not all(isinstance(value, str) and value for value in (order_no, order_id, result)):
        return _refuse(MESSAGE_REPLY_SHAPE, msg_id)
    if result not in _REPLY_RESULTS:
        return _refuse(MESSAGE_REPLY_RESULT, msg_id)
    try:
        order_message_id = uuid.UUID(order_id)
    except ValueError:
        return _refuse(MESSAGE_REPLY_ORDER_ID, msg_id)
    reason = payload.get("reason")
    reason = reason if isinstance(reason, str) else ""

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
        if not Outbox.objects.filter(
            purchase_order=purchase_order, message_id=order_message_id
        ).exists():
            return _refuse(MESSAGE_REPLY_ORDER_ID, msg_id)
        if purchase_order.reply_message_id == message_id:
            return ReplyReceipt(message_id=message_id, purchase_order=purchase_order)
        try:
            # 回答が来た = 相手は注文を受け取っている。「作成」なら先に「送った」にする
            # (送った時の返事が落ちた場合の救い)。「取消」は mark_sent が断る。
            if purchase_order.status != purchase_order.STATUS_SENT:
                purchasing_services.mark_sent(purchase_order, None)
            if result == "accepted":
                updated = purchasing_services.mark_accepted(
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


def _receive_acceptance(message_id, partner, payload):
    """署名の通った検収を、元の届いた注文から作った受注の明細に 1 回だけ付ける。返す物は AcceptanceReceipt か、断る理由の文。

    引く順は 形 → 元の注文の番号 (order_id) で届いた注文 → 届いた注文の取引先が署名の通ったつながりの取引先と同じ
    → 届いた注文が受け入れた (受注がある) 物 → その受注の明細の行番号 (line_no)。
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
            )
        except ValueError as exc:
            transaction.set_rollback(True)
            return _refuse(str(exc), msg_id)
    return AcceptanceReceipt(message_id=message_id, acceptance=acceptance)


def record_acceptance(inbox, order_line, qty, accepted_on, user, *, message_id, buyer_order_no, buyer_line_no):
    """検収を受注の明細に 1 行で記録する (受ける側の書く関数)。返す物は Acceptance。

    数が 0 以下・検収日が日付でない・その届いた注文から作った受注の明細でないときは日本語の ValueError。
    同じ番号 (message_id) の 2 回目は新しい行を作らず 1 回目の行を返す (R11 の考え。番号の一意の制約に当てる)。
    user は記録のために受ける (受け口からは None)。
    """
    try:
        number = qty if isinstance(qty, Decimal) else Decimal(str(qty))
        positive = number.is_finite() and number > 0
    except InvalidOperation:
        positive = False
    if not positive:
        raise ValueError("検収の数は 0 より大きい数にしてください。")
    if not isinstance(accepted_on, date):
        raise ValueError("検収日は日付にしてください。")
    if order_line.order_id != inbox.order_id:
        raise ValueError("検収の明細は、その届いた注文から作った受注の明細ではありません。")
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
            )
    except IntegrityError:
        return Acceptance.objects.get(message_id=message_id)


def _receive_shipping_notice(message_id, partner, payload):
    """署名の通った出荷の知らせを、元の送った注文の発注の明細に 1 回だけ付ける。返す物は ShippingNoticeReceipt か、断る理由の文。

    引く順は 形 → 元の注文の番号 (order_id) で送った注文 (Outbox) → その発注の取引先が署名の通ったつながりの取引先と同じ
    → その発注の明細の行番号 (line_no)。発注の状態は見ない (取り消せるのは「作成」の発注だけなので、送った注文の行がある
    発注は取消になっていない)。同じ番号か、同じ明細 × 同じ相手の出荷番号の 2 回目は 1 回目の行を返す (R11 の考え)。
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
            )
        except ValueError as exc:
            transaction.set_rollback(True)
            return _refuse(str(exc), msg_id)
    return ShippingNoticeReceipt(message_id=notice.message_id, notice=notice)


def record_shipping_notice(line, qty, shipped_at, user, *, message_id, shipment_no, supplier_order_no=""):
    """出荷の知らせを発注の明細に 1 行で記録する (受ける側の書く関数)。返す物は ShippingNotice。

    数が 0 以下・出荷日が日付でない・相手の出荷番号が 1〜35 字の字でないときは日本語の ValueError。
    同じ番号 (message_id) か、同じ明細 × 同じ相手の出荷番号の 2 回目は新しい行を作らず 1 回目の行を返す
    (R11 の考え。番号の一意の制約と、自然な鍵の一意の制約に当てる)。user は記録のために受ける (受け口からは None)。
    """
    try:
        number = qty if isinstance(qty, Decimal) else Decimal(str(qty))
        positive = number.is_finite() and number > 0
    except InvalidOperation:
        positive = False
    if not positive:
        raise ValueError("出荷の知らせの数は 0 より大きい数にしてください。")
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


def record_inbox(partner, payload, user, *, message_id=None):
    """受け口を通さずに、届いた注文を受信箱に 1 行入れる (作り物の種と試験と MCP の試しのため)。

    署名の確かめ (R12) を通らないので、受け口 (receive) からは呼ばない。相手に何も送らない。
    user は記録のために受ける。同じ番号の 2 回目は 1 回目の行を返す。
    """
    if not isinstance(payload, dict):
        raise ValueError("注文のデータは辞書 (send_order が作る形) にしてください。")
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
            )
    except IntegrityError:
        return Inbox.objects.get(message_id=message_id)


# 受け入れと断るの日本語 (何が起きて、何をすればよいか)。payload の全文は文に入れない。
MESSAGE_ALREADY_ACCEPTED = (
    "同じ番号の注文のデータは 1 回だけ登録する (2 回届いても 1 件)。"
    "受け入れは済んでいるので、受注の一覧を見てください。"
)
MESSAGE_ACCEPT_REJECTED = (
    "この注文のデータは断り済みなので、受け入れられません。"
    "受け入れるなら、送る側に新しい注文のデータを送ってもらってください。"
)
MESSAGE_NO_LINES = (
    "注文のデータに明細がありません。この注文は断り、送る側に送り直してもらってください。"
)
MESSAGE_NO_REASON = "断る理由がありません。理由を書いてから断ってください。"


def accept(inbox, item_mapping, user):
    """受信箱の注文のデータを受け入れて、受注 (入口 = 受信箱) を作る (R10・R11)。

    item_mapping は相手の品番の字 → 自社の Item。無い品番は取引先ごとの品番の表 (masters) で引く。
    item_mapping で選んだ品番が表に無ければ、その場で表に対応を足す。
    対応の追加・受注の作成・受信箱の状態の更新は 1 つのトランザクションで行う (どれかが失敗したら全部戻る)。
    受け入れ済みの 2 回目は RuleViolation("R11")、断った物と形の違う物は日本語の ValueError。
    得意先の役でない取引先は create_order の ValueError がそのまま出る。返す物は作った Order。
    """
    with transaction.atomic():
        locked = Inbox.objects.select_for_update().get(pk=inbox.pk)
        if locked.status == Inbox.STATUS_ACCEPTED:
            raise RuleViolation("R11", MESSAGE_ALREADY_ACCEPTED)
        if locked.status == Inbox.STATUS_REJECTED:
            raise ValueError(MESSAGE_ACCEPT_REJECTED)

        payload = locked.payload if isinstance(locked.payload, dict) else {}
        raw_lines = payload.get("lines")
        if not isinstance(raw_lines, list) or not raw_lines:
            raise ValueError(MESSAGE_NO_LINES)
        mapping = dict(item_mapping or {})
        items = {}
        lines = []
        for number, raw in enumerate(raw_lines, start=1):
            part_no, part_name = _part_of(raw, number)
            if part_no not in items:
                items[part_no] = _item_for(locked.partner, part_no, part_name, mapping, user)
            item = items[part_no]
            unit = raw.get("unit")
            note = raw.get("note")
            lines.append(
                {
                    "item": item,
                    "qty": _payload_decimal(raw.get("qty"), f" {number} 行目の数量"),
                    "unit": unit if isinstance(unit, str) and unit else item.unit,
                    "unit_price": _payload_decimal(raw.get("unit_price"), f" {number} 行目の単価"),
                    "due_date": _payload_date(raw.get("due_date"), f" {number} 行目の納期"),
                    "note": note if isinstance(note, str) else "",
                    "customer_part_no": part_no,
                }
            )

        order_date = _payload_date(payload.get("order_date"), "発注日")
        if order_date is None:
            raise ValueError(
                "注文のデータに発注日がありません。この注文は断り、送る側に送り直してもらってください。"
            )
        order_no = payload.get("order_no")
        order = create_order(
            locked.partner,
            lines,
            user,
            order_date=order_date,
            customer_order_no=order_no if isinstance(order_no, str) else "",
            source="inbox",
        )
        locked.status = Inbox.STATUS_ACCEPTED
        locked.order = order
        locked.save(update_fields=["status", "order"])

    # 注文回答を相手へ返す。HTTP は atomic の外 (R17)。届かなくても例外にしない (受け入れは成立する)。
    send_reply(locked, user)
    return order


def reject(inbox, reason, user):
    """受信箱の注文のデータを、理由を書いて断る。断れるのは状態が「届いた」の物だけ。返す物は Inbox。"""
    with transaction.atomic():
        locked = Inbox.objects.select_for_update().get(pk=inbox.pk)
        if locked.status != Inbox.STATUS_RECEIVED:
            raise ValueError(
                f"この注文のデータは「{locked.get_status_display()}」なので、断れません。"
                "断れるのは「届いた」の物だけです。"
            )
        text = reason.strip() if isinstance(reason, str) else ""
        if not text:
            raise ValueError(MESSAGE_NO_REASON)
        limit = Inbox._meta.get_field("reason").max_length
        if len(text) > limit:
            raise ValueError(f"断る理由が {limit} 字を超えています。{limit} 字までに縮めてください。")
        locked.status = Inbox.STATUS_REJECTED
        locked.reason = text
        locked.save(update_fields=["status", "reason"])

    # 注文回答を相手へ返す。HTTP は atomic の外 (R17)。届かなくても例外にしない (断りは成立する)。
    send_reply(locked, user)
    return locked


MESSAGE_REPLY_BEFORE_DECISION = (
    "承認か断りをする前の届いた注文には、注文回答を送れません。受け入れるか断ってから送ってください。"
)
MESSAGE_REPLY_NO_CONNECTION = (
    "取引先との接続 (送る向き) が無いので注文回答を送っていません。"
    "設定の画面の「取引先との接続」で、この取引先に送る向きの接続を登録してください。"
)


def send_reply(inbox, user):
    """承認か断りの結果を「注文回答」にし、署名して、注文を送ってきた相手の受け口へ送る。

    - 送れるのは、種類が注文で、受け入れたか断った後の行だけ。それ以外は日本語の ValueError。
    - 番号は 1 回目に付け、送り直すときに使い回す (相手は同じ番号を 1 件にまとめる = R11)。
    - つながりは届いた注文の取引先の、向き「送る」の物。無ければ HTTP を送らず、返事の欄に書いて返す。
    - 相手への HTTP は transaction.atomic の外 (R17)。送る所は send_order と同じ _deliver。
    - 届かない・断られた・鍵が無い・つながりが無いのどれでも例外にしない (承認と断りは成立する)。
      返事は reply_response に書き、相手が受け取ったときだけ reply_sent_at を書く。
    - 鍵の値・本文・見出しの値をログと返す物に出さない。返す物は取り直した Inbox。
    """
    # 冒頭の import の並びを変えないため、send_order と同じく関数の中で import する。
    from django.utils import timezone

    from seizou.core.conf import company_id
    from seizou.core.services import company

    with transaction.atomic():
        locked = Inbox.objects.select_for_update().get(pk=inbox.pk)
        if locked.kind != Inbox.KIND_ORDER or locked.status == Inbox.STATUS_RECEIVED:
            raise ValueError(MESSAGE_REPLY_BEFORE_DECISION)
        if locked.reply_message_id is None:
            locked.reply_message_id = uuid.uuid4()
            locked.save(update_fields=["reply_message_id"])
    message_id = locked.reply_message_id

    connection = (
        Connection.objects.filter(partner=locked.partner, direction=Connection.DIRECTION_SEND)
        .order_by("pk")
        .first()
    )
    if connection is None:
        reply, kind = MESSAGE_REPLY_NO_CONNECTION, "no_connection"
    else:
        accepted = locked.status == Inbox.STATUS_ACCEPTED
        order_data = locked.payload if isinstance(locked.payload, dict) else {}
        order_no = order_data.get("order_no")
        reply_data = {
            "id": str(message_id),
            "kind": "order_reply",
            "sent_at": timezone.localtime().isoformat(),
            "order_id": str(locked.message_id),
            "order_no": order_no if isinstance(order_no, str) else "",
            "result": "accepted" if accepted else "rejected",
            "supplier_order_no": locked.order.order_no if accepted and locked.order else "",
            "reason": "" if accepted else locked.reason,
            "supplier": {"id": company_id(), "name": company().name},
        }
        raw = json.dumps(reply_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
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

    logger.info("注文回答: 届いた注文 %s 番号 %s 返事の種類 %s", saved.pk, message_id, kind)
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

    from seizou.core.conf import company_id
    from seizou.core.services import company
    from seizou.purchasing import services as purchasing_services

    from .models import Outbox

    # 数量と受入日は表の値で送る (受入を作った直後の物は、数量が渡した桁のままのため)。
    receipt_model = apps.get_model("purchasing", "Receipt")
    current = receipt_model.objects.select_related("line__purchase_order__partner").get(pk=receipt.pk)
    line = current.line
    purchase_order = line.purchase_order

    first = Outbox.objects.filter(purchase_order=purchase_order).order_by("pk").first()
    if first is None:
        return purchasing_services.record_acceptance_response(
            current, MESSAGE_ACCEPTANCE_NOT_SENT_ORDER, user
        )
    message_id = purchasing_services.assign_acceptance_number(current, user)

    connection = (
        Connection.objects.filter(partner=purchase_order.partner, direction=Connection.DIRECTION_SEND)
        .order_by("pk")
        .first()
    )
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
        "line_no": line.line_no,
        "accepted_on": current.received_on.isoformat(),
        "qty": str(current.qty),
        "buyer": {"id": company_id(), "name": company().name},
    }
    raw = json.dumps(acceptance_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    # R17: ここは atomic の外。送る所は send_order・send_reply と同じ _deliver。
    reply, kind = _deliver(connection, message_id, raw)

    saved = purchasing_services.record_acceptance_response(current, reply, user, sent=(kind == "received"))
    logger.info("検収を送った: 受入 %s 番号 %s 返事の種類 %s", current.pk, message_id, kind)
    return saved


def send_shipping_notice(shipment, user):
    """出荷を「出荷の知らせ」にし、署名して、注文を送ってきた相手 (得意先の seizou) の受け口へ送る。

    - 送るのは、届いた注文 (Inbox) で作った受注の出荷だけ。それ以外は HTTP を送らず、返事の欄に書いて返す。
    - 番号は送る前に保存し (届かなくても残る)、送り直すときに使い回す (相手は同じ番号を 1 件にまとめる = R11)。
    - つながりは届いた注文の取引先の、向き「送る」の物 (注文回答と同じ向き)。無ければ HTTP を送らず、返事の欄に書いて返す。
    - 相手への HTTP は transaction.atomic の外 (R17)。送る所は send_order・send_reply・send_acceptance と同じ _deliver。
    - 届かない・断られた・鍵が無い・つながりが無いのどれでも例外にしない (出荷は成立する)。
      返事は出荷の notice_response に、相手が受け取ったときだけ notice_sent_at に書く。
      出荷の列は shipping.services の assign_notice_number・record_notice_response が書く。
    - 鍵の値・本文・見出しの値をログと返す物に出さない。返す物は取り直した Shipment。
    """
    # connect と shipping の循環を避け、冒頭の import の並びを変えないため、関数の中で import する。
    from django.apps import apps
    from django.utils import timezone

    from seizou.core.conf import company_id
    from seizou.core.services import company
    from seizou.shipping import services as shipping_services

    # 数量と出荷日は表の値で送る (出荷を作った直後の物は、数量が渡した桁のままのため。send_acceptance と同じ)。
    shipment_model = apps.get_model("shipping", "Shipment")
    current = shipment_model.objects.select_related("order_line__order").get(pk=shipment.pk)
    line = current.order_line
    order = line.order

    # 届いた注文は受注の 1 対 1 の逆の関係。無ければ RelatedObjectDoesNotExist (AttributeError の子) で None。
    inbox = getattr(order, "inbox_message", None)
    if inbox is None:
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
        "line_no": line.line_no,
        "supplier_order_no": order.order_no,
        "shipment_no": current.shipment_no,
        "shipped_at": current.shipped_at.isoformat(),
        "qty": str(current.qty),
        "supplier": {"id": company_id(), "name": company().name},
    }
    raw = json.dumps(notice_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
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
            f"注文のデータの {number} 行目に相手の品番がありません。"
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
            raise ValueError(f"品番 {part_no} の自社の部品を選んでください。")
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


def _deliver(connection, message_id, raw) -> tuple[str, str]:
    """本文の bytes を署名して相手の受け口へ送り、返事を (日本語の文, 種類) にして返す。

    send_order と send_reply が呼ぶ。requests.post を呼ぶのはこの 1 か所だけ。
    send_acceptance (検収) と send_shipping_notice (出荷の知らせ) も、この 1 か所から送る。
    呼び手は transaction.atomic の外から呼ぶ (R17)。待ちは 10 秒。
    種類は received (受け取った) / rejected (断られた) / unreadable / unreachable / no_secret / bad_secret。
    鍵が無い・鍵の形が違うときは送らない。鍵の値・本文・見出しの値を返す文に入れない。
    """
    # 試験が requests.post を差し替えるため、関数の中で import する。
    import requests

    secret = os.environ.get(connection.secret_env)
    if not secret:
        reply = (
            f"鍵の置き場 {connection.secret_env} に値が無いので送っていません。.env に置いてください。"
        )
        return reply, "no_secret"
    try:
        signed = signing.headers_for(secret, str(message_id), raw)
    except ValueError:
        # 鍵の形 (接頭辞つきの base64) の誤り。文に鍵の値を入れない。
        reply = (
            f"鍵の置き場 {connection.secret_env} の値が鍵の形 (接頭辞つきの base64) でないので"
            "送っていません。.env の値を確かめてください。"
        )
        return reply, "bad_secret"

    try:
        answer = requests.post(
            connection.url,
            data=raw,
            headers={**signed, "Content-Type": "application/json"},
            timeout=10,
        )
    except requests.RequestException as exc:
        return _unreachable_reply(exc), "unreachable"
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
    - 相手への HTTP は transaction.atomic の外で送る (R17)。待ちは 10 秒。
    - 相手の返事は日本語の短い文で Outbox.response に書く。200 (受け取った) なら発注を「送った」にする。
      鍵が無い・断られた・届かないときは例外にせず、発注は「作成」のまま (人が「送り直す」を押す)。
    - 送り直しは 1 回目の番号 (webhook-id) を使い回す。相手の受け口は同じ番号を 1 件にまとめる (R11) ので、
      2 つのタブから送り直しても相手の受信箱に 2 行できない (初見テスト 2 回目の所見)。Outbox には送るたびに 1 行足す。
    - 届かないときの返事は日本語 (例外の英語の名前を画面に出さない)。
    - 鍵の値・本文・見出しの値をログと返す物に出さない。
    """
    # connect と purchasing の循環を避け、冒頭の import の並びを変えないため、関数の中で import する。
    from django.utils import timezone

    from seizou.core.conf import company_id
    from seizou.core.services import company
    from seizou.purchasing import services as purchasing_services

    from .models import Outbox

    partner = purchase_order.partner
    if purchase_order.status == purchase_order.STATUS_CANCELLED:
        raise ValueError(f"発注 {purchase_order.po_no} は取り消してあるので送れません。")
    connection = (
        Connection.objects.filter(partner=partner, direction=Connection.DIRECTION_SEND)
        .order_by("pk")
        .first()
    )
    if connection is None:
        raise ValueError(
            f"取引先 {partner.name} へのつながりがありません。つながりを登録してください。"
        )

    first = Outbox.objects.filter(purchase_order=purchase_order).order_by("pk").first()
    message_id = first.message_id if first is not None else uuid.uuid4()
    lines = [
        {
            "line_no": line.line_no,
            "part_no": line.item.part_no,
            "part_name": line.item.name,
            "qty": str(line.qty),
            "unit": line.unit,
            "unit_price": str(line.unit_price),
            "due_date": line.due_date.isoformat() if line.due_date else None,
            "note": line.note,
        }
        for line in purchase_order.lines.select_related("item").order_by("line_no")
    ]
    order_data = {
        "id": str(message_id),
        "kind": "order",
        "sent_at": timezone.localtime().isoformat(),
        "order_no": purchase_order.po_no,
        "order_date": purchase_order.order_date.isoformat(),
        "buyer": {"id": company_id(), "name": company().name},
        "supplier": {"code": partner.code, "name": partner.name},
        "note": purchase_order.note,
        "lines": lines,
    }
    raw = json.dumps(order_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )

    with transaction.atomic():
        outbox = Outbox.objects.create(
            message_id=message_id,
            connection=connection,
            purchase_order=purchase_order,
            response="送っています",
        )

    # R17: ここは atomic の外。送る所は send_reply と同じ _deliver。
    reply, kind = _deliver(connection, message_id, raw)

    with transaction.atomic():
        outbox.response = reply[: Outbox._meta.get_field("response").max_length]
        outbox.save(update_fields=["response"])
        if kind == "received":
            purchasing_services.mark_sent(purchase_order, user)

    logger.info(
        "注文を送った: 発注 %s 番号 %s 返事の種類 %s", purchase_order.po_no, message_id, kind
    )
    return outbox
