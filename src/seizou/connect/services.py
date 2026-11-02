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

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction

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
