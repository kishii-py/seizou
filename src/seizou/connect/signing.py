"""受け口の署名 (Standard Webhooks の形) を作り、確かめる。

純粋な Python の module で、標準の library だけを使う。
画面の枠組みにも、HTTP の library にも依存しない。

署名の形:

- 見出しは `webhook-id`・`webhook-timestamp`・`webhook-signature` の 3 つ。
- 「番号.時刻.本文」を HMAC-SHA256 で署名し、
  `v1,` + base64 の値を署名の見出しに置く。
- 番号と時刻は文字列にし、時刻は UNIX 秒の整数にする。
  本文は受けた bytes のまま使い、整形し直さない。
- 鍵は公開の仕様の符号化 (接頭辞 `whsec_` + base64) で受け取る。
  ほどいた bytes を HMAC の鍵にする。
  符号化されていない生の文字列は受けない (ValueError)。
- 時刻の幅は既定で ±300 秒 (過去と未来の両側)。幅の外は断る。

同じ番号の 2 回目 (再送) の約束:

- この module は状態を持たず、再送を見分けない。
  時刻の幅の中の再送は、`verify` を何度呼んでも通り、同じ番号を返す。
- 再送を見分けるのは connect の `receive` の仕事。
  `receive` は `verify` が返した番号を `Inbox` の番号の一意の制約 (R11) に当てる。
  2 回目は新しい行を作らず、1 回目と同じ 200 を返す。
  送る側が再送をやめられるようにするため。
- 時刻の幅の外の再送は、この module の `verify` が断る (R12)。
- 登録した取引先の鍵を引くのも `receive` の仕事。
  この module は鍵を受け取るだけ。

鍵の値は、例外の文にもログにも出さない。
"""

import base64
import hashlib
import hmac
import time
from collections.abc import Mapping

HEADER_ID = "webhook-id"
HEADER_TIMESTAMP = "webhook-timestamp"
HEADER_SIGNATURE = "webhook-signature"

_SECRET_PREFIX = "whsec_"
_SIGNATURE_PREFIX = "v1,"


class SignatureError(Exception):
    """署名を確かめられなかったときの例外。`reason` に理由の種類を持つ。

    鍵違いと改ざんは、どちらも `SIGNATURE_MISMATCH` にする。
    違いを外に出さないため。
    理由の種類を日本語の断る理由に直すのは、呼ぶ側 (`receive`)。
    鍵の値は例外の文に入れない。
    """

    MISSING_HEADER = "missing_header"
    INVALID_TIMESTAMP = "invalid_timestamp"
    TIMESTAMP_OUT_OF_TOLERANCE = "timestamp_out_of_tolerance"
    SIGNATURE_MISMATCH = "signature_mismatch"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _decode_secret(secret: str) -> bytes:
    """接頭辞つきの base64 の鍵をほどき、HMAC の鍵の bytes を返す。"""
    if not secret.startswith(_SECRET_PREFIX):
        raise ValueError("鍵は接頭辞つきの base64 の形で渡す")
    try:
        return base64.b64decode(secret[len(_SECRET_PREFIX) :], validate=True)
    except ValueError:
        raise ValueError("鍵の base64 をほどけない") from None


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    """「番号.時刻.本文」を HMAC-SHA256 で署名する。

    署名の見出しの値 (`v1,` + base64) を返す。
    """
    key = _decode_secret(secret)
    signed = f"{msg_id}.{timestamp}.".encode() + body
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    return _SIGNATURE_PREFIX + base64.b64encode(digest).decode()


def headers_for(
    secret: str, msg_id: str, body: bytes, *, now: float | None = None
) -> dict[str, str]:
    """送る側が付ける 3 つの見出しの辞書を返す。

    番号は呼ぶ側が uuid4 で作って渡す。
    `now` は UNIX 秒で、省くと今の時刻を使う。
    """
    timestamp = int(time.time() if now is None else now)
    return {
        HEADER_ID: msg_id,
        HEADER_TIMESTAMP: str(timestamp),
        HEADER_SIGNATURE: sign(secret, msg_id, timestamp, body),
    }


def verify(
    secret: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    now: float | None = None,
    tolerance: int = 300,
) -> str:
    """3 つの見出しと本文で署名を確かめ、通れば番号を返す。

    見出しの名前は大小文字を区別せずに読む。
    確かめる順は、見出しの有無、時刻の形、時刻の幅、署名。
    時刻の差は ±tolerance 秒までを通す (境目は通す)。
    署名の見出しの空白区切りの候補のうち、
    `v1,` で始まる物のどれかが合えば通る。
    `v1,` 以外の候補は無視する。
    通らなければ SignatureError を投げる。
    再送は見分けない (module の説明の「2 回目」の約束)。
    """
    lowered = {name.lower(): value for name, value in headers.items()}
    msg_id = lowered.get(HEADER_ID)
    timestamp_text = lowered.get(HEADER_TIMESTAMP)
    signature_text = lowered.get(HEADER_SIGNATURE)
    if not (msg_id and timestamp_text and signature_text):
        raise SignatureError(SignatureError.MISSING_HEADER)

    if not (timestamp_text.isascii() and timestamp_text.isdigit()):
        raise SignatureError(SignatureError.INVALID_TIMESTAMP)
    try:
        timestamp = int(timestamp_text)
    except ValueError:
        # 桁が多すぎて int にできないとき
        raise SignatureError(SignatureError.INVALID_TIMESTAMP) from None

    current = int(time.time() if now is None else now)
    if abs(current - timestamp) > tolerance:
        raise SignatureError(SignatureError.TIMESTAMP_OUT_OF_TOLERANCE)

    expected = sign(secret, msg_id, timestamp, body).encode()
    for candidate in signature_text.split():
        if not candidate.startswith(_SIGNATURE_PREFIX):
            continue
        if hmac.compare_digest(candidate.encode(), expected):
            return msg_id
    raise SignatureError(SignatureError.SIGNATURE_MISMATCH)
