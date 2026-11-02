"""connect の公開する関数 (01 の 10.4)。ほかの部品と画面は、この関数だけを通してつながりと受信箱を書く。

- 引数の順は 対象 → 値 → user (01 の 10.1)。user は記録のために受ける。記録の列がまだ無いので、今は受けるだけ。
- 鍵の値は受け取らず、表にも持たない。つながりは鍵を入れる環境変数の名前だけを持ち、
  受けるときに os.environ から値を読む。鍵の値を例外の文・ログ・返す物に入れない。
- 署名の確かめは signing.verify をそのまま呼ぶ。ここで書き直さない。
- 受け口は受注を作らない (R10)。受け入れて受注にする関数は、受注の部品ができてから足す。
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction

from seizou.core.exceptions import RuleViolation
from seizou.masters.services import map_partner_item, resolve_partner_item
from seizou.orders.services import create_order

from . import signing
from .models import Connection, Inbox

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


def register_connection(partner, url, secret_env, direction, user):
    """つながりを登録する。secret_env は鍵を入れる環境変数の名前で、値は受け取らない。

    向きは send (送る) か receive (受ける)。送る向きで url が空でなければ、URL の形を確かめる。
    受ける向きの url は空でよい。誤りは日本語の文の ValueError。
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
    if direction == Connection.DIRECTION_SEND and url:
        try:
            URLValidator()(url)
        except ValidationError:
            raise ValueError(
                f"受け口の URL {url!r} が URL の形ではありません。http:// か https:// で始まる URL にしてください。"
            ) from None
    with transaction.atomic():
        return Connection.objects.create(
            partner=partner, url=url, secret_env=secret_env, direction=direction
        )


def receive(raw_body: bytes, headers: Mapping[str, str]):
    """受け口が受けた注文のデータを確かめ、受信箱に入れる (R11・R12)。

    raw_body は受けた本文の bytes (整形しない)。headers は見出しの名前 → 値の mapping。
    通れば Inbox を返す。同じ番号が既にあれば、新しい行を作らず 1 回目の行を返す。
    通らなければ断る理由の日本語の文字列を返し、受信箱には入れない。
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
        return locked


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


def send_order(purchase_order, user):
    """発注を注文のデータにし、署名して相手の受け口へ送る。送った物は Outbox に 1 行残して返す。

    - つながりは発注の取引先の、向き「送る」の物。無ければ ValueError。発注が「取消」でも ValueError。
    - 本文は鍵の並びを固定した JSON の bytes。署名はこの bytes のまま (整形し直さない)。
    - 相手への HTTP は transaction.atomic の外で送る (R17)。待ちは 10 秒。
    - 相手の返事は日本語の短い文で Outbox.response に書く。200 (受け取った) なら発注を「送った」にする。
      鍵が無い・断られた・届かないときは例外にせず、発注は「作成」のまま (人が「送り直す」を押す)。
    - 鍵の値・本文・見出しの値をログと返す物に出さない。
    """
    # connect と purchasing の循環を避け、冒頭の import の並びを変えないため、関数の中で import する。
    import requests
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

    message_id = uuid.uuid4()
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

    secret = os.environ.get(connection.secret_env)
    signed = None
    if not secret:
        reply = (
            f"鍵の置き場 {connection.secret_env} に値が無いので送っていません。.env に置いてください。"
        )
        kind = "no_secret"
    else:
        try:
            signed = signing.headers_for(secret, str(message_id), raw)
        except ValueError:
            # 鍵の形 (接頭辞つきの base64) の誤り。文に鍵の値を入れない。
            reply = (
                f"鍵の置き場 {connection.secret_env} の値が鍵の形 (接頭辞つきの base64) でないので"
                "送っていません。.env の値を確かめてください。"
            )
            kind = "bad_secret"

    if signed is not None:
        # R17: ここは atomic の外。
        try:
            answer = requests.post(
                connection.url,
                data=raw,
                headers={**signed, "Content-Type": "application/json"},
                timeout=10,
            )
        except requests.RequestException as exc:
            reply = f"届きません: {type(exc).__name__}"
            kind = "unreachable"
        else:
            if answer.status_code == 200:
                reply = "受け取った"
                kind = "received"
            elif answer.status_code == 400:
                # 返事の JSON の message (200 字まで)。読めなければ決まった文。
                try:
                    refused = answer.json().get("message", "")
                except (ValueError, AttributeError):
                    refused = ""
                if not isinstance(refused, str) or not refused:
                    refused = "理由が読めません"
                reply = f"断られた: {refused[:200]}"
                kind = "rejected"
            else:
                reply = f"返事が読めません (HTTP {answer.status_code})"
                kind = "unreadable"

    with transaction.atomic():
        outbox.response = reply[: Outbox._meta.get_field("response").max_length]
        outbox.save(update_fields=["response"])
        if kind == "received":
            purchasing_services.mark_sent(purchase_order, user)

    logger.info(
        "注文を送った: 発注 %s 番号 %s 返事の種類 %s", purchase_order.po_no, message_id, kind
    )
    return outbox
