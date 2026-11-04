"""intake の公開する関数 (05 の 9 節と 01 の 10.4)。画面は、この関数だけを通して読み取りの記録と受注を書く。

- 引数の順は 対象 → 値 → user (01 の 10.1)。
- 注文書 (PDF・PNG・JPEG) は Claude の API にそのまま渡し、注文のデータ (connect の受け口と同じ項目の名前) の
  JSON を返させる。返事の形は _check_reply の 1 か所で検査し、通った物だけを Extraction に残す。
  読めなかった結果は残さない。
- API を呼ぶのは _ask_claude の 1 か所。呼ぶのは transaction.atomic の外 (待ちの長い外への呼び出しで DB を握らない)。
  試験はこの関数を作り物の返事に差し替え、本物の API を呼ばない。
- 鍵は環境変数 API_KEY_ENV からだけ読み、client に明示して渡す (ほかの資格に落ちる道を作らない)。
  鍵の値を表・画面・ログ・例外の文・返す物に出さない。製品から外の命令は起こさない。
- 読み取った値をそのまま受注にしない。人が確かめの画面で品目を選び、accept_extracted を呼んだときだけ受注を作る。
- 見張るフォルダの読み取り (extract_to_inbox) は受注を作らず、受信した注文の行にする (R10)。差出人の会社名で得意先を当て、当たらなければ取引先の空の行 (差出人の確認待ち) にする。同じ中身の注文書は 1 行 (R11)。
- 呼んでよい部品は core・masters・orders (01 の 1 節)。見張りの extract_to_inbox だけが connect の services を関数の中の import で呼ぶ (表に無い向き。shipping の ship が connect を呼ぶのと同じ形)。ほかの部品の model は import しない。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import unicodedata
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from seizou.core import conf
from seizou.masters.services import map_partner_item, resolve_partner_item
from seizou.orders.services import create_order

from .models import Extraction

#: 鍵を入れる環境変数の名前 (値は会社のプロジェクトの .env にだけ置く)
API_KEY_ENV = "ANTHROPIC_API_KEY"
#: 読み取りに使う model の既定 (会社の settings の SEIZOU["intake_model"] で替えられる)
DEFAULT_MODEL = "claude-opus-5-5"
#: 1 回の読み取りで置けるファイルの大きさの上限 (bytes)
MAX_BYTES = 20 * 1024 * 1024
#: 1 回の読み取りの待ちの上限 (秒)。再試行はしない
TIMEOUT_SECONDS = 60
#: 返事の最大の長さ (token)
MAX_TOKENS = 16000

# 字数の上限 (01 の 4 節。識別子 35・名称 100・コード 10・備考 500)
_LIMIT_ID = 35
_LIMIT_NAME = 100
_LIMIT_CODE = 10
_LIMIT_NOTE = 500

_STRING = {"type": "string"}
_STRING_OR_NULL = {"type": ["string", "null"]}

#: 返させる JSON の形 (connect の受け口の注文のデータと同じ項目の名前。紙の注文書に無い番号・種類・送った日時・
#: 宛先は持たない)。数の上下と字数の上限は書かず、_check_reply で見る。
ORDER_SCHEMA = {
    "type": "object",
    "properties": {
        "order_no": _STRING,
        "order_date": _STRING_OR_NULL,
        "buyer": {
            "type": "object",
            "properties": {"name": _STRING},
            "required": ["name"],
            "additionalProperties": False,
        },
        "note": _STRING,
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line_no": {"type": "integer"},
                    "part_no": _STRING,
                    "part_name": _STRING,
                    "qty": _STRING,
                    "unit": _STRING,
                    "unit_price": _STRING_OR_NULL,
                    "due_date": _STRING_OR_NULL,
                    "note": _STRING,
                },
                "required": ["line_no", "part_no", "part_name", "qty", "unit", "unit_price", "due_date", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["order_no", "order_date", "buyer", "note", "lines"],
    "additionalProperties": False,
}

#: 読み取りの指示 (system)
PROMPT = (
    "あなたは製造業の注文書 (発注書) を読み取る係です。"
    "渡された注文書の画像か PDF から、得意先の注文番号 (order_no)・注文日 (order_date)・"
    "差出人の会社の名前 (buyer.name)・備考 (note) と、明細 (lines) を読み取ってください。"
    "明細は 1 行ごとに、行番号 (line_no)・得意先の品番 (part_no)・品名 (part_name)・数量 (qty)・単位 (unit)・"
    "単価 (unit_price)・納期 (due_date)・備考 (note) を読みます。"
    "注文書に書いていない項目は、空の字か null にしてください。推測で埋めないでください。"
    "数は半角の数字の字にし、桁の区切りのカンマを付けないでください。"
    "日付は YYYY-MM-DD の形にしてください。"
)

#: 注文書に添える依頼の文 (user)
REQUEST_TEXT = "この注文書を読み取ってください。"

# ファイルの先頭のバイト → (block の種類, media_type)。拡張子と送られた種類は信じない。
_KINDS = (
    (b"%PDF-", "document", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image", "image/png"),
    (b"\xff\xd8\xff", "image", "image/jpeg"),
)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# 日本語の文 (何が起きて、何をすればよいか)。返事の本文と鍵の値は文に入れない。
_NEXT = "手入力か受信した注文で受けてください。"
MESSAGE_NO_KEY = f"鍵が無いので読み取りは使えません。{_NEXT}"
MESSAGE_TOO_LARGE = "ファイルが 20MB を超えています。ページを分けるか、解像度を下げてから置き直してください。"
MESSAGE_UNREADABLE_TYPE = "読めない形式です。PDF・PNG・JPEG の注文書を置いてください。"
MESSAGE_NOT_READ = f"注文書を読み取れませんでした。{_NEXT}"
MESSAGE_NOT_JSON = f"読み取りの返事が決まった形 (JSON) ではありませんでした。{_NEXT}"
MESSAGE_BAD_KEY = "鍵が合いません。.env の鍵を確かめてください。"
MESSAGE_BUSY = "読み取りが混んでいます。少し待ってからもう一度置いてください。"
MESSAGE_NO_CREDIT = f"API の残高が足りません。{_NEXT}"
MESSAGE_TIMEOUT = f"{TIMEOUT_SECONDS} 秒の間に読み取れませんでした。少し待ってからもう一度置くか、{_NEXT}"
MESSAGE_UNREACHABLE = f"読み取りの API に届きませんでした。少し待ってからもう一度置くか、{_NEXT}"
MESSAGE_IN_INBOX = "この読み取りは受信した注文に入れてあります。受信した注文の画面で差出人を確かめてから承認してください。"
MESSAGE_NO_CONNECT = "受信した注文の部品 (connect) が入っていないので、見張りの読み取りは使えません。"

#: 見張りの読み取りの受信した注文の番号を作る名前空間 (同じ中身の注文書は同じ番号 = R11)
WATCH_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "seizou:intake:watch")


class IntakeUnavailable(ValueError):
    """読み取りが使えない (鍵が無い)。ValueError の子なので、土台の入力の画面が赤の知らせにする。"""


def is_available():
    """読み取りが使えるか (鍵の環境変数に空でない値があるか)。値そのものは返さない。"""
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def model_name():
    """読み取りに使う model の名前。会社の設定 intake_model (core の conf.DEFAULTS に既定) が空でない字ならそれ、無ければ DEFAULT_MODEL。"""
    value = conf.setting("intake_model")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return DEFAULT_MODEL


def extract_order(file, customer, user):
    """注文書のファイルを読み取り、形を検査して Extraction に 1 行残す。返す物は Extraction。

    鍵が無い (IntakeUnavailable)・得意先でない・大きすぎる・読めない形式は、API を呼ぶ前に ValueError で止める。
    API は atomic の外で呼ぶ。返事が JSON でないか形が違えば ValueError で、記録は残さない。受注は作らない。
    customer は None でよい (見張りの読み取り = extract_to_inbox。得意先は差出人の名前から後で当てる)。画面の form は得意先を必須にしている。
    """
    if not is_available():
        raise IntakeUnavailable(MESSAGE_NO_KEY)
    if customer is not None and not customer.is_customer:
        raise ValueError(
            f"取引先 {customer} は得意先ではありません。取引先の種類が得意先か両方の取引先を選んでください。"
        )
    if hasattr(file, "seek"):
        file.seek(0)
    data = file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(MESSAGE_TOO_LARGE)
    block, extension = _block_for(data)

    # ここは atomic の外。
    reply = _ask_claude(model_name(), block)
    try:
        parsed = json.loads(reply)
    except (TypeError, ValueError):
        raise ValueError(MESSAGE_NOT_JSON) from None
    cleaned = _check_reply(parsed)

    name = os.path.basename(getattr(file, "name", "") or "") or f"order{extension}"
    with transaction.atomic():
        return Extraction.objects.create(
            customer=customer,
            file=ContentFile(data, name=name),
            extracted=cleaned,
            read_by=user,
        )


def accept_extracted(extraction, item_mapping, user):
    """読み取りを受注 (入口 = 読み取り) にする。受信箱の受け入れと同じ形。返す物は作った Order。

    item_mapping は得意先の品番の字 → 自社の Item。無い品番は取引先ごとの品番の表 (masters) で引き、
    選んだ品目が表に無い対応なら表に足す。対応の追加・受注の作成・状態の更新は 1 つのトランザクション。
    受注にした物の 2 回目と捨てた物と受信した注文へ回した物は日本語の ValueError。数量と単価の決まり (R02) は orders の RuleViolation。
    """
    with transaction.atomic():
        locked = Extraction.objects.select_for_update().get(pk=extraction.pk)
        if locked.status == Extraction.STATUS_ORDERED:
            order_no = locked.order.order_no if locked.order is not None else ""
            raise ValueError(f"この読み取りは受注 {order_no} にしてあります。受注の詳細を見てください。")
        if locked.status == Extraction.STATUS_DISCARDED:
            raise ValueError("この読み取りは捨ててあるので、受注にできません。注文書を置き直してください。")
        if locked.status == Extraction.STATUS_INBOX:
            raise ValueError(MESSAGE_IN_INBOX)

        cleaned = _check_reply(locked.extracted)
        customer = locked.customer
        mapping = dict(item_mapping or {})
        items = {}
        lines = []
        for raw in cleaned["lines"]:
            part_no = raw["part_no"]
            if part_no not in items:
                items[part_no] = _item_for(customer, part_no, raw["part_name"], mapping, user)
            item = items[part_no]
            lines.append(
                {
                    "item": item,
                    "qty": Decimal(raw["qty"]),
                    "unit": raw["unit"] or item.unit,
                    "unit_price": _unit_price(raw, item),
                    "due_date": date.fromisoformat(raw["due_date"]) if raw["due_date"] else None,
                    "note": raw["note"],
                    "customer_part_no": part_no,
                }
            )

        if cleaned["order_date"]:
            order_date = date.fromisoformat(cleaned["order_date"])
        else:
            order_date = timezone.localtime(locked.read_at).date()
        order = create_order(
            customer,
            lines,
            user,
            order_date=order_date,
            customer_order_no=cleaned["order_no"],
            source="intake",
        )
        locked.status = Extraction.STATUS_ORDERED
        locked.order = order
        locked.save(update_fields=["status", "order"])
        return order


def extract_to_inbox(file, user):
    """見張るフォルダの注文書 1 枚を読み取り、受信した注文 (connect の Inbox。入口 = 読み取り) に 1 行入れる。受注は作らない (R10)。返す物は (Inbox, 新しく作ったか)。

    受信した注文の番号は中身の sha256 から作る uuid5 (同じ中身の注文書は同じ番号 = R11)。同じ番号の行が既にあれば、
    API を呼ばず、読み取りの行も作らずに (その行, False) を返す。読み取りの断り (鍵・大きさ・形式・API の失敗・返事の形) は
    extract_order の IntakeUnavailable と ValueError がそのまま出る。差出人の会社名 (buyer.name) で得意先を当て、
    当たらなければ取引先の空の行 (差出人の確認待ち) にする。読み取りの行は状態を「受信した注文へ回した」にする
    (読み取りの画面の「受注にする」で 2 つ目の受注を作らないため)。connect が入っていなければ日本語の ValueError。
    """
    if not apps.is_installed("seizou.connect"):
        raise ValueError(MESSAGE_NO_CONNECT)
    # intake と connect の輪を避けるため、関数の中で import する (shipping の ship と同じ形)。
    from seizou.connect import services as connect_services

    if hasattr(file, "seek"):
        file.seek(0)
    data = file.read(MAX_BYTES + 1)
    if hasattr(file, "seek"):
        file.seek(0)
    message_id = uuid.uuid5(WATCH_NAMESPACE, hashlib.sha256(data).hexdigest())

    # 同じ中身の確かめは API を呼ぶ前 (読むだけ。connect の表は app の登録から引く)。
    inbox_model = apps.get_model("connect", "Inbox")
    existing = inbox_model._default_manager.filter(message_id=message_id).first()
    if existing is not None:
        return existing, False

    extraction = extract_order(file, None, user)
    sender = extraction.extracted["buyer"]["name"]
    partner = _match_sender(sender)

    with transaction.atomic():
        locked = Extraction.objects.select_for_update().get(pk=extraction.pk)
        locked.customer = partner
        locked.status = Extraction.STATUS_INBOX
        locked.save(update_fields=["customer", "status"])
        inbox = connect_services.record_inbox(
            partner,
            locked.extracted,
            user,
            message_id=message_id,
            source=inbox_model.SOURCE_INTAKE,
            extraction=locked,
            sender_name=sender,
        )
    # 同じ番号が同時に入ったときは、record_inbox が先の行を返す (この読み取りは結ばれない)。
    return inbox, inbox.extraction_id == locked.pk


def _normal_name(text):
    """名前を比べる形にそろえた字 (NFKC で全角半角をそろえ、空白を全部落とし、casefold)。字でなければ ""。"""
    if not isinstance(text, str):
        return ""
    return "".join(unicodedata.normalize("NFKC", text).split()).casefold()


def _match_sender(name):
    """読み取った差出人の名前から得意先を 1 つ当てる。返す物は取引先か None。

    候補は取引先の種類が得意先か両方の物 (読むだけ。取引先の表は Extraction の外部キーの先から引く)。
    段は 1) 名前の完全一致 (前後の空白を落とした字どうし) → 2) そろえた名前 (_normal_name) の一致 →
    3) そろえた略称の一致 (略称が空の取引先は比べない。取引先に別名の列が無いので略称で代える)。
    前の段で 1 つ以上当たればそこで決め、1 つなら当たり、2 つ以上なら None (人が選ぶ)。名前が空なら None。
    """
    if not isinstance(name, str) or not name.strip():
        return None
    partner_model = Extraction._meta.get_field("customer").related_model
    candidates = list(
        partner_model._default_manager.filter(
            role__in=(partner_model.ROLE_CUSTOMER, partner_model.ROLE_BOTH)
        ).only("pk", "name", "short_name")
    )
    plain = name.strip()
    normal = _normal_name(name)
    stages = (
        lambda partner: partner.name.strip() == plain,
        lambda partner: _normal_name(partner.name) == normal,
        lambda partner: bool(partner.short_name.strip()) and _normal_name(partner.short_name) == normal,
    )
    for matches in stages:
        found = [partner for partner in candidates if matches(partner)]
        if found:
            return found[0] if len(found) == 1 else None
    return None


def _block_for(data):
    """ファイルの先頭のバイトで形を決め、API に渡す block と拡張子を返す。どれでもなければ ValueError。"""
    for magic, block_type, media_type in _KINDS:
        if data.startswith(magic):
            block = {
                "type": block_type,
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(data).decode("ascii"),
                },
            }
            return block, "." + media_type.rsplit("/", 1)[-1]
    raise ValueError(MESSAGE_UNREADABLE_TYPE)


def _ask_claude(model, block):
    """Claude の API に注文書の block を渡し、返事の最初の text の字を返す。API を呼ぶのはここ 1 か所。

    SDK は関数の中で import する (読み取りを使わない会社と試験は SDK が無くても動く)。
    鍵は環境変数の値を client に明示して渡す。待ちは TIMEOUT_SECONDS で、再試行しない。
    拒否と長さの打ち切りと SDK の例外は、日本語の ValueError に替える (返事の本文と鍵の値を文に入れない)。
    """
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ[API_KEY_ENV], timeout=TIMEOUT_SECONDS, max_retries=0)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=PROMPT,
            messages=[{"role": "user", "content": [block, {"type": "text", "text": REQUEST_TEXT}]}],
            output_config={"format": {"type": "json_schema", "schema": ORDER_SCHEMA}},
        )
    except anthropic.AuthenticationError:
        raise ValueError(MESSAGE_BAD_KEY) from None
    except anthropic.RateLimitError:
        raise ValueError(MESSAGE_BUSY) from None
    except anthropic.APIStatusError as exc:
        if exc.status_code == 402:
            raise ValueError(MESSAGE_NO_CREDIT) from None
        raise ValueError(f"{MESSAGE_UNREACHABLE} (状態 {exc.status_code})") from None
    except anthropic.APITimeoutError:
        raise ValueError(MESSAGE_TIMEOUT) from None
    except anthropic.APIConnectionError:
        raise ValueError(MESSAGE_UNREACHABLE) from None

    if response.stop_reason in ("refusal", "max_tokens"):
        raise ValueError(MESSAGE_NOT_READ)
    for content in response.content:
        if getattr(content, "type", None) == "text":
            return content.text
    raise ValueError(MESSAGE_NOT_READ)


def _check_reply(data):
    """返事の形を検査して整えた辞書を返す。返事の形の検査はここ 1 か所。外れたら日本語の ValueError。

    必須は lines (1 行以上) と、各行の part_no (空でない字) と qty (数の字)。数は Decimal に直せて有限な字か整数
    (float は通さない) で、字のまま正規化する。日付は null か YYYY-MM-DD。字数は 01 の 4 節の上限まで
    (切り詰めずに断る)。無い任意の項目は "" か None にする。
    """
    if not isinstance(data, dict):
        raise ValueError(MESSAGE_NOT_JSON)
    raw_lines = data.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValueError(f"読み取った中身に明細がありません。{_NEXT}")

    buyer = data.get("buyer")
    buyer_name = buyer.get("name") if isinstance(buyer, dict) else None
    cleaned = {
        "order_no": _text(data.get("order_no"), "", "得意先の注文番号", _LIMIT_ID),
        "order_date": _date_text(data.get("order_date"), "", "注文日"),
        "buyer": {"name": buyer_name.strip() if isinstance(buyer_name, str) else ""},
        "note": _text(data.get("note"), "", "備考", _LIMIT_NOTE),
        "lines": [],
    }
    for number, raw in enumerate(raw_lines, start=1):
        where = f" {number} 行目の"
        if not isinstance(raw, dict):
            raise ValueError(f"読み取った{where}明細の形が違います。{_NEXT}")
        part_no = _text(raw.get("part_no"), where, "品番", _LIMIT_ID)
        if not part_no:
            raise ValueError(f"読み取った{where}品番がありません。{_NEXT}")
        qty = _number_text(raw.get("qty"), where, "数量")
        if qty is None:
            raise ValueError(f"読み取った{where}数量がありません。{_NEXT}")
        line_no = raw.get("line_no")
        cleaned["lines"].append(
            {
                "line_no": line_no if isinstance(line_no, int) and not isinstance(line_no, bool) else None,
                "part_no": part_no,
                "part_name": _text(raw.get("part_name"), where, "品名", _LIMIT_NAME),
                "qty": qty,
                "unit": _text(raw.get("unit"), where, "単位", _LIMIT_CODE),
                "unit_price": _number_text(raw.get("unit_price"), where, "単価"),
                "due_date": _date_text(raw.get("due_date"), where, "納期"),
                "note": _text(raw.get("note"), where, "備考", _LIMIT_NOTE),
            }
        )
    return cleaned


def _text(value, where, label, limit):
    """字の項目。null は ""。字でなければ、上限を超えれば日本語の ValueError。前後の空白は取る。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"読み取った{where}{label}が字ではありません。{_NEXT}")
    text = value.strip()
    if len(text) > limit:
        raise ValueError(f"読み取った{where}{label}が {limit} 字を超えています。{_NEXT}")
    return text


def _number_text(value, where, label):
    """数の項目。null と空は None。Decimal に直せて有限な字か整数なら、正規化した字。ほかは日本語の ValueError。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            number = None
        if number is not None and number.is_finite():
            return f"{number:f}"
    raise ValueError(f"読み取った{where}{label}が数の形ではありません。{_NEXT}")


def _date_text(value, where, label):
    """日付の項目。null と空は None。YYYY-MM-DD の字ならその字。ほかは日本語の ValueError。"""
    if value is None or value == "":
        return None
    if isinstance(value, str) and _ISO_DATE.fullmatch(value.strip()):
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError:
            pass
    raise ValueError(f"読み取った{where}{label}が日付 (YYYY-MM-DD) の形ではありません。{_NEXT}")


def _item_for(customer, part_no, part_name, item_mapping, user):
    """得意先の品番に対応する自社の品目。選んだ品目が表に無い対応なら、表に足す。"""
    known = resolve_partner_item(customer, part_no)
    chosen = item_mapping.get(part_no)
    if chosen is None:
        if known is None:
            raise ValueError(f"品番 {part_no} の自社の品目を選んでください。")
        return known
    if known is None:
        map_partner_item(customer, chosen, part_no, user, partner_item_name=part_name)
    return chosen


def _unit_price(raw, item):
    """明細の単価。読み取った単価、無ければ品目の標準単価。どちらも無いか 0 なら日本語の ValueError。"""
    if raw["unit_price"] is not None:
        return Decimal(raw["unit_price"])
    if item.standard_price:
        return item.standard_price
    raise ValueError(
        f"品番 {raw['part_no']} の単価が、注文書にも部品マスターの標準単価にもありません。"
        "部品マスターに標準単価を入れるか、手入力で受けてください。"
    )
