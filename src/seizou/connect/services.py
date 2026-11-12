"""connect の公開する関数 (部品の公開する関数の設計の決め)。ほかの部品と画面は、この関数だけを通してつながりと受信箱を書く。

- 引数の順は 対象 → 値 → user (services の書き方の設計の決め)。user は記録のために受け、受信した注文を書く関数は変更の履歴 (core.services.record_history。
  作者の決め) に残す。受け口が作る行は人が無いので、人の字を「<取引先の名前> の seizou」にする。つながりは変更の履歴に入れていない (受けるだけ)。
- 鍵の値は受け取らず、表にも持たない。つながりは鍵を入れる環境変数の名前だけを持ち、
  受けるときに os.environ から値を読む。鍵の値を例外の文・ログ・返す物に入れない。
- 署名の確かめは signing.verify をそのまま呼ぶ。ここで書き直さない。
- 受け口は受注を作らない (R10)。受け入れて受注にする関数は、受注の部品ができてから足す。
- 受信した注文に答えた後、send_reply が答えを「注文回答」にして相手の受け口へ返す。答えは 4 つ (受諾・条件付受諾・辞退・保留) で、
  JSON の reply_type に中小企業共通 EDI の注文回答タイプコードの値 (AP・CA・RE・AJ。core.edi_codes) をそのまま持つ。
  受け口は本文の kind で注文と注文回答を見分け、注文回答は元の発注に 1 回だけ当てる。
- 受入の後、send_acceptance が検収を相手へ送り、受け口は kind が acceptance の物を受注の明細に 1 回だけ当てる。
- 出荷の後、send_shipping_notice が出荷の知らせを相手へ送り、受け口は kind が shipping_notice の物を発注の明細に 1 回だけ当てる。
- 受注と発注の 1 件ごとの会話 (作者の決め): post_chat_message が言葉を残して相手へ送り、受け口は kind が chat_message の物を元の注文に 1 回だけ結ぶ。
  会話は中小企業共通 EDI に無い seizou どうしだけの種類。言葉を直す関数と消す関数は作らない (ChatMessage の docstring)。
- 会話の言葉を受けたら、取引先のつながりの自社の連絡先のメールアドレスへ core.mail で知らせる (行を作った後の atomic の外。送れなくても受ける)。
- 送り合った記録 (Exchange): 送る所 _deliver と受ける所 receive が、送った物と受けた物の中身を送ったとおり・受けたとおりの字で 1 通 1 行に残す
  (電子帳簿保存法の電子取引のデータ。作者の決め)。金額は exchange_amount が中身から数える (作者の決め)。直す道と消す道は無い。
- 受信した注文の入口は 2 つ (直接 = 受け口 / 読み取り = intake の見張り)。読み取りの行は注文回答と出荷の知らせを相手へ送らない (相手は seizou とつながっていない)。差出人の確認待ち (取引先の空の行) は choose_sender で得意先を選んでから承認する。
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse

from seizou.core import access, edi_codes
from seizou.core.exceptions import RuleViolation
from seizou.masters.services import PRICE_BASIS_LUMP, PRICE_BASIS_UNIT, PRICE_BASIS_VALUES, map_partner_item, resolve_partner_item
from seizou.masters.services import unit_name_for
from seizou.orders.services import cancel_order, create_order, shipped_qty, update_order

from . import signing
from .models import Acceptance, Connection, Inbox
from .models import Exchange
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
#: 受ける向きの 2 つ以上の取引先の鍵で署名が合った (同じ鍵を 2 社に使っている設定の誤り。どの取引先かは出さない)
MESSAGE_SAME_SECRET = "同じ鍵で署名の合う取引先が 2 つ以上あります。つながりの鍵を取引先ごとに分けてください。"
MESSAGE_INVALID_ID = "番号 (webhook-id) が uuid の形ではありません。"
MESSAGE_INVALID_BODY = "本文が JSON の形ではありません。"
#: 知らない種類の通知 ({kind} は本文の kind の字を 40 字で切った物。字でなければ決まった字)
MESSAGE_UNKNOWN_KIND = "知らない種類の通知です: {kind}"
#: 明細の無い注文 (送る側へ返す文。承認の時の MESSAGE_NO_LINES とは読み手が違う)
MESSAGE_RECEIVE_NO_LINES = "明細の無い注文は受けません。"
#: 送り手の辞書 (buyer か supplier) の id が自社の id と同じ本文 (受け口の URL を自社に向けた設定の誤り)
MESSAGE_FROM_SELF = (
    "送り手の会社の id が、この会社の id と同じです。"
    "送る向きのつながりの受け口の URL が、自社の受け口を指していないかを確かめてください。"
)
#: 送る向きのつながりの受け口の URL が空 (送らない。頭を「届きません」にして「届いていない: 」の前置きを重ねない)
MESSAGE_NO_URL = (
    "届きません: この取引先の送る向きのつながりに、受け口の URL がありません。"
    "設定の画面の「取引先との接続」で、相手の受け口の URL を入れてください。"
)
#: 相手の受け口が 404 を返した (受け口の URL の道の誤り。種類は unreadable)
MESSAGE_NOT_FOUND = (
    "届きません: 相手の受け口が見つかりません (HTTP 404)。"
    "つながりの受け口の URL が /connect/receive/order/ で終わるかを確かめてください。"
)

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


def _check_connection(partner, url, secret_env, direction, *, exclude_pk=None, contact_email=""):
    """つながりの値を確かめる (登録と編集で同じ)。見る順は 向き → 鍵の置き場の名前 → URL → 自社の連絡先のメールアドレス → 2 行目。

    送る向きで url が空でなければ、URL の形を確かめる。受ける向きの url は空でよい。
    settings の SEIZOU_CONNECT_PUBLIC_ONLY が真なら、送る向きの URL は https だけを保存できる (訳は _public_only)。
    contact_email (自社の連絡先のメールアドレス) は空でよい。空でなければ 254 字までのメールアドレスの形を見る (受けた字は文に入れない)。
    同じ取引先と向きのつながりが既にあれば断る。exclude_pk は、直すときに自分の行を除くための引数。
    DB の一意の制約は持たない (表を変えないため)。誤りは日本語の文の ValueError。
    訳: 知らせのメールが届かない形のアドレスを残さないため。出所 = AI の案。変えてよい条件 = 1 つのつながりに複数のアドレスを持たせる時。
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
    if direction == Connection.DIRECTION_SEND and url and _public_only() and urlsplit(url).scheme != "https":
        # 送る先を外の名前に絞る設定 (_public_only) では、送る向きの URL は https だけ (127.0.0.1・localhost も外さない)。
        raise ValueError(
            "保存できません。この置き場は送る先を外の名前に絞る設定なので、受け口の URL は https:// で始まる URL にしてください。"
        )
    if contact_email and not _email_is_valid(contact_email):
        # 字は括弧の中にそのまま書く (使い方の画面が ast で集め、画面から起きない文の表 core/manual.py の NOT_FROM_SCREEN と比べるため)
        raise ValueError("保存できません。自社の連絡先のメールアドレスが、メールアドレスの形ではありません。例 info@example.jp の形で入れてください。")
    same = Connection.objects.filter(partner=partner, direction=direction)
    if exclude_pk is not None:
        same = same.exclude(pk=exclude_pk)
    if same.exists():
        label = dict(Connection.DIRECTION_CHOICES)[direction]
        raise ValueError(
            f"保存できません。この取引先との「{label}」のつながりは既にあります。つながりの一覧の行から編集してください。"
        )


#: 手元の番地の名前 (url_points_to_self が自社のポートと組で見る。urlsplit の hostname は小文字で、IPv6 の [] を外した字)
_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
#: seizou の受け口の道の終わり (connection_url_notes が見る。相手の置き場の道なので reverse で引かない)
_RECEIVE_ORDER_PATH = "/connect/receive/order/"

# つながりを保存した後の黄の知らせの字 (connection_url_notes が返す。止めない)
NOTE_URL_SELF = "この受け口の URL は自社の seizou を指しています。このままでは相手に届きません。相手の担当者に聞いた URL に直してください。"
NOTE_URL_EMPTY = "送る向きなのに受け口の URL が空です。このままでは、この取引先へ送れません。相手の担当者に聞いた URL を入れてください。"
NOTE_URL_PATH = "受け口の URL が /connect/receive/order/ で終わっていません。seizou の受け口の URL はこの形で終わります。相手の担当者に確かめてください。"


def url_points_to_self(url) -> bool:
    """受け口の URL が自社の seizou を指すか (読むだけ。例外を投げない。外へは問い合わせない)。

    URL が _url_is_valid で偽なら偽。名前が 127.0.0.1・localhost・::1 のどれかで、ポート (URL に無ければ http は 80、https は 443) が
    自社のポート (settings の SEIZOU の port が整数のとき) と同じなら真。core.conf.mail_settings() の site_url が空でなく、
    その名前と URL の名前が同じ (大小を問わない) なら真。ほかは偽。名前は引かない (mail_settings が設定の画面の上書きを読むのは今のまま)。
    呼び手は connection_url_notes (つながりを保存した後の黄の知らせ) と、点検 (seizou_doctor)。名前と引数を変えない。
    訳: 送る向きの受け口の URL を自社に向けた打ち違い (手元の 2 社のポートの打ち違い・VPS で自社の名前を入れた) を、保存した後の黄の知らせと点検で分かるようにするため。止めない (本人の決め 11-11 12:5x の考え方)。手元の PC の名前 (点の無い名前) と自社の名前の別名は見ない (名前を引かない)。出所 = 本体の決め 11-11 14:2x (精査 Q3 の T1)。変えてよい条件 = 自社を指すかの見方を本人が広げると決めたとき。
    """
    from django.conf import settings

    from seizou.core.conf import mail_settings

    if not _url_is_valid(url):
        return False
    parts = urlsplit(url)
    host = parts.hostname or ""
    seizou = getattr(settings, "SEIZOU", {}) or {}
    own_port = seizou.get("port") if isinstance(seizou, dict) else None
    if host in _LOOPBACK_NAMES and isinstance(own_port, int) and not isinstance(own_port, bool):
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port == own_port:
            return True
    site_url = mail_settings().get("site_url") or ""
    if not isinstance(site_url, str) or not site_url:
        return False
    try:
        site_host = urlsplit(site_url).hostname or ""
    except ValueError:
        return False
    return bool(site_host) and site_host == host


def connection_url_notes(connection) -> list[str]:
    """つながりの受け口の URL の黄の知らせの字の並び (読むだけ。表に書かない)。

    向きが送るのときだけ見る。URL が空なら NOTE_URL_EMPTY だけ。空でなければ、url_points_to_self が真なら NOTE_URL_SELF、
    URL の道が /connect/receive/order/ で終わらなければ NOTE_URL_PATH (2 つとも当たれば 2 つ。終わりの / が無い形も知らせる =
    Django は POST の終わりの / を足さないため)。受ける向きは空の並び。
    """
    if connection.direction != Connection.DIRECTION_SEND:
        return []
    url = connection.url or ""
    if not url:
        return [NOTE_URL_EMPTY]
    notes = []
    if url_points_to_self(url):
        notes.append(NOTE_URL_SELF)
    try:
        path = urlsplit(url).path
    except ValueError:
        path = ""
    if not path.endswith(_RECEIVE_ORDER_PATH):
        notes.append(NOTE_URL_PATH)
    return notes


def _with_url_notes(connection):
    """connection_url_notes が空でなければ、返すつながりに黄の知らせ (saved_note と saved_level) を付けて返す。属性は画面の知らせのためだけで、表に書かない。"""
    notes = connection_url_notes(connection)
    if notes:
        connection.saved_note = "".join(notes)
        connection.saved_level = "warning"
    return connection


def _public_only() -> bool:
    """送る先を外の名前に絞るか。Django の settings の SEIZOU_CONNECT_PUBLIC_ONLY を読む (既定は偽)。

    会社の設定の画面で変えられる SEIZOU の辞書には入れない (画面から守りを外させないため)。
    真のとき: _check_connection は送る向きの URL を https だけ保存し、_post_signed は送る前に名前を引いて
    全部の番地が外の番地 (_private_destination) のときだけ送る。偽のときは今のまま (名前を引かない・http を通す)。
    訳: ログインした人が送る向きの URL を社内の機械や同じ VPS のもう 1 社 (127.0.0.1) に向け、署名つきの本文を POST させないため。既定は今のまま (LAN の中だけで http で開く置き場と社内の PC の名前を通す作者の決め (開発の記録 docs/nights/20261102/N15.md)・開発の記録 docs/nights/20261102/N19.md を破らない)。出所 = エンタープライズ化の調べを受けた作者の決め。
    変えてよい条件 = 本人が既定を真にすると決めたとき。
    限り: 名前を確かめた後に requests がもう 1 度名前を引くので、その間に名前の向きを変える攻撃 (DNS rebinding) は止めない ((c)。番地を固定する adapter は作らない)。
    """
    from django.conf import settings

    return bool(getattr(settings, "SEIZOU_CONNECT_PUBLIC_ONLY", False))


def _private_destination(url):
    """送る先の URL の名前を引き、外の番地でない番地が 1 つでもあれば返事の欄の文を返す。全部が外の番地なら None。

    名前は socket.getaddrinfo で引き、全部の番地を ipaddress の is_global で見る (IPv6 の %scope は外して読む)。
    名前が無い・引けない・番地が 1 つも無いときは、今の届かない字 (相手につながりません) を返す。
    番地の値は文に出すが、鍵と本文は出さない。呼び手 (_post_signed) は atomic の外から呼ぶ (R17)。
    訳: 送る先を外の名前に絞る設定 (_public_only) のとき、社内か手元の番地へ署名つきの本文を送らないため。出所 = エンタープライズ化の調べを受けた作者の決め。
    変えてよい条件 = 本人が既定を真にすると決めたとき (_public_only と同じ)。
    """
    unreachable = "届きません: 相手につながりません。つながりの URL と、相手が立っているかを確かめてください。"
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return unreachable
    if not parts.hostname:
        return unreachable
    try:
        found = socket.getaddrinfo(parts.hostname, port or 443, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return unreachable
    if not found:
        return unreachable
    for _family, _type, _proto, _name, address in found:
        text = str(address[0]).split("%", 1)[0]
        try:
            public = ipaddress.ip_address(text).is_global
        except ValueError:
            public = False
        if not public:
            return (
                f"送っていません: 受け口の URL の名前が社内か手元の番地 ({text}) を指しています。"
                "つながりの URL を確かめてください。"
            )
    return None


def _email_is_valid(text):
    """自社の連絡先のメールアドレスの形を見る。字で、254 字まで、Django の validate_email が通る。"""
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    if not isinstance(text, str) or len(text) > Connection._meta.get_field("contact_email").max_length:
        return False
    try:
        validate_email(text)
    except ValidationError:
        return False
    return True


def register_connection(partner, url, secret_env, direction, user, *, contact_email=""):
    """つながりを登録する。secret_env は鍵を入れる環境変数の名前で、値は受け取らない。

    向きは send (送る) か receive (受ける)。確かめは _check_connection。
    contact_email (自社の連絡先のメールアドレス) は名前でだけ渡せる (今の呼び手は位置の引数で呼ぶため)。
    誤りは日本語の文の ValueError。
    保存した後に、送る向きの受け口の URL が空・自社を指す・道の形が違うときは、返す物に黄の知らせ (saved_note と saved_level) を付ける (止めない。本体の決め 11-11 14:2x)。
    """
    _check_connection(partner, url, secret_env, direction, contact_email=contact_email)
    with transaction.atomic():
        connection = Connection.objects.create(
            partner=partner, url=url, secret_env=secret_env, direction=direction, contact_email=contact_email
        )
    return _with_url_notes(connection)


def update_connection(connection, partner, url, secret_env, direction, user, *, contact_email=None):
    """つながりを直す。引数は register_connection にそろえ、対象を先頭に置く (services の書き方の設計の決め)。

    確かめは登録と同じ。鍵の値は受け取らない。
    contact_email は名前でだけ渡せる。None なら今の値のまま (位置の引数で呼ぶ今の呼び手が値を消さないように)、字なら確かめて書く。
    user は記録のために受ける (つながりは変更の履歴に入れていない = 作者の決めの列挙の外なので、今は受けるだけ)。返す物は取り直した Connection。
    保存した後に、送る向きの受け口の URL が空・自社を指す・道の形が違うときは、返す物に黄の知らせ (saved_note と saved_level) を付ける (止めない。本体の決め 11-11 14:2x)。
    """
    _check_connection(
        partner, url, secret_env, direction, exclude_pk=connection.pk, contact_email=contact_email or ""
    )
    with transaction.atomic():
        locked = Connection.objects.select_for_update().get(pk=connection.pk)
        locked.partner = partner
        locked.url = url
        locked.secret_env = secret_env
        locked.direction = direction
        if contact_email is not None:
            locked.contact_email = contact_email
        locked.save()
    return _with_url_notes(locked)


def receive(raw_body: bytes, headers: Mapping[str, str], *, remote: str = ""):
    """受け口が受けた注文のデータを確かめ、受信箱に入れる (R11・R12)。

    raw_body は受けた本文の bytes (整形しない)。headers は見出しの名前 → 値の mapping。
    通れば Inbox を返す。同じ番号が既にあれば、新しい行を作らず 1 回目の行を返す。
    通らなければ断る理由の日本語の文字列を返し、受信箱には入れない。
    本文の kind が order_reply (注文回答) なら受信箱に入れず、元の発注に当てて ReplyReceipt を返す。
    本文の kind が acceptance (検収) なら受信箱に入れず、受注の明細に当てて AcceptanceReceipt を返す。
    本文の kind が shipping_notice (出荷の知らせ) なら受信箱に入れず、発注の明細に当てて ShippingNoticeReceipt を返す。
    本文の kind が chat_message (会話の言葉) なら受信箱に入れず、元の注文 (受信した注文か発注) に結んで ChatReceipt を返す。
    本文の kind が chat_file (会話の添付を取りに来た要求) なら何も残さず、返してよい添付なら ChatFileGrant を返す。
    本文の kind が self_invoice (仕入明細) なら受信箱に入れず、受注の明細に当てて受けた仕入明細にし、SelfInvoiceReceipt を返す。
    本文の kind が self_invoice_response (仕入明細回答) なら受信箱に入れず、元の仕入明細の明細に答えを書いて SelfInvoiceResponseReceipt を返す。
    kind が無い物は注文として入れる。知らない kind と、明細 (lines) の無い注文は断る (受信箱に残さない)。
    署名は受ける向きの全部のつながりの鍵で確かめ、署名の合う取引先が 2 つ以上なら断る (_signed_partner)。
    送り手の辞書 (buyer か supplier) の id が自社の id と同じ本文は、kind で分ける前に断る (_sent_by_self)。
    remote は接続元の IP の字 (受け口の view が core.login_guard.client_address で読んで渡す。名前でだけ渡せる)。
    結果が決まったら、接続元と結果をログ seizou.connect に 1 行書く (_log_receive)。
    """
    msg_id = _header_value(headers, signing.HEADER_ID)

    partner, verified_id, refused = _signed_partner(raw_body, headers, msg_id)
    if refused is not None:
        _log_receive(remote, msg_id, None, refused)
        return refused

    # 署名が通って取引先が決まった物は、送り合った記録に受けたとおりの字で残す (署名の合わない物は取引のデータでないので残さない)。
    # 返る時に結果を「受けた」か「断った: <理由>」にする。添付を取りに来た要求 (chat_file) は取引の文書でないので残さない。
    exchange = _exchange_received(partner, verified_id, raw_body)
    result = _receive_verified(partner, verified_id, raw_body, msg_id)
    _exchange_result(exchange, f"断った: {result}" if isinstance(result, str) else "受けた")
    _log_receive(remote, msg_id, partner, result)
    return result


def _log_receive(remote: str, msg_id, partner, result) -> None:
    """受け口の結果を 1 行ログ seizou.connect に書く (接続元・webhook-id・取引先の pk・結果)。

    取引先が決まらない (署名の前に断った) ときの pk は「-」、接続元が空なら「-」。結果は「受けた」か「断った: <理由>」。
    断ったは warning、受けたは info。本文と鍵の値は書かない (webhook-id は見出しの字なので %r で書く)。
    """
    who = partner.pk if partner is not None else "-"
    if isinstance(result, str):
        logger.warning("受け口の記録: 接続元 %s webhook-id=%r 取引先 %s 結果 断った: %s", remote or "-", msg_id, who, result)
    else:
        logger.info("受け口の記録: 接続元 %s webhook-id=%r 取引先 %s 結果 受けた", remote or "-", msg_id, who)


def _signed_partner(raw_body: bytes, headers: Mapping[str, str], msg_id):
    """受ける向きの全部のつながりの鍵で署名を確かめ、署名の合う取引先を決める (receive が呼ぶ)。

    返す物は (取引先, 確かめた番号, None) か、断ったとき (None, None, 断る理由の文)。
    鍵が未設定のつながりと、鍵の形が読めない (短い鍵を含む) つながりは、名前だけをログに出して飛ばす。
    鍵の見出しの誤り (_HEADER_ERRORS) は鍵と関係なく決まるので、1 つ目に出たところで断る (今までの形のまま)。
    署名の合う取引先が無ければ MESSAGE_SIGNATURE_MISMATCH、2 つ以上なら MESSAGE_SAME_SECRET で断る (どの取引先かは文に出さない)。
    訳: 1 つの鍵を 2 社の受ける向きに使うと、注文が別の取引先の物として入るため (後から来る種類は元の注文と取引先を比べるので断られる)。出所 = エンタープライズ化の調べを受けた作者の決め。
    変えてよい条件 = 1 つの取引先に複数の受ける向きのつながりを持たせると決めたとき。
    """
    matched = {}
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
                return None, None, _refuse(_HEADER_ERRORS[exc.reason], msg_id)
            continue
        except ValueError:
            # 鍵の符号化が壊れているか、長さが外れている。送り手でなく設定の誤りなので、名前だけを出して次へ。
            logger.warning(
                "受け口: 鍵の形が読めないつながりを飛ばした (環境変数 %s。設定の誤り)",
                connection.secret_env,
            )
            continue
        # 最初に合った物で止めず、全部のつながりを確かめる (同じ鍵の 2 社を見つけるため)。
        matched.setdefault(connection.partner_id, connection.partner)

    if not matched:
        return None, None, _refuse(MESSAGE_SIGNATURE_MISMATCH, msg_id)
    if len(matched) > 1:
        return None, None, _refuse(MESSAGE_SAME_SECRET, msg_id)
    (partner,) = matched.values()
    return partner, verified_id, None


def _sent_by_self(payload) -> bool:
    """本文の送り手の辞書 (buyer か supplier) の id が、自社の id と同じか (_receive_verified が呼ぶ)。

    payload の buyer と supplier のうち辞書の物を見て、id が空でない字で自社の id と同じなら真。自社の id が空なら偽。
    訳: 送り手の会社の id が自社の id と同じ本文は、受信箱にも元の伝票にも当てずに断る。送る向きの受け口の URL を自社に向けた設定の誤りで、相手に届いていない発注を「受け取った」と記録しないため。自社の id が空の置き場 (試験の設定 tests/settings.py の SEIZOU = {}) では比べない (1 つの DB で 2 社の役をする試験が、自社あての送り合いで回っているため)。出所 = 依頼書 N382 (精査 Q3 の報告の T1、2026-11-10)。
    変えてよい条件 = 1 つの置き場が自社あての知らせを受ける形を、本人が決めたとき。
    """
    # 冒頭の import の並びを変えないため、_party と同じく関数の中で import する。
    from seizou.core.conf import company_id

    own = company_id()
    if not own:
        return False
    for key in ("buyer", "supplier"):
        party = payload.get(key)
        if isinstance(party, dict):
            sender = party.get("id")
            if isinstance(sender, str) and sender and sender == own:
                return True
    return False


def _receive_verified(partner, verified_id, raw_body: bytes, msg_id):
    """署名が通って取引先 partner が決まった後の受け口の中身 (receive が呼ぶ)。返す物は receive と同じ。"""
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
    # 送り手が自社の本文 (受け口の URL を自社に向けた設定の誤り) は、kind で分ける前に断る。
    if _sent_by_self(payload):
        return _refuse(MESSAGE_FROM_SELF, msg_id)

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
    # 仕入明細 (得意先が受け入れた数と金額。中小企業共通 EDI の仕入明細の項目を元にした) も受信した注文の行にせず、受けた仕入明細にする。
    if payload.get("kind") == "self_invoice":
        return _receive_self_invoice(message_id, partner, payload)
    # 仕入明細回答 (相手が明細ごとに返した答え) も受信した注文の行にせず、元の仕入明細の明細に書く。
    if payload.get("kind") == "self_invoice_response":
        return _receive_self_invoice_response(message_id, partner, payload)
    kind = payload.get("kind")
    if kind is not None and kind != "order":
        # 鍵の値と本文は文に入れない。kind の字だけを 40 字で切って出す。
        shown = kind[:40] if isinstance(kind, str) else "(字でない値)"
        return _refuse(MESSAGE_UNKNOWN_KIND.format(kind=shown), msg_id)
    # 明細の無い注文は受信箱に入れずに断る (数の欄の検査 _order_lines_problem の前)。
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        return _refuse(MESSAGE_RECEIVE_NO_LINES, msg_id)

    # 明細の数が受注の明細の表の欄に入らない注文は、受信箱に入れずに断る (承認の後の 500 を防ぐ。作者の決め)。
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
        # 断りの文は相手へ返り、送り合った記録の結果の列 (金額の列でない) に残るので、単価の値を入れない (Claude Security の走査の指摘 (11-07))
        problem = _revision_problem(revises, payload, revision_no, purpose_code, hide_money=True)
        if problem:
            return _refuse(f"注文の変更を受けられません。{problem}", msg_id)
    # R11: 番号の一意の制約に当て、2 回目は 1 回目の行を返す。
    # 入れ子の atomic で囲み、制約に当たっても外のトランザクションを壊さない。
    # 作った行は同じ atomic の中で変更の履歴に残す (人は無いので、人の字は「<取引先の名前> の seizou」。2 回目は何も書かない)。
    try:
        with transaction.atomic():
            row = Inbox.objects.create(
                message_id=message_id,
                partner=partner,
                kind=Inbox.KIND_ORDER,
                payload=payload,
                revision_no=revision_no,
                purpose_code=purpose_code,
                revises=revises,
            )
            _record(row, None, by=f"{partner.name} の seizou")
            return row
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


def _revision_problem(base, payload, revision_no, purpose_code, *, hide_money=False):
    """変更と取消の版 (payload) を元の行 (base = 第 1 版の受信した注文) の受注に当てられない訳の字。当てられれば None。

    受け口 (receive)・受け入れ (_accept_revision)・受信した注文の画面 (connect_tags.revision_problem) が同じ見方で呼ぶ。
    見る順: 元の行が断った → 未処理なら None (受けて待たせる。受け入れの時にもう一度見る) → 受け入れてある版以下の版 → 受注が取消・出荷済 →
    取消の版で受注が一部出荷 → 変更の版の明細ごと (行番号で受注の明細を引く): 品番が違う・取り消す明細に出荷済みがある・
    取り消す明細を指示書か入荷の引当か検収が指す・出荷済みのある明細の単価が違う・数が出荷済みより少ない。受注に無い行番号の明細は断らない (受け入れの時に足す)。
    訳: 出荷し終えた受注と、出した物の記録 (納品書・請求書) と食い違う変更を受注に当てず、作り始めた物の記録 (指示書・入荷の引当) と届いた検収が指す受注の明細を消さないため。出所 = 作者の決め (11-06 23:2x)・中小企業共通 EDI に合わせた作者の決め (11-06 23:2x)・作者の決め (11-07 06:33。一部を出荷した受注への取消を受けない)・作者の決め (11-07 06:37。指示書・入荷の引当・検収・出荷のある受注の明細を消す変更を受けない)。
    変えてよい条件 = 作者の決めの答えが変わったとき。
    hide_money が真なら、単価の違いの字に単価の値を入れない (「注文の単価が受注の単価と違います」)。受け口 (receive) はいつも真
    (文は相手へ返り、送り合った記録の結果の列に残る = 金額の列でないので、金額の閲覧の無い人にも出る)。受け入れと画面は
    金額の閲覧の無い人 (access.hides_money) のとき真 (請求書の取消の断り cancel_refusal の hide_money と同じ形)。
    訳: 金額を見られない人に、断りの文の中の単価を見せないため。出所 = Claude Security の走査の指摘 (11-07)。
    変えてよい条件 = 結果の列と相手の返事の欄を金額の閲覧で伏せる形ができたとき (そのときは受け口でも値を出してよい)。
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
            if hide_money:
                return f"明細 {number} は出荷済みの物があるので単価は変えられません (注文の単価が受注の単価と違います)。"
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
#: 会話の言葉 1 つに付けられる添付の数の上限 (送る post_chat_message と受ける _chat_file_items で同じ数を使う。本人の決め)
MAX_CHAT_FILES = 1
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
    訳: 相手の 4 つの答えを、答えを待っている発注にだけ当て、形の違う答えを当てずに相手へ返すため。出所 = 中小企業共通 EDI の注文回答タイプコードの項目と作者の決め (11-07 00:0x)・作者の決め (11-06 23:1x)。
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
    訳: 形の違う明細の答えを表に入れる前に断り、桁を超える数を表に入れて後で 500 になるのを防ぐため。出所 = 中小企業共通 EDI の注文回答明細回答タイプコードの項目と作者の決め (11-06 23:1x)。
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
    断られた後に取り消した発注にも、送った注文の行がある。作者の決め)。
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
    行を新しく作った時だけ、行を作った atomic の外で _notify_chat_received を呼び、相手から会話が届いたことをメールで知らせる
    (同じ番号の 2 回目では呼ばない。送り手が送り直すたびにメールが重ならないように。送れなくても会話は受ける)。
    断る処理を足さない: 取消した発注・断った受信した注文・出荷し終えた受注への言葉も受ける。
    訳: 形の読めない言葉と、その取引先と結び付かない注文への言葉を受けないため (同じ番号の 2 回目は 1 件にまとめる = R11 の考え)。出所 = RULES.md R11・R12 と AI の案 (今の受け口の断りと同じ作り = 出荷の知らせ・検収の受け口。記録 = docs/nights/20261106/N311.md)。添付は項目だけを受け、形と大きさは今の添付と同じ決まり (attachments.services の ALLOWED・MAX_BYTES) で見る (出所 = 本人の決めの「上限は今の添付と同じ 20MB」)。
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
    created = False
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
        created = True
    except IntegrityError:
        chat = ChatMessage.objects.get(message_id=message_id, direction=ChatMessage.DIRECTION_RECEIVED)
    if created:
        # 行と添付の行を作った atomic の外で知らせる (サーバーを待つ間、書き込みの鍵を握らないように)
        _notify_chat_received(chat, partner)
    return ChatReceipt(message_id=message_id, chat_message=chat)


#: 会話の知らせのメールに載せる 1 行目の字数の上限 (超えたら切って「…」を付ける)
CHAT_MAIL_FIRST_LINE_MAX = 100


def _chat_first_line(body):
    """会話の言葉の 1 行目 (空白だけでない最初の行の前後の空白を落とした字)。CHAT_MAIL_FIRST_LINE_MAX 字を超えたら切って「…」。無ければ空の字。"""
    for line in (body or "").splitlines():
        text = line.strip()
        if text:
            return text if len(text) <= CHAT_MAIL_FIRST_LINE_MAX else text[:CHAT_MAIL_FIRST_LINE_MAX] + "…"
    return ""


def _chat_mail_target(message):
    """会話の言葉が結ぶ注文の番号の字と、画面の道 (reverse。部品が入っていなければ空の字)。

    自社が発注した側 = 発注の詳細、自社が受けた側 = 受け入れた後は受注の詳細・前は受信した注文の詳細。
    受け入れる前の番号は、受信した注文の行の payload (相手が送った注文の JSON) の発注番号。字でなければ「受信した注文」だけ。
    """
    from django.urls import NoReverseMatch, reverse

    def path(name, pk):
        try:
            return reverse(name, args=[pk])
        except NoReverseMatch:
            return ""

    if message.purchase_order_id is not None:
        po = message.purchase_order
        return f"発注 {po.po_no}", path("purchasing:purchaseorder_detail", po.pk)
    inbox = message.inbox
    if inbox.order_id is not None:
        return f"受注 {inbox.order.order_no}", path("orders:order_detail", inbox.order_id)
    order_no = inbox.buyer_order_no()
    label = f"受信した注文 {order_no}" if order_no else "受信した注文"
    return label, path("connect:inbox_detail", inbox.pk)


def _chat_mail_address(partner):
    """会話の知らせの宛先 (その取引先のつながりの自社の連絡先のメールアドレス 1 つ)。受ける向きの行を先に見て、空なら送る向きの行。無ければ空の字。"""
    found = dict(
        Connection.objects.filter(partner=partner).exclude(contact_email="").values_list("direction", "contact_email")
    )
    return found.get(Connection.DIRECTION_RECEIVE) or found.get(Connection.DIRECTION_SEND) or ""


def _notify_chat_received(message, partner):
    """相手から届いた会話の言葉を、取引先との接続の自社の連絡先のメールアドレスへメールで知らせる (core.mail.send)。何も返さず、例外を外へ出さない。

    宛先が無い・メールの送り方がまだ・送れない は記録 (logging) だけに書いて戻る (会話の行は残り、受け口は今までどおり受ける)。
    記録には取引先と言葉の pk だけを書き、アドレスと本文は書かない。atomic の外で呼ぶ (呼ぶのは _receive_chat_message の行を作った後)。
    件名は「<取引先の名前> から会話が届きました (<番号の字>)」。本文は字だけで、取引先の名前・番号の字・1 行目・リンク・返信しても相手に届かない旨。
    訳: 相手から会話が届いたことを、seizou を開いていない人も見落とさないため (中小企業共通 EDI 本編 6.4.2 の EDI 文書新着連絡サービスと同じ考え)。宛先は取引先との接続の自社の連絡先のアドレス (受ける向きの行を先に、空なら送る向きの行)。メールには会話の 1 行目と画面へのリンクだけを載せ、2 行目から後と添付は載せない。送れなくても会話は受ける。出所 = 作者の決め (宛先は取引先との接続の画面で登録したアドレス・載せるのは 1 行目とリンクだけ) と中小企業共通 EDI 本編 6.4.2。変えてよい条件 = 作者が宛先か載せる物を決め直した時。
    """
    from seizou.core import mail

    address = _chat_mail_address(partner)
    if not address:
        logger.info("会話の知らせ: 取引先 %s のつながりに自社の連絡先のメールアドレスが無いので送らなかった", partner.pk)
        return
    if not mail.ready():
        logger.info("会話の知らせ: メールの送り方がまだなので送らなかった")
        return
    try:
        label, path = _chat_mail_target(message)
        url = mail.link(path) if path else ""
        first = _chat_first_line(message.body) or "(言葉はありません。添付は seizou で開いてください)"
        subject = f"{partner.name} から会話が届きました ({label})"
        body = "\n".join(
            [
                f"{partner.name} から、{label} の会話に言葉が届きました。",
                "",
                first,
                "",
                "続きは seizou の「会話」で読めます。",
                url or "(seizou の URL の頭が設定に無いので、リンクを付けていません)",
                "",
                f"このメールは seizou が送りました。このメールに返信しても {partner.name} には届きません。返事は seizou の「会話」に書いてください。",
            ]
        )
        mail.send([address], subject, body + "\n")
    except mail.MailNotSent as exc:
        logger.warning("会話の知らせのメールを送れなかった: 取引先 %s 言葉 %s 訳 %s", partner.pk, message.pk, exc)
        return
    except Exception:
        logger.exception("会話の知らせのメールで思わぬ誤り: 取引先 %s 言葉 %s", partner.pk, message.pk)
        return
    logger.info("会話の知らせのメールを送った: 取引先 %s 言葉 %s", partner.pk, message.pk)


def _chat_file_items(value):
    """会話の言葉の添付の項目 (attachments) を読む。鍵が無ければ空の並び。読めなければ None (言葉ごと断る)。

    各要素は id (uuid の字)・name (1〜100 字で、拡張子が添付ファイルの形式の表にある)・size (1 から 20MB までの整数。bool は外す)・
    sha256 (16 進の 64 字) を見る。mime は読まず、自社の表 (attachments.services.ALLOWED) の値を入れる。同じ id が 2 つあれば読めない物にする。
    並びが MAX_CHAT_FILES (送る側の上限と同じ 1 つ) より長ければ、要素を見る前に読めない物にする。
    返す物は ChatFile の欄の辞書の並び (file_id・name・mime・size・sha256)。
    訳: 送る側は添付を 1 つまでしか付けないので、それより多い並びを受けて行と比べる手間 (同じ id の確かめは数の 2 乗) を増やさないため。出所 = 本人の決め (送る側の 1 つまで) とエンタープライズ化の調べを受けた作者の決め。
    変えてよい条件 = 送る側の上限を変えるとき (同じ定数 MAX_CHAT_FILES を使う)。
    """
    from seizou.attachments import services as attachment_services

    if value is None:
        return []
    if not isinstance(value, list):
        return None
    if len(value) > MAX_CHAT_FILES:
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
    訳: 取引先との接続の署名が通った相手に、その相手との会話で自社が書いた添付だけを返すため (EDI 本編 6.4.1 の「アクセス制御」)。出所 = 本人の決めと EDI 本編 6.4.1。
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
    user は記録のために受け、作った行を変更の履歴に残す。同じ番号の 2 回目は 1 回目の行を返す (何も書かない)。
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
            row = Inbox.objects.create(
                message_id=message_id,
                partner=partner,
                kind=Inbox.KIND_ORDER,
                payload=payload,
                source=source,
                extraction=extraction,
                sender_name=sender_name,
                **values,
            )
            _record(row, user)
            return row
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
#: 受け入れ・保留・断るの後に注文回答が届かなかったときの黄の知らせ ({response} は返事の欄 reply_response の字。発注の UNDELIVERED_MESSAGE と同じ形。金額を入れない)
MESSAGE_REPLY_UNDELIVERED = "注文回答は相手に届いていません。{response} 直したら、受信した注文の詳細の「送り直す」を押してください。"
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
    明細の process_name と quote_no は、受注の明細の工程名と見積の番号に写す (字でなければ空。作者の決め)。
    訳: 受信した注文を人が受け入れたときだけ受注にし (R10)、同じ注文を 2 回受け入れても受注を 1 件にするため (R11。2 回届いても 1 件)。出所 = RULES.md R10・R11。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    return _accept(inbox, item_mapping, user, send=send, accepted_at=accepted_at)


#: 条件を直して受け入れたときの知らせ (入力の土台が「保存しました。」の代わりに出す)
MESSAGE_CONDITIONAL_SAVED = "条件を直して受け入れました。直した値で受注を作り、相手に条件付きの答えを返しました。相手が発注を直すのを待ちます。"
#: 条件を直して受け入れるで、どの値も直していなかったときの知らせ
MESSAGE_CONDITIONAL_UNCHANGED = "どの値も直していないので、受け入れるとして相手に返しました。"
#: 上の 2 つの、注文回答が相手に届かなかったときの字 (「相手に返しました」と書かない。後ろに MESSAGE_REPLY_UNDELIVERED の黄の知らせが続く)
MESSAGE_CONDITIONAL_SAVED_UNDELIVERED = "条件を直して受け入れました。直した値で受注を作りました。"
MESSAGE_CONDITIONAL_UNCHANGED_UNDELIVERED = "どの値も直していないので、そのまま受け入れました。"
#: 保留したときの知らせ
MESSAGE_HOLD_SAVED = "保留にしました。答えが決まったら、受信した注文の詳細から受け入れるか断ってください。"


def _with_reply_notice(result, inbox):
    """注文回答を送った後に取り直した受信した注文 (inbox) を見て、届いていなければ result に黄の知らせの字を付けて返す (読むだけ。表に書かない)。

    付けるのは、入口が直接 (Inbox.SOURCE_DIRECT) で、reply_sent_at が空で、reply_response が空でなく MESSAGE_REPLY_SEED でもないときだけ
    (読み取りの行は相手へ送らない = MESSAGE_REPLY_INTAKE なので知らせない。送る向きのつながりが無い時も届いていないので知らせる)。
    result の saved_note の前に MESSAGE_REPLY_UNDELIVERED を置き (前からある saved_note はその後ろに残す)、saved_level を "warning" にする。
    前に置くのは、受注を作った知らせの 1 文 (orders.services.create_order の saved_note) を知らせの終わりに残すため。saved_message は変えない。
    赤にせず黄にするのは、受け入れ・保留・断るそのものは成立しているため (相手に届いていない発注と同じ形。本体の決め 11-11 14:2x)。
    """
    if _reply_undelivered(inbox):
        note = getattr(result, "saved_note", "")
        result.saved_note = MESSAGE_REPLY_UNDELIVERED.format(response=inbox.reply_response) + (note if isinstance(note, str) else "")
        result.saved_level = "warning"
    return result


def _reply_undelivered(inbox) -> bool:
    """注文回答を送った後に取り直した受信した注文 (inbox) が、相手に届いていない形か (読むだけ。_with_reply_notice と同じ見方)。"""
    response = inbox.reply_response or ""
    return bool(
        inbox.source == Inbox.SOURCE_DIRECT and inbox.reply_sent_at is None and response and response != MESSAGE_REPLY_SEED
    )


def replies_undelivered():
    """相手に届いていない注文回答の受信した注文の queryset (読むだけ)。

    受信した注文のうち、入口が直接で、送った日時が空で、返事の欄が空でなく (送ろうとした)、送る物でない字
    (MESSAGE_REPLY_SEED・MESSAGE_REPLY_INTAKE) でない物。まだ答えていない行は返事の欄が空なので、入らない。
    見方は、保存した後の黄の知らせ (_reply_undelivered) と同じ。注文回答のタイプ (reply_type) は見ない:
    変更と取消の版を受け入れた・断った時の注文回答は、タイプを書かずに送る (send_reply が状態から決める) ので、タイプで絞ると、
    その注文回答が届かなかった時に黄の知らせは出るのに、この関数で数えなかった (本体の直し 11-12。N416 の記録の「本体の判断が要る事」2)。
    今日やることの「相手に届いていない注文回答」の数 (core.dashboard) と、受信した注文の一覧の絞り「注文回答が届いていない物だけ」
    (connect.views) が、この 1 つの関数を使う (数と一覧が食い違わないため。本体の決め 11-12 13:4x)。

    訳: 相手に届いていない注文回答・出荷の知らせ・検収を、詳細を開かなくても今日やることで気づけるようにするため (届かなかった物は自動では送り直さず、人が「送り直す」を押す)。作り物の種の読み込みと、相手が seizou でない物 (送る物でない) は数えない。相手に断られた物 (「断られた: …」) も、届いていない物として数える。出所 = 本体の決め 11-11 14:2x (精査 Q3 の T8・M4)。変えてよい条件 = 届いていない物の数え方を本人が決め直したとき。
    """
    return (
        Inbox.objects.filter(source=Inbox.SOURCE_DIRECT, reply_sent_at__isnull=True)
        .exclude(reply_response="")
        .exclude(reply_response__in=(MESSAGE_REPLY_SEED, MESSAGE_REPLY_INTAKE))
    )


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
    """条件を直して受け入れるの答えを、_order_lines_problem が読む注文の形 ({"lines": [{"qty", "unit_price"}, …]}) にする。"""
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
        before = _saved(locked)
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
            unit_code = _known_unit_code(raw)
            note = raw.get("note")
            # 工程名と見積の番号 (作者の決め。どちらも任意の項目。字でなければ空の字にし、断らない)
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
                    "unit": unit if isinstance(unit, str) and unit else ("" if unit_code else item.unit),
                    "unit_price": unit_price,
                    "price_basis": _payload_price_basis(raw.get("price_basis"), f" {number} 行目の単価の単位"),
                    "due_date": due_date,
                    "note": note if isinstance(note, str) else "",
                    "customer_part_no": part_no,
                    "process_name": process_name if isinstance(process_name, str) else "",
                    "quote_no": quote_no if isinstance(quote_no, str) else "",
                    # 相手が一覧にあるコードを送っていれば、受注の明細に渡す (orders の _line_unit がコードを先に使い、字が空なら一覧の名前を入れる)
                    **({"unit_code": unit_code} if unit_code else {}),
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
        _record(locked, user, before=before)

    # 注文回答を相手へ返す。HTTP は atomic の外 (R17)。届かなくても例外にしない (受け入れは成立する)。届かなければ黄の知らせ。
    undelivered = False
    if send:
        replied = send_reply(locked, user)
        undelivered = _reply_undelivered(replied)
        _with_reply_notice(order, replied)
    if answers is not None:
        # 届かなかったときは「相手に返しました」と書かない字にする (後ろに続く黄の知らせ「注文回答は相手に届いていません。」と食い違わせない)
        if conditional:
            order.saved_message = MESSAGE_CONDITIONAL_SAVED_UNDELIVERED if undelivered else MESSAGE_CONDITIONAL_SAVED
        else:
            order.saved_message = MESSAGE_CONDITIONAL_UNCHANGED_UNDELIVERED if undelivered else MESSAGE_CONDITIONAL_UNCHANGED
    return order


def accept_with_conditions(inbox, item_mapping, answers, user, *, send=True):
    """受信した注文の単価・数量・納期を明細ごとに直して受け入れ、直した値で受注を作る。返す物は作った Order。

    中小企業共通 EDI の条件付受諾の項目を元にした (ガイドブック 3.2.2。回答納期・回答数量・回答単価を返し、発注者が合わせて注文を変更する)。
    回答した値で受注を作る。R10 (人が受け入れるまで受注にならない) と R11 は accept と同じ。
    answers は {明細の並びの番号 (1 から): {"unit_price": Decimal か None, "qty": Decimal, "due_date": date か None}}。
    unit_price が None (金額の閲覧の無い人の form) なら相手の単価のまま、due_date が None なら注文の納期のまま。
    相手の注文と違う明細は条件付受諾 (CA) と回答単価・回答数量・回答納期の 3 つ、同じ明細は受諾 (AP) で返す。
    どの値も直していなければ受諾として返す (条件付のまま返すと、相手の発注が違う値の無い条件付になり、何を直すか分からないため。作者の決め (11-07 06:46))。
    返す受注の saved_message に、条件付で返したか受諾で返したかの知らせの字を付ける。
    訳: 相手の注文の単価・数量・納期のままでは受けられないときに、直した値で受注を作って相手に返すため。出所 = 作者の決め (11-07 00:0x)。
    変えてよい条件 = 条件付受諾の範囲の決めが変わったとき。
    """
    return _accept(inbox, item_mapping, user, answers=answers or {}, send=send)


def hold(inbox, user, *, send=True):
    """受信した注文に保留 (AJ) と答え、相手へ返す。受注は作らず、状態は「未処理」のまま。返す物は Inbox。

    中小企業共通 EDI の保留 (AJ) の項目を元にした。受注を作らない。
    答え直した時に前の答えの中身を残すのは変更の履歴の仕組みの仕事 (電子帳簿保存法。EDI の取引データを全部残す = ガイドブック 3.2.2)。
    保留を 2 回押しても断らない (2 回目は新しい知らせとして、新しい番号で送る = _restart_reply)。
    送るのは atomic の外 (R17)。返す受信した注文の saved_message に、保留にした知らせの字を付ける。
    訳: すぐ答えられない注文に、受注を作らずに保留と相手へ知らせるため。「未処理」の物だけを保留にする (受け入れた・断った注文に保留を返すと記録が食い違うため)。出所 = 作者の決め (11-07 00:0x)。
    変えてよい条件 = 保留の扱いの決めが変わったとき。
    """
    with transaction.atomic():
        locked = Inbox.objects.select_for_update().get(pk=inbox.pk)
        before = _saved(locked)
        if locked.status != Inbox.STATUS_RECEIVED:
            raise ValueError(
                f"保留できません。この注文のデータは「{locked.get_status_display()}」です。"
                "保留できるのは「未処理」の物だけです。"
            )
        fields = ["reply_type", "reply_lines", *_restart_reply(locked)]
        locked.reply_type = edi_codes.REPLY_PENDING
        locked.reply_lines = []
        locked.save(update_fields=fields)
        _record(locked, user, before=before)

    # 注文回答を相手へ返す。HTTP は atomic の外 (R17)。届かなくても例外にしない (保留は成立する)。届かなければ黄の知らせ。
    saved = locked
    if send:
        saved = send_reply(locked, user)
        _with_reply_notice(saved, saved)
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
    保留 (AJ) を答えた版を受け入れる時は、保留の答えのタイプを空に戻し、注文回答の番号を新しくする (保留の番号のまま送ると、
    相手の受け口が同じ番号の 2 回目と見て当てず、相手の発注には「相手が保留した」のまま残るため。511 回目)。
    訳: 受信した注文を人が受け入れたときだけ受注を変え (R10)、同じ版を 2 回受け入れないため (R11)。受ける側が手で直した明細も注文の値で上書きするのは、中小企業共通 EDI の注文変更 (方式 2) が変更の後の注文の全体を送るため。出所 = RULES.md R10・R11 と作者の決め (11-07 06:35。受け入れる前の画面で受注と違う値を全部出す)。
    変えてよい条件 = 作者の決めの答えが変わったとき (R10・R11 は変えない)。
    元の行の鍵は受信した注文の行だけにかける (select_for_update の of=("self",))。受注の行の鍵は orders.services の update_order・cancel_order が取る。order は空になれる列で、PostgreSQL は外部結合の空になれる側に FOR UPDATE をかけられないため (postgres の CI が落ちた。11-07)。
    """
    from django.utils import timezone

    with transaction.atomic():
        locked = Inbox.objects.select_for_update().get(pk=inbox.pk)
        before = _saved(locked)
        if locked.status == Inbox.STATUS_ACCEPTED:
            raise RuleViolation("R11", "受け入れられません。" + MESSAGE_ALREADY_ACCEPTED)
        if locked.status == Inbox.STATUS_REJECTED:
            raise ValueError("受け入れられません。" + MESSAGE_ACCEPT_REJECTED)
        base = Inbox.objects.select_for_update(of=("self",)).select_related("order").get(pk=locked.revises_id)
        if base.status == Inbox.STATUS_RECEIVED:
            raise ValueError(f"受け入れられません。先に第 1 版の注文 (受信した注文 {base.pk}) を受け入れてください。")
        payload = locked.payload if isinstance(locked.payload, dict) else {}
        problem = _revision_problem(base, payload, locked.revision_no, locked.purpose_code, hide_money=access.hides_money(user))
        if problem:
            raise ValueError(f"受け入れられません。{problem}この変更は断ってください。")
        if locked.purpose_code == edi_codes.PURPOSE_CANCEL:
            order = cancel_order(base.order, user, reason=f"得意先の注文の取消 (注文履歴番号 {locked.revision_no})")
        else:
            order = update_order(base.order, {}, _revision_lines(base, payload, item_mapping, user), user)
        locked.status = Inbox.STATUS_ACCEPTED
        locked.accepted_at = timezone.now() if accepted_at is None else accepted_at
        fields = ["status", "accepted_at"]
        # 保留 (AJ) を答えた後の受け入れは、別の知らせとして新しい番号で送る (_restart_reply。第 1 版の受け入れと断りと同じ)。
        # 版の受諾は答えのタイプを書かない (send_reply が状態から受諾と読む) ので、保留のタイプも空に戻す。
        if locked.reply_type:
            fields += ["reply_type", "reply_lines", *_restart_reply(locked)]
            locked.reply_type = ""
            locked.reply_lines = []
        if not send:
            locked.reply_response = MESSAGE_REPLY_SEED
            fields.append("reply_response")
        locked.save(update_fields=fields)
        _record(locked, user, before=before)

    # 注文回答を相手へ返す。HTTP は atomic の外 (R17)。届かなくても例外にしない (受け入れは成立する)。届かなければ黄の知らせ。
    if send:
        _with_reply_notice(order, send_reply(locked, user))
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
        unit_code = _known_unit_code(raw)
        lines.append(
            {
                "item": item,
                "unit": unit if isinstance(unit, str) and unit else ("" if unit_code else item.unit),
                "customer_part_no": part_no,
                "line_no": number,
                **_revision_values(raw, number),
                **({"unit_code": unit_code} if unit_code else {}),
            }
        )
    return lines


def _known_unit_code(raw):
    """注文の明細 1 行の unit_code が数量単位の一覧にあるコードなら、そのコード。無い・字でない・一覧に無いコードは ""。

    受け入れは断らない (今の受け入れは単位を見ずに通す。一覧に無いコードは使わず、名前から引く)。
    相手のコードを先に使う訳: seizou どうしでは名前とコードがそろうが、EDI の基盤から届く注文は日本語の名前がそろわず、
    コード (国際標準の値) だけが正しいことがあるため (作者の決め)。
    """
    code = raw.get("unit_code")
    return code if isinstance(code, str) and unit_name_for(code) else ""


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
        before = _saved(locked)
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
        _record(locked, user, before=before)

    # 注文回答を相手へ返す。HTTP は atomic の外 (R17)。届かなくても例外にしない (断りは成立する)。届かなければ黄の知らせ。
    return _with_reply_notice(locked, send_reply(locked, user))


def choose_sender(inbox, partner, user):
    """差出人の確認待ちの受信した注文 (入口 = 読み取り) に、差出人の得意先を選ぶ。返す物は Inbox。

    選べるのは入口が読み取りで状態が「届いた」の行だけ。取引先が当たった行の付け直しもできる (名前の当て違いを直すため)。
    入口が直接の行は署名で取引先が決まるので断る (R12)。得意先でない取引先も断る。誤りは日本語の ValueError。
    user は記録のために受ける。
    """
    with transaction.atomic():
        locked = Inbox.objects.select_for_update().get(pk=inbox.pk)
        before = _saved(locked)
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
        _record(locked, user, before=before)
    logger.info("差出人を選んだ: 受信した注文 %s 取引先 %s", locked.pk, partner.code)
    return locked


MESSAGE_REPLY_BEFORE_DECISION = (
    "この受信した注文は、まだ受け入れも断りもしていません (保留とも答えていません)。"
    "受け入れる・条件を直して受け入れる・保留する・断るのどれかで答えてから送ってください。"
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

    connection = _send_connection(inbox.partner)
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

    訳: 単価の単位を送らない相手 (前の版の seizou) の注文を今と同じ意味で受け、読めない値の注文は数量・単価・納期と同じく断って送り直してもらうため。出所 = 作者の決めと、今の受け入れの決まり (_payload_decimal・_payload_date)。
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
    is_finite() だけでは 1e20 と 0.001 が通り、表に入れた後で読むと 500 になる (作者の決め)。
    訳: 受信した注文の数が表の欄の桁に入るかを見て、桁を超える数を表に入れた後で読むと 500 になるのを防ぐため。出所 = AI の案 (docs/nights/20261103/N74.md。作者の決め)。
    変えてよい条件 = Issue「決まりの提案」でこの訳に対して議論してから。
    """
    if not isinstance(number, Decimal) or not number.is_finite() or number <= 0:
        return False
    if number >= Decimal(10) ** (field.max_digits - field.decimal_places):
        return False
    return number == number.quantize(Decimal(1).scaleb(-field.decimal_places))


def _order_lines_problem(payload, head="注文の"):
    """注文のデータの明細の数量・単価・金額 (数量 × 単価) (単価の単位が式の行は見ない) が、受注の明細の表の欄に入らないときの日本語の文。入れば None。

    head は文の頭の字 (既定は「注文の」。条件を直して受け入れるの直した値は「直した値の」)。

    見るのは読める数 (字か整数で、有限の数) のうち 0 より大きい物だけ。読めない数・0 と負・明細の無い注文・形の違う行は見ない
    (承認の時に accept と create_order が止める = 開発の記録 docs/nights/20261103/N39.md の決めと R02)。受け口 (receive) と承認 (accept) が呼ぶ。
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

    send_order・send_reply・send_acceptance・send_shipping_notice が呼ぶ。無いときの扱い (例外か返事の欄の文か) は呼び手が決める。
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
    options は requests.post に渡す (待ちの timeout と、返事の本文と添付の中身を少しずつ読む stream)。呼び手は transaction.atomic の外から呼ぶ (R17)。
    _deliver (注文・注文回答・検収・出荷の知らせ・会話の言葉) と fetch_chat_file (会話の添付を取りに行く) が呼ぶ。
    3xx の転送には付いて行かない (allow_redirects=False)。3xx の返事はそのまま呼び手へ返し、呼び手が受け取ったにしない。
    送る先を外の名前に絞る設定 (_public_only) が真なら、送る前に名前を引き、社内か手元の番地を指すときは送らない (unreachable)。
    受け口の URL が空のつながりは送らずに MESSAGE_NO_URL を返す (空の URL を requests に渡すと送る途中の誤りの文になり、直す所が分からないため)。
    訳: 3xx の転送に付いて行くと、道を誤った http の URL で届いていない注文を受け取ったと記録しうる (POST が GET に変わり、ログインの画面の 200 で終わる) ので、転送を追わず、返事の印 (status と送った番号) が合うときだけ受け取ったにする。出所 = エンタープライズ化の調べを受けた作者の決め (11-07)。
    変えてよい条件 = 相手の受け口が転送を返す形を、本人が認めたとき。
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

    if not connection.url:
        # 空の URL を requests に渡すと「送る途中で誤りが起きました」になり、直す所が分からない。送らずに直す所を返す。
        return None, (MESSAGE_NO_URL, "unreachable")

    if _public_only():
        # 送る先を外の名前に絞る設定のときだけ、送る前に名前を引く (偽のときは引かない = 今のまま)。
        private = _private_destination(connection.url)
        if private is not None:
            return None, (private, "unreachable")

    try:
        answer = requests.post(
            connection.url,
            data=raw,
            headers={**signed, "Content-Type": "application/json"},
            allow_redirects=False,
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
    # 署名を作れて POST する直前に、送り合った記録に送ったとおりの字で 1 行作り、返事が決まったら結果の字にする (鍵が無い・形が違う物は送らないので作らない)
    exchange = _exchange_sent(connection, message_id, raw)
    result = _deliver_result(connection, message_id, raw)
    _exchange_result(exchange, result[0])
    return result


#: 送る口が返事の本文を読む上限 (bytes)。受け口の返事は小さな JSON なので、超える物は読めない返事にする
ANSWER_MAX_BYTES = 64 * 1024
#: 送る口の全体の締切 (秒。送り始めから返事の本文を読み終わるまで)。1 回の待ちの timeout=10 とは別
DELIVER_DEADLINE_SECONDS = 30
#: 会話の添付を取りに行く口の全体の締切 (秒。要求を送り始めてから中身を読み終わるまで)
FETCH_DEADLINE_SECONDS = 120


class _AnswerTimeout(Exception):
    """返事の本文を読む途中で、全体の締切を過ぎた (_read_answer が投げ、_deliver_result が届かない字にする)。"""


def _read_answer(answer, started):
    """stream=True で受けた返事の本文を、最初の ANSWER_MAX_BYTES だけ iter_content で読む。

    返す物は読んだ bytes か、上限を超えたら None。started (time.monotonic の値) から DELIVER_DEADLINE_SECONDS を
    過ぎたら読むのをやめて _AnswerTimeout を投げる。close は呼び手 (_deliver_result) が必ず呼ぶ。
    """
    body = bytearray()
    for chunk in answer.iter_content(chunk_size=8 * 1024):
        body.extend(chunk)
        if len(body) > ANSWER_MAX_BYTES:
            return None
        if time.monotonic() - started > DELIVER_DEADLINE_SECONDS:
            raise _AnswerTimeout
    return bytes(body)


def _json_or_none(body: bytes):
    """返事の本文の bytes を JSON として読む。読めなければ None。"""
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        return None


def _close_answer(answer) -> None:
    """stream=True で受けた返事を閉じる (つながりを返す)。close の無い物は何もしない。"""
    close = getattr(answer, "close", None)
    if callable(close):
        close()


def _deliver_result(connection, message_id, raw) -> tuple[str, str]:
    """_post_signed で送り、返事を (日本語の文, 種類) にする (_deliver の中身)。

    3xx は受け取ったにせず unreadable (相手の受け口の URL が変わっている)。200 は本文の JSON が受け取った印
    (_is_received_mark) のときだけ received、ほかは unreadable。新しい種類は作らない (呼び手は received だけを見る)。
    返事は stream=True で受け、200 と 400 の本文は最初の ANSWER_MAX_BYTES (64KB) だけを読む (_read_answer)。
    64KB を超える本文は unreadable。送り始めから DELIVER_DEADLINE_SECONDS (30 秒) を過ぎたら読むのをやめて unreachable。
    1 回の待ちの timeout=10 はそのまま。返事は読み終わりに必ず閉じる。
    404 は、受け口の URL の道の誤りが分かる文にする (種類は unreadable のまま)。
    訳: 3xx の転送に付いて行くと、道を誤った http の URL で届いていない注文を受け取ったと記録しうる (POST が GET に変わり、ログインの画面の 200 で終わる) ので、転送を追わず、返事の印 (status と送った番号) が合うときだけ受け取ったにする。出所 = エンタープライズ化の調べを受けた作者の決め (11-07)。
    変えてよい条件 = 相手の受け口が転送を返す形を、本人が認めたとき。
    訳: 相手の返事の本文がとても大きいか、少しずつ終わらずに届くと、送る口の thread が止まらず保存の画面も返らないので、本文の上限と全体の締切で読むのをやめる。出所 = エンタープライズ化の調べを受けた作者の決め。
    変えてよい条件 = 未確認 (依頼書に書いていない)。
    限り: gunicorn の gthread の --timeout が遅い thread を止めないことを前提に置く (未確認)。
    """
    # 試験が requests.post を差し替えるため、関数の中で import する (_post_signed と同じ)。
    import requests

    started = time.monotonic()
    answer, failed = _post_signed(connection, message_id, raw, timeout=10, stream=True)
    if failed is not None:
        return failed
    try:
        status = answer.status_code
        if 300 <= status < 400:
            return (
                f"届きません: 相手の受け口の URL が変わっています (HTTP {status})。つながりの URL を直してください。",
                "unreadable",
            )
        if status == 404:
            return MESSAGE_NOT_FOUND, "unreadable"
        if status not in (200, 400):
            return f"返事が読めません (HTTP {status})", "unreadable"
        try:
            body = _read_answer(answer, started)
        except _AnswerTimeout:
            return (
                f"届きません: 相手の返事が終わらず、時間切れです ({DELIVER_DEADLINE_SECONDS} 秒)。相手が立っているかを確かめてください。",
                "unreachable",
            )
        except requests.RequestException as exc:
            return _unreachable_reply(exc), "unreachable"
        if body is None:
            return f"返事が読めません (HTTP {status}。本文が {ANSWER_MAX_BYTES // 1024}KB を超えています)", "unreadable"
        data = _json_or_none(body)
        if status == 200:
            if _is_received_mark(data, message_id):
                return "受け取った", "received"
            return "返事が読めません (HTTP 200。受け取った印が合いません)", "unreadable"
        # 400 は返事の JSON の message (200 字まで)。読めなければ決まった文。
        refused = data.get("message", "") if isinstance(data, dict) else ""
        if not isinstance(refused, str) or not refused:
            refused = "理由が読めません"
        return f"断られた: {refused[:200]}", "rejected"
    finally:
        _close_answer(answer)


def _is_received_mark(data, message_id) -> bool:
    """200 の返事の本文 (JSON を読んだ物) が受け取った印か。status が received で、id があればそれが送った番号の字と同じ。

    id の無い {"status": "received"} は受け取ったにする (今までの返事の形と、既にある試験の偽物の既定の返事のため)。
    """
    if not isinstance(data, dict) or data.get("status") != "received":
        return False
    return "id" not in data or data["id"] == str(message_id)


# ---------------------------------------------------------------- 送り合った記録 (作者の決め)

#: 送り合った記録の結果の字 (送る前・受けて確かめる前)
EXCHANGE_SENDING = "送っています"
EXCHANGE_RECEIVING = "受けています"
#: 送り合った記録に残さない kind (会話の添付を取りに来た要求。取引の文書でなく、送った側も残さない)
EXCHANGE_SKIPPED_KINDS = frozenset({"chat_file"})
#: 金額の列の桁 (Exchange.amount の max_digits=20・decimal_places=4 = 小数 4 桁と整数 16 桁)
_AMOUNT_PLACES = 4
_AMOUNT_INTEGER_DIGITS = 16


def _record(obj, user, *, before=None, by=""):
    """変更の履歴に残す (core.services.record_history)。冒頭の import の並びを変えないため、関数の中で import する。"""
    from seizou.core.services import record_history

    return record_history(obj, user, before=before, by=by)


def _saved(obj):
    """書く前の値 (core.history.saved_state)。冒頭の import の並びを変えないため、関数の中で import する。"""
    from seizou.core import history

    return history.saved_state(obj)


def _json_dict(raw):
    """本文の bytes か字を JSON として読んだ辞書。読めないか辞書でなければ None。"""
    try:
        data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _uuid_or_none(value):
    """番号の uuid。uuid の形でなければ None。"""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _kind_of(data) -> str:
    """送り合った記録の種類の字 (本文の kind。kind の無い注文は "order"。読めない本文は空)。"""
    if data is None:
        return ""
    kind = data.get("kind")
    if kind is None:
        return "order"
    return str(kind)[:35]


def _order_amount(data):
    """注文 (注文の変更と取消の版も) の金額 = 明細ごとの金額の合計 (丸めない)。読めない明細があれば None。

    明細の金額は数量 × 単価。式の行 (price_basis が "lump") は数量を掛けず単価のまま (作者の決め)。
    取り消した明細 (明細状態コード 2) は合計に入れない (全部の明細を取り消した取消の版は 0)。
    """
    lines = data.get("lines")
    if not isinstance(lines, list):
        return None
    total = Decimal(0)
    for raw in lines:
        if not isinstance(raw, dict):
            return None
        if _is_deleted(raw):
            continue
        if isinstance(raw.get("qty"), bool) or isinstance(raw.get("unit_price"), bool):
            return None
        try:
            qty = Decimal(str(raw["qty"]))
            price = Decimal(str(raw["unit_price"]))
        except (KeyError, InvalidOperation, ValueError, TypeError):
            return None
        if not (qty.is_finite() and price.is_finite()):
            return None
        total += price if raw.get("price_basis") == PRICE_BASIS_LUMP else qty * price
    return total


def _self_invoice_amount(data):
    """仕入明細 (修正と取消の版も) の金額 = 本文の合計 (total) の字を Decimal にした物 (丸めない)。読めなければ None。

    電子帳簿保存法の電子取引のデータを取引金額で探せるようにする (送り合った記録の金額。作者の決めの「本文に合計の金額がある物はその値」)。
    """
    value = data.get("total")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


#: 種類 (本文の kind) → 金額を数える関数。無い種類は金額が空。後の依頼が自分の kind の行を足す (本文に合計の金額がある物はその値)
EXCHANGE_AMOUNT_KINDS = {"order": _order_amount, "self_invoice": _self_invoice_amount}


def _fits_amount(value) -> bool:
    """金額を丸めずに列 (小数 4 桁・整数 16 桁) に持てるか。"""
    _sign, digits, exponent = value.normalize().as_tuple()
    if not isinstance(exponent, int) or exponent < -_AMOUNT_PLACES:
        return False
    return len(digits) + exponent <= _AMOUNT_INTEGER_DIGITS


def exchange_amount(data):
    """送り合った記録の金額。本文の JSON を読んだ辞書を受け、Decimal か None を返す (読むだけ。表に書かない)。

    電子帳簿保存法: 送り合った電子取引のデータを取引金額で探せるようにするための金額。送った本文と受けた本文から同じ決まりで
    数える (作者の決め)。注文 (kind の無い注文と、注文の変更・取消の版も) は明細の金額の合計を丸めずに返す (_order_amount)。
    ほかの種類 (注文回答・検収・出荷の知らせ・会話) は None (金額の無い物は空。注文回答に回答単価があっても空)。
    読めない値 (lines が並びでない・明細が辞書でない・数量か単価が無いか数として読めない) と、丸めずに持てない値
    (小数 5 桁以上・桁の上限を超える) も None (行は残し、金額だけ空)。
    訳: 送った側と受けた側が同じ本文から同じ関数で数え、2 社の送り合った記録の金額を同じ値にするため。丸めないのは、取引先ごとに
    違う端数の決まりを探すための金額に持ち込まないため。読めない値を空にするのは、受けた物の形の誤りで送り合った記録が残らなく
    なるのを避けるため。出所 = 持ち主の決めと、作者の決め (式の行の扱いと、取り消した明細の版の決め)。
    変えてよい条件 = 送る本文に合計の欄を足したとき (その欄を読む形に替える)。
    """
    if not isinstance(data, dict):
        return None
    reader = EXCHANGE_AMOUNT_KINDS.get(_kind_of(data))
    if reader is None:
        return None
    value = reader(data)
    if value is None or not _fits_amount(value):
        return None
    return value


def _exchange_sent(connection, message_id, raw):
    """送る直前の送り合った記録の 1 行 (結果は「送っています」)。鍵が無いか鍵の形が違って署名を作れない (送らない) ときは作らず None。

    atomic の外から呼ぶ (R17 の _deliver の中。行は autocommit で 1 回ずつ書く)。鍵の値と見出しの署名は表に書かない。
    """
    secret = os.environ.get(connection.secret_env)
    if not secret:
        return None
    try:
        signing.headers_for(secret, str(message_id), raw)
    except ValueError:
        return None
    data = _json_dict(raw)
    kind = _kind_of(data)
    if kind in EXCHANGE_SKIPPED_KINDS:
        return None
    return Exchange.objects.create(
        direction=Exchange.DIRECTION_SENT,
        kind=kind,
        message_id=_uuid_or_none(message_id),
        partner=connection.partner,
        body=raw.decode("utf-8"),
        amount=exchange_amount(data),
        result=EXCHANGE_SENDING,
    )


def _exchange_received(partner, verified_id, raw_body: bytes):
    """署名が通った受けた物の送り合った記録の 1 行 (結果は「受けています」)。添付を取りに来た要求 (chat_file) は作らず None。"""
    data = _json_dict(raw_body)
    kind = _kind_of(data)
    if kind in EXCHANGE_SKIPPED_KINDS:
        return None
    return Exchange.objects.create(
        direction=Exchange.DIRECTION_RECEIVED,
        kind=kind,
        message_id=_uuid_or_none(verified_id),
        partner=partner,
        body=raw_body.decode("utf-8", errors="replace"),
        amount=exchange_amount(data),
        result=EXCHANGE_RECEIVING,
    )


def _exchange_result(exchange, text: str) -> None:
    """送り合った記録の結果の字を書く (結果の列だけ。ほかの列は直さない)。行が無ければ何もしない。"""
    if exchange is not None:
        Exchange.objects.filter(pk=exchange.pk).update(result=str(text)[:500])


def send_order(purchase_order, user):
    """発注を注文のデータにし、署名して相手の受け口へ送る。送った物は Outbox に 1 行残して返す。

    - つながりは発注の取引先の、向き「送る」の物。無ければ ValueError。発注が「取消」でも ValueError。
    - 本文は鍵の並びを固定した JSON の bytes。署名はこの bytes のまま (整形し直さない)。
    - 明細の price_basis は単価の単位 (unit = 部品の単位 1 つあたり・lump = 式。金額に数量を掛けない)。中小企業共通 EDI の注文単価は
      1 単位あたりの単価で、式に当たる数量単位コードと単価コードは無いので、seizou の項目として送る。
    - 明細の process_name は発注の明細の工程の名前 (中小企業共通 EDI の工程名を元にした鍵)、quote_no は単価表から写した見積の番号
      (同じく (明細行参照) 見積回答書ID を元にした鍵)。どちらも無ければ空の字 (作者の決め)。
    - 明細の unit_code は中小企業共通 EDI の注文数量単位コード (付表 1。値は数量単位の一覧 = masters.QuantityUnit のコード) の項目を元にした値。
      空は単位の指定なし (EDI では H87 (個) の既定で読まれる)。作者の決め。
    - 相手への HTTP は transaction.atomic の外で送る (R17)。待ちは 10 秒。
    - 相手の返事は日本語の短い文で Outbox.response に書く。200 (受け取った) なら発注を「送った」にする。
      鍵が無い・断られた・届かないときは例外にせず、発注は「作成」のまま (人が「送り直す」を押す)。
    - 送り直しは 1 回目の番号 (webhook-id) を使い回す。相手の受け口は同じ番号を 1 件にまとめる (R11) ので、
      2 つのタブから送り直しても相手の受信箱に 2 行できない (作者の決め)。Outbox には送るたびに 1 行足す。
    - 届かないときの返事は日本語 (例外の英語の名前を画面に出さない)。
    - 鍵の値・本文・見出しの値をログと返す物に出さない。
    - 版 (中小企業共通 EDI の注文変更の方式 2): 番号は同じ発注の同じ版 (revision_no) の最初の行の番号を使い回し、無ければ新しい uuid4
      (版ごとに新しい番号。同じ版の送り直しは同じ番号)。本文の order_id は第 1 版の番号 (注文キー。第 1 版は id と同じ)、
      order_revision_no は発注の今の版、purpose_code は注文書目的コード。明細は取り消した明細 (2) も含めて全部の行を行番号の順に載せ、
      明細状態コードと明細状態理由コードを付ける。届いたときに発注を「送った」にするのは第 1 版だけ (変更の版は発注の状態を変えない)。
      取消の発注は、取消を送る版 (目的 "1") だけ送れる。
    訳: 同じ版の送り直しを相手が 1 件にまとめ (R11)、版が変わったら新しい知らせとして届けるため (中小企業共通 EDI のガイドブック 3.2.1 の注文変更方式 2)。出所 = 中小企業共通 EDI と作者の決め (11-06 23:1x)。
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
            "unit_code": line.unit_code,
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


# --- 受注と発注の 1 件ごとの会話 (作者の決め) ---
# 意図: 受注と発注のそれぞれで相手と話し、両方の会社の seizou に同じ会話を残すため (本人の決め)。
# 相手に届かなかった言葉は「届いていない」と出し、送り直せる (注文の送り直しと同じ作り = 番号の使い回しと受け口の 1 件にまとめる)。

#: 会話が使えない訳の字 (見る順は NOT_CONNECTED → NOT_LINKED → NO_SEND → NOT_SENT)。
#: NOT_CONNECTED は本人の決めの添え書きの字のまま。ほかの 3 つは、その字が事実と合わない取引のために足した字 (AI の案)。
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
    訳: 社内のつもりで書いた字が、相手とつながった後に相手へ届く事故を防ぐため、seizou どうしでつながっていない取引では会話を使えなくする。出所 = 本人の決め 11-06 23:4x〜23:5x「社内だけは事故の元かもな / 繋がっていないで伝達用に打っていたら繋がっていた場合筒抜けでしょう？」。
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
    訳: 会話の添付の形式と大きさを、今の添付と同じ決まりで止めるため (決まりを 2 か所に書かない)。出所 = 本人の決め (11-06 23:5x) の「上限は今の添付と同じ 20MB」。
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
    - 添付 (file) は 1 つまで (MAX_CHAT_FILES。受け口も同じ数で断る)。形式と大きさは今の添付と同じ決まり (_check_chat_file)。中身の印 (SHA-256) を作り、言葉の行と同じ atomic で
      ChatFile の行を作ってファイルを storage に置く (attachments.services.add_attachment と同じ形)。送る JSON には項目だけを載せる。
    - 行は atomic の中で作り (返事の欄は「送っています」)、atomic の外で相手へ送る (R17)。返事の字を書き、届いたときだけ相手に届いた日時を書く。
    - 届かなくても例外にしない (書いた言葉は残る)。返す物の saved_message が知らせの字、届かなければ saved_level = "warning"。
    - 鍵の値・本文・見出しの値をログと返す物に出さない。言葉を直す関数と消す関数は作らない (ChatMessage の docstring)。
    訳: 会話が使えない取引と、空の言葉と 2000 字を超える言葉を送らないため。出所 = 本人の決め 11-06 23:4x〜23:5x と AI の案 (2000 字。根拠の資料は無い。中小企業共通 EDI に会話は無く、字数の決まりも無い。記録 = docs/nights/20261106/N311.md)。
    変えてよい条件 = 字数は、相手の seizou も同じ字数を受けられる版になってから (受け口が同じ字数で断るため)。
    添付は形と大きさを今の添付と同じ決まり (attachments.services の ALLOWED・MAX_BYTES) で見る (出所 = 本人の決めの「上限は今の添付と同じ 20MB」)。
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
    訳: 相手から届いた言葉と、もう届いた言葉を送り直さないため (送り直しは、届いていない自社の言葉を同じ番号でもう 1 度送る物)。出所 = 本人の決め 11-06 23:2x (作者の決めの添え書き「相手に届かなかった言葉は「届いていない」と出し、送り直せる」) と AI の案 (記録 = docs/nights/20261106/N311.md)。
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
      要求を送り始めてから FETCH_DEADLINE_SECONDS (120 秒) を過ぎたら読むのをやめ、「まだ受け取っていない: 届きません: …時間切れ…」を書く。
    - 200 で、読んだ大きさと中身の印 (SHA-256) が相手の書いた値と同じときだけファイルを置き「受け取った」を書く。ほかは「まだ受け取っていない: <理由>」。
    - 受け口の中では取りに行かない (送り手は 10 秒で返事を待つのをやめるため)。受け手の画面が開いた時と「取り直す」で呼ぶ。
    訳: 相手の seizou に置いた添付を、署名つきの要求で取りに行って自社の側にも残し、中身が相手の書いた大きさと印に合う時だけ受け取ったとするため。出所 = 本人の決め (作者の決め「推奨で」11-06 23:5x) と中小企業共通 EDI 本編 6.4.1 の URL 記載方式。
    変えてよい条件 = 本人が添付の方式を決め直したとき。
    訳: 相手が中身を少しずつ終わらずに送ると、取りに行く画面の thread が止まらないので、全体の締切で読むのをやめる。出所 = エンタープライズ化の調べを受けた作者の決め。
    限り: gunicorn の gthread の --timeout が遅い thread を止めないことを前提に置く (未確認)。
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
    started = time.monotonic()
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
            if time.monotonic() - started > FETCH_DEADLINE_SECONDS:
                return _write_fetch(
                    current,
                    f"まだ受け取っていない: 届きません: 相手の返事が終わらず、時間切れです ({FETCH_DEADLINE_SECONDS} 秒)。取り直してください。",
                )
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


# ---- 上の帯の知らせの鈴 (core.bell が呼ぶ読むだけの 2 つ。受け渡しの seizou-部品の6案-20261107/指示書.md の 4) ----

BELL_TITLE = "受信した注文が届きました"


def _bell_visible(user) -> bool:
    """受信した注文を見られる人か (左のメニューと同じ判定 = 部署で connect を開ける人。記録は書かない)。未ログインは見られない。"""
    return getattr(user, "is_authenticated", False) and access.can_open(user, "connect", record=False)


def bell_count(user, since) -> int:
    """since より後に届いた受信した注文の数 (鈴の赤い数。件数だけ数える)。見られない人は 0。読むだけ。"""
    if not _bell_visible(user):
        return 0
    return Inbox.objects.filter(received_at__gt=since).count()


def bell_notices(user, *, limit=10) -> list[dict]:
    """鈴の箱に並べる受信した注文 (新しい順に limit 件)。1 件 = {"when", "title", "text", "url", "icon"}。見られない人は空の並び。読むだけ。"""
    if not _bell_visible(user):
        return []
    rows = Inbox.objects.select_related("partner").order_by("-received_at", "-pk")[:limit]
    notices = []
    for row in rows:
        name = row.partner.name if row.partner_id else "差出人の確認待ち"
        summary = row.summary()
        notices.append(
            {
                "when": row.received_at,
                "title": BELL_TITLE,
                "text": f"{name} の{row.get_kind_display()}" + (f" ({summary})" if summary else ""),
                "url": reverse("connect:inbox_detail", args=[row.pk]),
                "icon": "inbox",
            }
        )
    return notices


# ---------------------------------------------------------------- 仕入明細 (作者の決め。中小企業共通 EDI の仕入明細の項目を元にした)

MESSAGE_SELF_INVOICE_NO_CONNECTION = (
    "取引先との接続 (送る向き) が無いので仕入明細を送っていません。"
    "設定の画面の「取引先との接続」で、この取引先に送る向きの接続を登録してください。"
)
MESSAGE_SELF_INVOICE_SHAPE = "仕入明細の形が読めません。self_invoice_no・revision_no・lines を送ってください。"
#: 仕入明細の文書の種類 (版 1 は仕入明細、版 2 からは修正仕入明細)
_SI_DOCUMENTS = frozenset({edi_codes.SI_DOC_NEW, edi_codes.SI_DOC_REVISED})


@dataclass(frozen=True)
class SelfInvoiceReceipt:
    """受け口が仕入明細を受けた仕入明細にした (か、同じ番号の 2 回目だった) 印。受け口の view は message_id だけを読む。"""

    message_id: uuid.UUID
    self_invoice: object


def _plain_number(value) -> str:
    """送る数の字 (区切りと ¥ を付けない。末尾の 0 は落とさない = 表の値のまま)。"""
    return str(value)


def _rate_text(value) -> str:
    """送る税率の字 (百分率。末尾の 0 を落とす。10.00 → "10")。"""
    return f"{value.normalize():f}"


def send_self_invoice(self_invoice, user):
    """仕入明細 (今の版) を署名して、取引先 (協力企業の seizou) の受け口へ送る。send_acceptance と同じ形。

    - 番号は purchasing.services.assign_self_invoice_number (今の版の番号。無ければ付けて保存。送り直しは同じ番号 = R11)。
    - つながりは取引先の向き「送る」の物。無ければ HTTP を送らず、返事の欄に MESSAGE_SELF_INVOICE_NO_CONNECTION を書いて返す。
    - 本文の鍵は id・kind ("self_invoice")・sent_at・self_invoice_no・revision_no・document_code (版 1 は "1"・版 2 からは "1C")・purpose_code・
      issued_on・period_from・period_to・due_on (空なら null)・subtotal・tax・total・tax_rate (百分率の字)・tax_category_code ("S")・
      supplier_name (取引先の名前)・supplier_registration_no・buyer・lines。明細の鍵は line_no・order_id (その発注の第 1 版の送った注文の番号)・
      order_no・order_line_no・order_revision_no (発注の今の版)・part_no・part_name・received_on・qty・unit・unit_code・price_basis・
      unit_price・amount・revised_in。金額と数は数の字 (¥ もカンマも付けない)。
    - 相手への HTTP は transaction.atomic の外 (R17)。送る所は send_order・send_acceptance と同じ _deliver。
    - 届かない・断られた・鍵が無い・つながりが無いのどれでも例外にしない (仕入明細は成立する)。返事と送った日時は
      purchasing.services.record_self_invoice_delivery が書く。返す物は取り直した SelfInvoice。
    インボイス制度の記載事項 (相手の名前と登録番号・税率と課税分類) を送る中身そのものに持つ。
    """
    from django.apps import apps
    from django.utils import timezone

    from seizou.purchasing import services as purchasing_services

    from .models import Outbox

    model = apps.get_model("purchasing", "SelfInvoice")
    current = (
        model.objects.select_related("partner")
        .prefetch_related("lines__receipt__line__purchase_order", "lines__receipt__line__item")
        .get(pk=self_invoice.pk)
    )
    message_id = purchasing_services.assign_self_invoice_number(current, user)
    connection = _send_connection(current.partner)
    if connection is None:
        return purchasing_services.record_self_invoice_delivery(current, MESSAGE_SELF_INVOICE_NO_CONNECTION, user)

    lines = []
    first_orders = {}
    for row in current.lines.all():
        order_line = row.receipt.line
        purchase_order = order_line.purchase_order
        if purchase_order.pk not in first_orders:
            first = Outbox.objects.filter(purchase_order=purchase_order, revision_no=1).order_by("pk").first()
            first_orders[purchase_order.pk] = str(first.message_id) if first else ""
        lines.append(
            {
                "line_no": row.line_no,
                "order_id": first_orders[purchase_order.pk],
                "order_no": purchase_order.po_no,
                "order_line_no": order_line.line_no,
                "order_revision_no": purchase_order.revision_no,
                "part_no": order_line.item.part_no,
                "part_name": order_line.item.name,
                "received_on": row.received_on.isoformat(),
                "qty": _plain_number(row.qty),
                "unit": row.unit,
                "unit_code": row.unit_code,
                "price_basis": row.price_basis,
                "unit_price": _plain_number(row.unit_price),
                "amount": _plain_number(row.amount),
                "revised_in": row.revised_in,
            }
        )
    data = {
        "id": str(message_id),
        "kind": "self_invoice",
        "sent_at": timezone.localtime().isoformat(),
        "self_invoice_no": current.self_invoice_no,
        "revision_no": current.revision_no,
        "document_code": edi_codes.SI_DOC_NEW if current.revision_no == 1 else edi_codes.SI_DOC_REVISED,
        "purpose_code": current.purpose_code,
        "issued_on": current.issued_on.isoformat(),
        "period_from": current.period_from.isoformat(),
        "period_to": current.period_to.isoformat(),
        "due_on": current.due_on.isoformat() if current.due_on else None,
        "subtotal": _plain_number(current.subtotal),
        "tax": _plain_number(current.tax),
        "total": _plain_number(current.total),
        "tax_rate": _rate_text(current.tax_rate),
        "tax_category_code": edi_codes.TAX_CATEGORY_STANDARD,
        "supplier_name": current.partner.name,
        "supplier_registration_no": current.supplier_registration_no,
        "buyer": _party(),
        "lines": lines,
    }
    raw = _encode(data)
    # R17: ここは atomic の外。
    reply, kind = _deliver(connection, message_id, raw)
    saved = purchasing_services.record_self_invoice_delivery(current, reply, user, sent=(kind == "received"))
    logger.info("仕入明細を送った: 仕入明細 %s 番号 %s 返事の種類 %s", current.pk, message_id, kind)
    return saved


def _receive_self_invoice_revision(message_id, partner, existing, values, payload):
    """修正仕入明細 (目的 4・版が今より大きい) を、受けた仕入明細に当てる。返す物は SelfInvoiceReceipt か断る文。

    明細を形と受注の明細で確かめてから (_self_invoice_line_values)、1 つの atomic の中で、見出しの列 (版・文書の種類・目的・小計・消費税・合計・番号) を
    書き替え、明細ごとに数・単価・金額のどれかが違えば値を書き替えて答えの列を空にし、直した版を新しい版にする (同じ明細の答えはそのまま)。
    状態を明細の答えから決め直す。行番号の無い明細は断る (修正仕入明細は明細を足さない)。
    中小企業共通 EDI の修正仕入明細の流れ: 問題なければ売り手は修正仕入明細回答で確認回答する (インボイス仕様編 6.1.1 の ④)。
    """
    from .models import ReceivedSelfInvoice

    msg_id = str(message_id)
    rows = []
    for number, raw in enumerate(payload["lines"], start=1):
        row = _self_invoice_line_values(partner, raw, number)
        if isinstance(row, str):
            return _refuse(row, msg_id)
        rows.append(row)
    with transaction.atomic():
        locked = ReceivedSelfInvoice.objects.select_for_update().get(pk=existing.pk)
        lines = {line.line_no: line for line in locked.lines.all()}
        unknown = [row["line_no"] for row in rows if row["line_no"] not in lines]
        if unknown:
            return _refuse(f"修正仕入明細の明細の行番号 {unknown[0]} が仕入明細 {locked.self_invoice_no} にありません。", msg_id)
        by = f"{partner.name} の seizou"
        for row in rows:
            line = lines[row["line_no"]]
            if (line.qty, line.unit_price, line.amount) == (row["qty"], row["unit_price"], row["amount"]):
                continue
            before = _saved(line)
            line.qty = row["qty"]
            line.unit_price = row["unit_price"]
            line.amount = row["amount"]
            line.revised_in = values["revision_no"]
            line.reply_type = ""
            line.reply_reason_code = ""
            line.reply_qty = None
            line.reply_unit_price = None
            line.reply_note = ""
            line.save(update_fields=[
                "qty", "unit_price", "amount", "revised_in", "reply_type", "reply_reason_code", "reply_qty", "reply_unit_price", "reply_note",
            ])
            _record(line, None, before=before, by=by)
        before = _saved(locked)
        for key in ("revision_no", "document_code", "purpose_code", "subtotal", "tax", "total"):
            setattr(locked, key, values[key])
        locked.message_id = message_id
        locked.status = _received_status(list(lines.values()))
        locked.save(update_fields=["revision_no", "document_code", "purpose_code", "subtotal", "tax", "total", "message_id", "status"])
        _record(locked, None, before=before, by=by)
    return SelfInvoiceReceipt(message_id=message_id, self_invoice=locked)


def _receive_self_invoice_cancel(message_id, partner, existing, values):
    """取消仕入明細 (目的 1・版が今より大きい) を、受けた仕入明細に当てる。返す物は SelfInvoiceReceipt か断る文。

    受けた仕入明細に答えを送ってあれば (答えの番号がある)、行を変えずに断る。答えを送っていなければ、1 つの atomic の中で
    状態を取消にし、版・文書の種類 (1C)・目的 (1)・番号を書く (明細は変えない)。
    訳: 受ける側が答えを送った後の取消を受けず、答えた明細と金額を動かさないため (送る側は届いた答えしか知らないので、受ける側でも見る)。出所 = 作者の決め。
    変えてよい条件 = 作者が答えた後の取消を決めたとき。
    """
    from .models import ReceivedSelfInvoice

    msg_id = str(message_id)
    with transaction.atomic():
        locked = ReceivedSelfInvoice.objects.select_for_update().get(pk=existing.pk)
        if locked.reply_message_id is not None:
            return _refuse(
                f"取り消せません。仕入明細 {locked.self_invoice_no} には第 {locked.revision_no} 版の答えを送ってあります。"
                "得意先と会話で話してください。",
                msg_id,
            )
        before = _saved(locked)
        locked.status = ReceivedSelfInvoice.STATUS_CANCELLED
        locked.revision_no = values["revision_no"]
        locked.document_code = values["document_code"]
        locked.purpose_code = values["purpose_code"]
        locked.message_id = message_id
        locked.save(update_fields=["status", "revision_no", "document_code", "purpose_code", "message_id"])
        _record(locked, None, before=before, by=f"{partner.name} の seizou")
    return SelfInvoiceReceipt(message_id=message_id, self_invoice=locked)


def _self_invoice_text_problem(payload):
    """仕入明細の字 (仕入明細番号・自社の名前・登録番号・課税分類コード・明細の発注番号・単位・数量単位コード) が、受ける表の欄の字数に入らないときの日本語の文。入れば None。

    上限は受ける欄 (connect.ReceivedSelfInvoice・ReceivedSelfInvoiceLine) の max_length。見る順は頭の鍵 → 明細を上から 1 行ずつ。最初に見つけた 1 つだけを返す。
    字数は len() で数える (1 字を 1 と数え、バイトで数えない)。字でない値は見ない (形の確かめが断る)。
    訳: 受けた仕入明細の字を切って入れて、別の値として残すのを防ぎ、送った側に直す所を返すため (受け取れる字数を超えた字は受け口で断る)。出所 = 作者の決め (注文の鍵を見る _order_text_problem と同じ形)。
    変えてよい条件 = Issue「決まりの提案」でこの訳に対して議論してから。変えたら docs/edi-items.md の「受け取れる字数を超えたとき」も直す。
    """
    from .models import ReceivedSelfInvoice, ReceivedSelfInvoiceLine

    if not isinstance(payload, dict):
        return None
    heads = (
        ("self_invoice_no", "仕入明細番号", ReceivedSelfInvoice, "self_invoice_no"),
        ("supplier_name", "自社の名前", ReceivedSelfInvoice, "supplier_name"),
        ("supplier_registration_no", "登録番号", ReceivedSelfInvoice, "supplier_registration_no"),
        ("tax_category_code", "課税分類コード", ReceivedSelfInvoice, "tax_category_code"),
    )
    for key, label, model, name in heads:
        value = payload.get(key)
        limit = model._meta.get_field(name).max_length
        if isinstance(value, str) and len(value) > limit:
            return f"仕入明細の{label}は {limit} 字までにしてください。"
    checks = (
        ("order_no", "発注番号", "buyer_order_no"),
        ("unit", "単位", "unit"),
        ("unit_code", "数量単位コード", "unit_code"),
    )
    lines = payload.get("lines")
    for number, raw in enumerate(lines if isinstance(lines, list) else [], start=1):
        if not isinstance(raw, dict):
            continue
        for key, label, name in checks:
            value = raw.get(key)
            limit = ReceivedSelfInvoiceLine._meta.get_field(name).max_length
            if isinstance(value, str) and len(value) > limit:
                return f"仕入明細の {number} 行目の{label}は {limit} 字までにしてください。"
    return None


def _positive_int(value):
    """1 以上の整数 (bool でない) ならその値、ほかは None。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _fits_money(number, field):
    """number (Decimal) が 0 以上で、表の欄 field (DecimalField) の桁に入るか (0 は入る。一式の途中の受入は 0 円)。"""
    if not isinstance(number, Decimal) or not number.is_finite() or number < 0:
        return False
    return number == 0 or _fits_field(number, field)


def _self_invoice_values(payload):
    """仕入明細の本文の頭の値を読んだ辞書。形が違えば断る文 (str)。_receive_self_invoice が呼ぶ。"""
    from .models import ReceivedSelfInvoice

    meta = ReceivedSelfInvoice._meta
    number = payload.get("self_invoice_no")
    revision_no = _positive_int(payload.get("revision_no"))
    lines = payload.get("lines")
    if not (isinstance(number, str) and number.strip()) or revision_no is None:
        return MESSAGE_SELF_INVOICE_SHAPE
    if not isinstance(lines, list) or not lines or not all(isinstance(raw, dict) for raw in lines):
        return MESSAGE_SELF_INVOICE_SHAPE
    purpose = payload.get("purpose_code", edi_codes.PURPOSE_NEW)
    if purpose not in _PURPOSES or (purpose == edi_codes.PURPOSE_NEW and revision_no != 1):
        return "仕入明細の目的コードか版が読めません。新規は目的 9 の第 1 版で送ってください。"
    document = payload.get("document_code")
    if document not in _SI_DOCUMENTS:
        return "仕入明細の文書の種類 (document_code) が 1 か 1C ではありません。"
    values = {"self_invoice_no": number.strip(), "revision_no": revision_no, "purpose_code": purpose, "document_code": document}
    try:
        for key, label in (("issued_on", "発行日"), ("period_from", "締め期間の初め"), ("period_to", "締め期間の終わり")):
            day = _payload_date(payload.get(key), label)
            if day is None:
                return MESSAGE_SELF_INVOICE_SHAPE
            values[key] = day
        values["due_on"] = _payload_date(payload.get("due_on"), "支払期日")
        for key, label in (("subtotal", "小計"), ("tax", "消費税"), ("total", "合計")):
            amount = _payload_decimal(payload.get(key), label)
            if not _fits_money(amount, meta.get_field(key)):
                return f"仕入明細の{label}は、0 以上で整数 12 桁・小数 2 桁までの数にしてください。"
            values[key] = amount
        rate = _payload_decimal(payload.get("tax_rate"), "税率")
        if not _fits_field(rate, meta.get_field("tax_rate")):
            return "仕入明細の税率は、0 より大きく整数 3 桁・小数 2 桁までの数にしてください。"
        values["tax_rate"] = rate
    except ValueError:
        return MESSAGE_SELF_INVOICE_SHAPE
    for key in ("tax_category_code", "supplier_name", "supplier_registration_no"):
        value = payload.get(key, "")
        if not isinstance(value, str):
            return MESSAGE_SELF_INVOICE_SHAPE
        values[key] = value
    if not values["supplier_name"] or not values["tax_category_code"]:
        return MESSAGE_SELF_INVOICE_SHAPE
    return values


def _self_invoice_line_values(partner, raw, number):
    """仕入明細の明細 1 つを読み、受注の明細に当てた辞書。当たらなければ断る文 (str)。_receive_self_invoice が呼ぶ。

    引く順は 形 → 元の注文の番号 (order_id) で受信した注文 → 受信した注文の取引先が署名の取引先と同じ → 受け入れた (受注がある) 物
    → その受注の order_line_no の明細 (検収の受け口 _receive_acceptance と同じ引き方)。
    """
    from .models import ReceivedSelfInvoiceLine

    meta = ReceivedSelfInvoiceLine._meta
    shape = f"仕入明細の {number} 行目の形が読めません。line_no・order_id・order_line_no・received_on・qty・unit_price・amount を送ってください。"
    line_no = _positive_int(raw.get("line_no"))
    order_line_no = _positive_int(raw.get("order_line_no"))
    order_revision_no = _revision_number(raw.get("order_revision_no"))
    revised_in = _revision_number(raw.get("revised_in"))
    order_id = raw.get("order_id")
    if line_no is None or order_line_no is None or order_revision_no is None or revised_in is None or not isinstance(order_id, str):
        return shape
    try:
        received_on = _payload_date(raw.get("received_on"), "受領日")
        qty = _payload_decimal(raw.get("qty"), "受領数量")
        unit_price = _payload_decimal(raw.get("unit_price"), "単価")
        amount = _payload_decimal(raw.get("amount"), "金額")
        price_basis = _payload_price_basis(raw.get("price_basis"), "単価の単位")
    except ValueError:
        return shape
    if received_on is None:
        return shape
    if not _fits_field(qty, meta.get_field("qty")):
        return f"仕入明細の {number} 行目の受領数量は、0 より大きく整数 10 桁・小数 2 桁までの数にしてください。"
    if not _fits_field(unit_price, meta.get_field("unit_price")) or not _fits_money(amount, meta.get_field("amount")):
        return f"仕入明細の {number} 行目の単価か金額が、表の欄の桁に入りません。"
    texts = {}
    for key in ("order_no", "unit", "unit_code"):
        value = raw.get(key, "")
        if not isinstance(value, str):
            return shape
        texts[key] = value
    try:
        order_message_id = uuid.UUID(order_id)
    except ValueError:
        order_message_id = None
    missing = f"仕入明細の明細 {number} の注文が、受け入れた受注の明細にありません。"
    inbox = (
        Inbox.objects.select_related("order").filter(message_id=order_message_id).first()
        if order_message_id is not None
        else None
    )
    if inbox is None or inbox.partner_id != partner.pk or inbox.status != Inbox.STATUS_ACCEPTED or inbox.order is None:
        return missing
    order_line = inbox.order.lines.filter(line_no=order_line_no).first()
    if order_line is None:
        return missing
    return {
        "line_no": line_no,
        "order_line": order_line,
        "buyer_order_no": texts["order_no"],
        "buyer_line_no": order_line_no,
        "order_revision_no": order_revision_no,
        "received_on": received_on,
        "qty": qty,
        "unit": texts["unit"],
        "unit_code": texts["unit_code"],
        "price_basis": price_basis,
        "unit_price": unit_price,
        "amount": amount,
        "revised_in": revised_in,
    }


def _receive_self_invoice(message_id, partner, payload):
    """署名の通った仕入明細を、受けた仕入明細 (と明細) にする。返す物は SelfInvoiceReceipt か、断る理由の文。

    見る順は 同じ番号 (message_id) の 2 回目 (何もせず 1 回目の行を返す = R11) → 形 (字・数・日付・目的と版・文書の種類)
    → 受け取れる字数 (_self_invoice_text_problem) → 修正と取消の目的 (今は受けない) → 同じ得意先の同じ仕入明細番号の行があれば
    版の断り → 明細ごとに受注の明細に当てる (_self_invoice_line_values) → 1 つの atomic の中で作る。断るときは何も残さない。
    受け口に利用者はいないので、変更の履歴の人の字は「<得意先の名前> の seizou」。
    訳: 受注の明細に当てられない仕入明細を受けて、どの受注の金額か分からない記録を作らないため (検収と同じ見方)。古い版で新しい版を上書きしないため。出所 = AI の案 (作者の決め。中小企業共通 EDI の修正仕入明細の流れ)。
    変えてよい条件 = 送り合いが行番号でなく明細の識別で明細を指すようになったとき。
    """
    from .models import ReceivedSelfInvoice, ReceivedSelfInvoiceLine

    msg_id = str(message_id)
    same = ReceivedSelfInvoice.objects.filter(message_id=message_id).first()
    if same is not None:
        return SelfInvoiceReceipt(message_id=message_id, self_invoice=same)
    values = _self_invoice_values(payload)
    if isinstance(values, str):
        return _refuse(values, msg_id)
    problem = _self_invoice_text_problem(payload)
    if problem:
        return _refuse(problem, msg_id)
    existing = ReceivedSelfInvoice.objects.filter(partner=partner, self_invoice_no=values["self_invoice_no"]).first()
    if existing is not None:
        if values["revision_no"] <= existing.revision_no:
            return _refuse(f"仕入明細 {existing.self_invoice_no} は第 {existing.revision_no} 版まで受けてあります。", msg_id)
        if values["purpose_code"] == edi_codes.PURPOSE_CANCEL:
            return _receive_self_invoice_cancel(message_id, partner, existing, values)
        if existing.status == ReceivedSelfInvoice.STATUS_CANCELLED:
            return _refuse(f"仕入明細 {existing.self_invoice_no} は取り消してあるので、修正を受けられません。", msg_id)
        return _receive_self_invoice_revision(message_id, partner, existing, values, payload)
    rows = []
    for number, raw in enumerate(payload["lines"], start=1):
        row = _self_invoice_line_values(partner, raw, number)
        if isinstance(row, str):
            return _refuse(row, msg_id)
        rows.append(row)
    line_numbers = [row["line_no"] for row in rows]
    if len(set(line_numbers)) != len(line_numbers):
        return _refuse(MESSAGE_SELF_INVOICE_SHAPE, msg_id)
    if values["purpose_code"] == edi_codes.PURPOSE_CANCEL:
        # 前の版が届いていない取消: 新しく受けたときと同じ形で行を作り、状態を取消にする (送った側の取消を断らず、取り消された記録を残す)。
        values = {**values, "status": ReceivedSelfInvoice.STATUS_CANCELLED}
    try:
        with transaction.atomic():
            received = ReceivedSelfInvoice.objects.create(partner=partner, message_id=message_id, **values)
            _record(received, None, by=f"{partner.name} の seizou")
            for row in rows:
                line = ReceivedSelfInvoiceLine.objects.create(self_invoice=received, **row)
                _record(line, None, by=f"{partner.name} の seizou")
    except IntegrityError:
        same = ReceivedSelfInvoice.objects.filter(message_id=message_id).first()
        if same is not None:
            return SelfInvoiceReceipt(message_id=message_id, self_invoice=same)
        return _refuse(MESSAGE_SELF_INVOICE_SHAPE, msg_id)
    return SelfInvoiceReceipt(message_id=message_id, self_invoice=received)


# ---------------------------------------------------------------- 仕入明細回答 (作者の決めその 4)

MESSAGE_SI_REPLY_NO_CONNECTION = (
    "取引先との接続 (送る向き) が無いので仕入明細回答を送っていません。"
    "設定の画面の「取引先との接続」で、この取引先に送る向きの接続を登録してください。"
)
MESSAGE_SI_REPLY_SHAPE = "仕入明細回答の形が読めません。self_invoice_no・revision_no・reply_type・lines を送ってください。"
_SI_REPLY_DOCUMENTS = frozenset({edi_codes.SI_DOC_REPLY, edi_codes.SI_DOC_REVISED_REPLY})
_SI_REPLY_TYPES = frozenset(code for code, _label in edi_codes.SI_REPLY_TYPES)
_SI_REPLY_LINE_TYPES = frozenset(code for code, _label in edi_codes.SI_REPLY_LINE_TYPES)
_SI_REASON_CODES = frozenset(code for code, _label in edi_codes.SI_REASON_CHOICES)
#: 承諾 (と、直した内容の承諾) の答え。一度送った明細は答え直せない (作者の決め)
SI_ACCEPTED_TYPES = frozenset({edi_codes.SI_REPLY_ACCEPTED, edi_codes.SI_REPLY_ACKNOWLEDGED})
#: 答える form が渡す明細の答えの種類 (画面の 3 つ)
ANSWER_ACCEPT = "accept"
ANSWER_PROPOSE = "propose"
ANSWER_REJECT = "reject"


@dataclass(frozen=True)
class SelfInvoiceResponseReceipt:
    """受け口が仕入明細回答を元の仕入明細に書いた (か、同じ番号の 2 回目だった) 印。受け口の view は message_id だけを読む。"""

    message_id: uuid.UUID
    self_invoice: object


def _received_status(lines):
    """受けた仕入明細の状態を明細の答えから決める。答えの無い明細があれば未回答、提案か確認できないがあれば確かめ中、全部承諾なら確認済。"""
    from .models import ReceivedSelfInvoice

    codes = [line.reply_type for line in lines]
    if any(code in (edi_codes.SI_REPLY_PROPOSED, edi_codes.SI_REPLY_REJECTED) for code in codes):
        return ReceivedSelfInvoice.STATUS_PARTIAL
    if codes and all(code in SI_ACCEPTED_TYPES for code in codes):
        return ReceivedSelfInvoice.STATUS_CONFIRMED
    return ReceivedSelfInvoice.STATUS_RECEIVED


def _head_reply_type(lines, revision_no):
    """鑑ヘッダの仕入明細回答タイプコード。全部の明細が確認できない → RE、提案か確認できないが 1 つでも → CA、ほかで版が 2 以上 → AC、版 1 → AP。"""
    codes = [line.reply_type for line in lines]
    if codes and all(code == edi_codes.SI_REPLY_REJECTED for code in codes):
        return edi_codes.SI_REPLY_REJECTED
    if any(code in (edi_codes.SI_REPLY_PROPOSED, edi_codes.SI_REPLY_REJECTED) for code in codes):
        return edi_codes.SI_REPLY_PARTIAL
    if revision_no >= 2:
        return edi_codes.SI_REPLY_ACKNOWLEDGED
    return edi_codes.SI_REPLY_ACCEPTED


def answer_self_invoice(received, answers, user):
    """受けた仕入明細の明細ごとの答え (承諾・提案・確認できない) を書き、得意先へ仕入明細回答を送る。

    answers = {行番号: {"answer": "accept"・"propose"・"reject", "qty", "unit_price", "reason", "note"}}。提案は数と単価 (金額の閲覧の無い人は単価が None =
    仕入明細の単価のまま)、確認できないは訳のコード (明細行仕入明細回答理由コード)。「承諾」は、修正仕入明細で直した明細 (直した版が 2 以上) なら
    直した内容の承諾 AC、ほかは AP。状態を決め直し (未回答の明細があれば未回答・提案か確認できないがあれば確かめ中・全部承諾なら確認済)、
    新しい答えの番号を付け、答えの文書の種類を決めて残す (1 回目の答えは 1R・2 回目からは 1CR)。atomic の外で send_self_invoice_reply。
    届かなければ返す物に saved_note と saved_level = "warning" を付ける (答えは成立する)。
    断るのは、受けた仕入明細が取消か、承諾 (AP・AC) を送ってある明細への答えが入っているとき (何も書かず何も送らない。日本語の ValueError)。
    インボイス制度: 仕入明細書は売り手の確認で得意先の仕入税額控除の書類になる。ここの「承諾」がその確認に当たる (確かめた資料 = 中小企業共通 EDI インボイス仕様編 3.1.8)。
    訳: 一度承諾した明細の金額を後から動かさないため (支払の見込みが承諾した金額で数える前提)。出所 = 作者の決め。
    変えてよい条件 = 作者が承諾の後の答え直しを決めたとき。
    """
    from .models import ReceivedSelfInvoice

    with transaction.atomic():
        locked = ReceivedSelfInvoice.objects.select_for_update().get(pk=received.pk)
        if locked.status == ReceivedSelfInvoice.STATUS_CANCELLED:
            raise ValueError("送れません。この仕入明細は得意先が取り消しました。")
        lines = {line.line_no: line for line in locked.lines.all()}
        for number in sorted(answers):
            line = lines.get(number)
            if line is None:
                raise ValueError(f"送れません。明細 {number} はこの仕入明細にありません。")
            if line.reply_type in SI_ACCEPTED_TYPES:
                raise ValueError(f"送れません。明細 {number} は承諾してあります。承諾した明細は答え直せません。")
        if not answers:
            raise ValueError("送れません。答える明細がありません。")
        before = _saved(locked)
        for number, answer in answers.items():
            line = lines[number]
            line_before = _saved(line)
            kind = answer.get("answer")
            line.reply_qty = None
            line.reply_unit_price = None
            line.reply_reason_code = ""
            line.reply_note = (answer.get("note") or "")[: line._meta.get_field("reply_note").max_length]
            if kind == ANSWER_PROPOSE:
                line.reply_type = edi_codes.SI_REPLY_PROPOSED
                line.reply_qty = answer.get("qty") if answer.get("qty") is not None else line.qty
                line.reply_unit_price = answer.get("unit_price") if answer.get("unit_price") is not None else line.unit_price
            elif kind == ANSWER_REJECT:
                line.reply_type = edi_codes.SI_REPLY_REJECTED
                line.reply_reason_code = answer.get("reason") or ""
            else:
                line.reply_type = edi_codes.SI_REPLY_ACKNOWLEDGED if line.revised_in >= 2 else edi_codes.SI_REPLY_ACCEPTED
            line.save(update_fields=["reply_type", "reply_qty", "reply_unit_price", "reply_reason_code", "reply_note"])
            _record(line, user, before=line_before)
        all_lines = list(lines.values())
        locked.reply_document_code = (
            edi_codes.SI_DOC_REPLY if locked.reply_message_id is None else edi_codes.SI_DOC_REVISED_REPLY
        )
        locked.status = _received_status(all_lines)
        locked.reply_message_id = uuid.uuid4()
        locked.reply_sent_at = None
        locked.reply_response = ""
        locked.save(update_fields=["reply_document_code", "status", "reply_message_id", "reply_sent_at", "reply_response"])
        _record(locked, user, before=before)
    saved = send_self_invoice_reply(locked, user)
    if saved.reply_sent_at is None:
        saved.saved_note = f"答えは得意先に届いていません。{saved.reply_response} 直したら「送り直す」を押してください。"
        saved.saved_level = "warning"
    return saved


def send_self_invoice_reply(received, user):
    """受けた仕入明細の今の答え (仕入明細回答) を署名して、得意先の受け口へ送る。send_reply と同じ形。

    - 送るのは答えの番号 (reply_message_id) がある行だけ (答えを作る前は日本語の ValueError)。送り直しは同じ番号・同じ文書の種類。
    - つながりは得意先の向き「送る」の物。無ければ HTTP を送らず、返事の欄に MESSAGE_SI_REPLY_NO_CONNECTION を書く。
    - 本文の鍵は id・kind ("self_invoice_response")・sent_at・self_invoice_no・revision_no・document_code (1R か 1CR)・reply_type (鑑ヘッダ)・
      supplier・lines (答えのある全部の明細。1 つは line_no・reply_type・reason_code (確認できない以外は "")・reply_qty・reply_unit_price
      (提案以外は null)・note)。
    - 相手への HTTP は transaction.atomic の外 (R17)。届かない・断られたでも例外にしない。返す物は取り直した ReceivedSelfInvoice。
    """
    from django.utils import timezone

    from .models import ReceivedSelfInvoice

    current = ReceivedSelfInvoice.objects.select_related("partner").get(pk=received.pk)
    if current.reply_message_id is None:
        raise ValueError("送れません。この仕入明細には、まだ答えていません。")
    message_id = current.reply_message_id
    connection = _send_connection(current.partner)
    if connection is None:
        reply, kind = MESSAGE_SI_REPLY_NO_CONNECTION, "no_connection"
    else:
        lines = [line for line in current.lines.order_by("line_no") if line.reply_type]
        data = {
            "id": str(message_id),
            "kind": "self_invoice_response",
            "sent_at": timezone.localtime().isoformat(),
            "self_invoice_no": current.self_invoice_no,
            "revision_no": current.revision_no,
            "document_code": current.reply_document_code or edi_codes.SI_DOC_REPLY,
            "reply_type": _head_reply_type(list(current.lines.all()), current.revision_no),
            "supplier": _party(),
            "lines": [
                {
                    "line_no": line.line_no,
                    "reply_type": line.reply_type,
                    "reason_code": line.reply_reason_code if line.reply_type == edi_codes.SI_REPLY_REJECTED else "",
                    "reply_qty": _plain_number(line.reply_qty) if line.reply_type == edi_codes.SI_REPLY_PROPOSED else None,
                    "reply_unit_price": (
                        _plain_number(line.reply_unit_price) if line.reply_type == edi_codes.SI_REPLY_PROPOSED else None
                    ),
                    "note": line.reply_note,
                }
                for line in lines
            ],
        }
        raw = _encode(data)
        # R17: ここは atomic の外。
        reply, kind = _deliver(connection, message_id, raw)
    with transaction.atomic():
        saved = ReceivedSelfInvoice.objects.select_for_update().get(pk=current.pk)
        saved.reply_response = reply[: ReceivedSelfInvoice._meta.get_field("reply_response").max_length]
        fields = ["reply_response"]
        if kind == "received":
            saved.reply_sent_at = timezone.now()
            fields.append("reply_sent_at")
        saved.save(update_fields=fields)
    logger.info("仕入明細回答: 受けた仕入明細 %s 番号 %s 返事の種類 %s", saved.pk, message_id, kind)
    return saved


def _si_reply_line_values(raw, number):
    """仕入明細回答の明細 1 つを読んだ辞書。形が違えば断る文 (str)。_receive_self_invoice_response が呼ぶ。"""
    from django.apps import apps

    model = apps.get_model("purchasing", "SelfInvoiceLine")
    meta = model._meta
    shape = f"仕入明細回答の {number} 行目の形が読めません。line_no・reply_type を送ってください。"
    if not isinstance(raw, dict):
        return shape
    line_no = _positive_int(raw.get("line_no"))
    code = raw.get("reply_type")
    reason = raw.get("reason_code") or ""
    note = raw.get("note") or ""
    if line_no is None or code not in _SI_REPLY_LINE_TYPES or not isinstance(reason, str) or not isinstance(note, str):
        return shape
    if code == edi_codes.SI_REPLY_REJECTED and reason not in _SI_REASON_CODES:
        return f"仕入明細回答の {number} 行目の確認できない訳 (reason_code) が読めません。"
    limit = meta.get_field("reply_note").max_length
    if len(note) > limit:
        return f"仕入明細回答の {number} 行目の字 (note) は {limit} 字までにしてください。"
    values = {"line_no": line_no, "reply_type": code, "reply_reason_code": reason if code == edi_codes.SI_REPLY_REJECTED else "",
              "reply_note": note, "reply_qty": None, "reply_unit_price": None}
    if code == edi_codes.SI_REPLY_PROPOSED:
        try:
            qty = _payload_decimal(raw.get("reply_qty"), "提案の数")
            price = _payload_decimal(raw.get("reply_unit_price"), "提案の単価")
        except ValueError:
            return shape
        if not _fits_field(qty, meta.get_field("reply_qty")) or not _fits_field(price, meta.get_field("reply_unit_price")):
            return f"仕入明細回答の {number} 行目の提案の数か単価が、表の欄の桁に入りません。"
        values["reply_qty"] = qty
        values["reply_unit_price"] = price
    return values


def _receive_self_invoice_response(message_id, partner, payload):
    """署名の通った仕入明細回答を、元の仕入明細の明細に書く。返す物は SelfInvoiceResponseReceipt か、断る理由の文。

    見る順は 同じ答えの番号の 2 回目 (何もせず返す = R11) → 形 (番号・版・文書の種類・鑑ヘッダの答え・明細の答えと訳と提案の数と単価と字数)
    → 仕入明細番号の仕入明細 → 取引先が署名の取引先 → 答えた版が今の版 → 明細の行番号がある → purchasing.services.record_self_invoice_reply。
    断るときは何も書かない。受け口に利用者はいないので、変更の履歴の人の字は「<取引先の名前> の seizou」。
    取り消した仕入明細に、取消の 1 つ前の版への答えが届いたとき (行き違い) は、版の断りを出さずに答えの列を書き、状態は取消のまま
    (purchasing.services.record_self_invoice_reply が取消の状態を変えない)。仕入明細の詳細に行き違いの黄の 1 行が出る。
    訳: 古い版への答えで新しい版の明細を書き換えないため。出所 = AI の案 (作者の決め)。
    変えてよい条件 = 版をまたいで答えを当てる決まりを作者が決めたとき。

    行き違いの扱いの訳: 取消と相手の答えが行き違ったとき、相手に誤った断りを返さず、相手が何と答えたかを発注元にも残すため。
    出所 = AI の案 (作者の決め。送った発注の取消の行き違いと同じ形)。変えてよい条件 = 作者が行き違いの扱いを決めたとき。
    """
    from django.apps import apps

    from seizou.purchasing import services as purchasing_services

    model = apps.get_model("purchasing", "SelfInvoice")
    msg_id = str(message_id)
    same = model.objects.filter(reply_message_id=message_id).first()
    if same is not None:
        return SelfInvoiceResponseReceipt(message_id=message_id, self_invoice=same)
    number = payload.get("self_invoice_no")
    revision_no = _positive_int(payload.get("revision_no"))
    lines = payload.get("lines")
    if (
        not isinstance(number, str)
        or revision_no is None
        or payload.get("document_code") not in _SI_REPLY_DOCUMENTS
        or payload.get("reply_type") not in _SI_REPLY_TYPES
        or not isinstance(lines, list)
        or not lines
    ):
        return _refuse(MESSAGE_SI_REPLY_SHAPE, msg_id)
    rows = []
    for position, raw in enumerate(lines, start=1):
        row = _si_reply_line_values(raw, position)
        if isinstance(row, str):
            return _refuse(row, msg_id)
        rows.append(row)
    self_invoice = model.objects.filter(self_invoice_no=number).first()
    if self_invoice is None:
        return _refuse(f"仕入明細回答の元の仕入明細 {number[:35]} がありません。", msg_id)
    if self_invoice.partner_id != partner.pk:
        return _refuse("仕入明細回答の元の仕入明細は、その取引先への物ではありません。", msg_id)
    # 行き違い: 取り消した (取消の版を送った) 後に、相手の取消の 1 つ前の版への答えが届いた。断らずに答えを書き、状態は取消のまま (作者の決めの送った発注の取消と同じ形)。
    crossed = self_invoice.status == model.STATUS_CANCELLED and revision_no == self_invoice.revision_no - 1
    if revision_no != self_invoice.revision_no and not crossed:
        return _refuse(
            f"仕入明細回答の版 (第 {revision_no} 版) が今の版 (第 {self_invoice.revision_no} 版) と違います。"
            "新しい版を受けてから答え直してください。",
            msg_id,
        )
    known = set(self_invoice.lines.values_list("line_no", flat=True))
    unknown = [row["line_no"] for row in rows if row["line_no"] not in known]
    if unknown:
        return _refuse(f"仕入明細回答の明細の行番号 {unknown[0]} が仕入明細 {self_invoice.self_invoice_no} にありません。", msg_id)
    try:
        with transaction.atomic():
            saved = purchasing_services.record_self_invoice_reply(
                self_invoice,
                None,
                revision_no=revision_no,
                reply_type=payload["reply_type"],
                lines=rows,
                reply_message_id=message_id,
                by=f"{partner.name} の seizou",
            )
    except IntegrityError:
        same = model.objects.filter(reply_message_id=message_id).first()
        if same is not None:
            return SelfInvoiceResponseReceipt(message_id=message_id, self_invoice=same)
        return _refuse(MESSAGE_SI_REPLY_SHAPE, msg_id)
    return SelfInvoiceResponseReceipt(message_id=message_id, self_invoice=saved)
