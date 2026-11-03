"""2 社の作り物の出荷の知らせと検収の種 (core/demo.py の PART_SEEDS が呼ぶ)。

load_after_orders(seeds_dir, user) が acceptances.yaml (B 社。届いた注文の写しと検収) を、
load(seeds_dir, user) が shipping_notices.yaml (A 社。出荷の知らせと注文回答の写し) を読み、services の関数で入れる。
core の demo.load は load_after_orders を受注の後・出荷の前に (出荷と納品書の種が届いた注文の写しの受注を指すため)、
load を部品の種の段で呼び、2 つの数を同じ種類に足す。
ファイルが無ければ何もせず {"入れた": 0, "飛ばした": 0} を返す (出力の行を出さない)。

- 書くのは services の関数だけ (connect.services・purchasing.services)。表に直接書かない。標準出力に書かない。
- 届いた注文の写しは、受け口 (署名の確かめ) を通さずに受信箱に 1 行入れ、受け入れて受注にする (入口は「届いた注文」)。
  相手の品番は取引先ごとの品番の表 (partner_items.csv) で自社の品目に引く。受け入れは send=False で呼び、
  注文回答を相手に送らない (届いた注文の返事の欄に書くだけ)。2 社のつながりは両向き。
- 出荷の知らせは発注の明細に付ける。注文回答の写しは発注の状態を「作成」→「送った」→「相手が受け入れた」に進める
  (相手に送らず、送った注文の行も作らない)。
- 相手の会社に HTTP を送らない。受け入れは send=False で呼び、注文回答を相手に送らない (返事の欄に書くだけ)。
  2 社のつながりは両向き。
- 2 回入れても重ならない。自然な鍵で既にある物は飛ばす (上書きしない): 届いた注文の写し = 取引先 × 相手の発注番号 の
  受注が既にある、検収 = 受注の明細 × 検収日、出荷の知らせ = 発注の明細 × 相手の出荷番号。
  番号は自然な鍵の字から uuid5 で作る (入れ直しても同じ番号。表の一意の制約が 2 つ目の守り)。
- 1 件でも services が断ったら、ファイルと「N 件目」を文に入れて ValueError を上げる
  (core の demo.load の transaction.atomic が全体を巻き戻す)。「注意」の文は足さない。
- 数える物: 届いた注文の写し・検収・出荷の知らせをそれぞれ 1 件と数える (発注の状態を進めるのは数えない)。
- ほかの部品の services と model は関数の中で import する。取引先の引き方と YAML の読み方は stock/demo_seed.py の物を写した
  (core の private な関数を import しない)。
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from seizou.core.exceptions import RuleViolation

from . import services
from .models import Acceptance, ShippingNotice

ACCEPTANCES_FILE = "acceptances.yaml"
ACCEPTANCES_ROOT = "届いた注文"
INBOX_KEYS = ("取引先コード", "相手の発注番号", "発注日", "明細", "検収")
INBOX_LINE_KEYS = ("相手の品番", "品名", "数量", "単位", "単価", "納期")
ACCEPTANCE_KEYS = ("行番号", "検収日", "数量")

NOTICES_FILE = "shipping_notices.yaml"
NOTICES_ROOT = "発注"
NOTICE_ORDER_KEYS = ("取引先コード", "備考", "注文回答", "出荷の知らせ")
NOTICE_KEYS = ("行番号", "出荷日", "数量", "相手の出荷番号", "相手の受注番号")
REPLY_WORDS = ("受け入れた",)


@contextmanager
def _guard(name, number):
    """services と変換の誤りに、ファイルと位置を付けて ValueError に上げ直す (巻き戻しは呼んだ側の atomic)。"""
    try:
        yield
    except (ValueError, RuleViolation) as exc:
        raise ValueError(f"{name} の {number} 件目: {exc}") from exc


def _read(path, root_key):
    """YAML を読み、根の鍵の下の並びを返す。"""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name} を YAML として読めません: {exc}") from exc
    if not isinstance(data, dict) or root_key not in data:
        raise ValueError(f"{path.name} に「{root_key}」の並びがありません。")
    entries = data[root_key] or []
    if not isinstance(entries, list):
        raise ValueError(f"{path.name} の「{root_key}」は並びで書いてください。")
    return entries


def _check_keys(entry, allowed, what):
    if not isinstance(entry, dict):
        raise ValueError(f"{what}の形が違います (鍵と値の並びではありません)。")
    unknown = [key for key in entry if key not in allowed]
    if unknown:
        raise ValueError(f"{what}の鍵「{'」「'.join(unknown)}」が分かりません。")


def _decimal(raw, label):
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label}「{raw}」を数として読めません。") from exc
    if not value.is_finite():
        raise ValueError(f"{label}「{raw}」を数として読めません。")
    return value


def _date(raw, label):
    import datetime

    if isinstance(raw, datetime.date):
        return raw
    try:
        return datetime.date.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{label}「{raw}」を日付 (YYYY-MM-DD) として読めません。") from exc


def _text(raw):
    return "" if raw is None else str(raw).strip()


def _line_no(raw, what):
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{what}の行番号「{raw}」を数として読めません。") from exc


def _message_id(*parts):
    """自然な鍵の字から番号を作る (入れ直しても同じ番号)。"""
    return uuid.uuid5(uuid.NAMESPACE_URL, "seizou-demo/" + "/".join(str(p) for p in parts))


# --- 引き方 (stock/demo_seed.py の _partner_by_code を写した) ---


def _partner_by_code(code):
    from seizou.masters.models import Partner

    partner = Partner.objects.filter(code=str(code).strip()).first()
    if partner is None:
        raise ValueError(f"取引先コード {code} が取引先にありません。取引先の種に行を足してください。")
    return partner


# --- B 社: 届いた注文の写しと検収 ---


def _inbox_payload(entry, order_no, message_id):
    """届いた注文の写しの中身 (相手が送る注文のデータと同じ形のうち、受ける側が読む鍵だけ)。数は字で渡す。"""
    raw_lines = entry.get("明細") or []
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValueError("届いた注文の明細が空です。明細を 1 行以上書いてください。")
    lines = []
    for index, raw in enumerate(raw_lines, start=1):
        _check_keys(raw, INBOX_LINE_KEYS, f"{index} 行目の明細")
        due = raw.get("納期")
        lines.append(
            {
                "line_no": index,
                "part_no": _text(raw.get("相手の品番")),
                "part_name": _text(raw.get("品名")),
                "qty": str(_decimal(raw.get("数量"), "数量")),
                "unit": _text(raw.get("単位")),
                "unit_price": str(_decimal(raw.get("単価"), "単価")),
                "due_date": _date(due, "納期").isoformat() if due not in (None, "") else None,
                "note": "",
            }
        )
    return {
        "id": str(message_id),
        "kind": "order",
        "order_no": order_no,
        "order_date": _date(entry.get("発注日"), "発注日").isoformat(),
        "note": "",
        "lines": lines,
    }


def _load_inbox(entry, user):
    """1 件の届いた注文の写しと、その受注の明細への検収の並び。(入れた数, 飛ばした数) を返す。"""
    from seizou.orders.models import Order

    _check_keys(entry, INBOX_KEYS, "届いた注文")
    partner = _partner_by_code(entry.get("取引先コード"))
    order_no = _text(entry.get("相手の発注番号"))
    if not order_no:
        raise ValueError(
            "相手の発注番号が空です。届いた注文の写しは 取引先 × 相手の発注番号 を自然な鍵にするので、書いてください。"
        )
    made = skipped = 0
    order = Order.objects.filter(customer=partner, customer_order_no=order_no).order_by("pk").first()
    if order is None:
        message_id = _message_id("inbox", partner.code, order_no)
        payload = _inbox_payload(entry, order_no, message_id)
        inbox = services.record_inbox(partner, payload, user, message_id=message_id)
        order = services.accept(inbox, {}, user, send=False)
        # 受け入れは渡した行を書き換えず、表の行を取り直して書くので、受注から取り直す (飛ばす道と同じ式)。
        inbox = order.inbox_message
        made += 1
    else:
        inbox = getattr(order, "inbox_message", None)
        if inbox is None:
            raise ValueError(
                f"受注 {order.order_no} (得意先の注文番号 {order_no}) は届いた注文から作った物ではありません。"
                "受注の種の得意先の注文番号と重ならない番号にしてください。"
            )
        skipped += 1
    for index, raw in enumerate(entry.get("検収") or [], start=1):
        _check_keys(raw, ACCEPTANCE_KEYS, f"{index} 件目の検収")
        number = _line_no(raw.get("行番号"), "検収")
        line = order.lines.filter(line_no=number).first()
        if line is None:
            raise ValueError(f"検収の行番号 {number} の明細がその受注にありません。")
        accepted_on = _date(raw.get("検収日"), "検収日")
        if Acceptance.objects.filter(order_line=line, accepted_on=accepted_on).exists():
            skipped += 1
            continue
        services.record_acceptance(
            inbox,
            line,
            _decimal(raw.get("数量"), "数量"),
            accepted_on,
            user,
            message_id=_message_id("acceptance", partner.code, order_no, line.line_no, accepted_on.isoformat()),
            buyer_order_no=order_no,
            buyer_line_no=line.line_no,
        )
        made += 1
    return made, skipped


def _load_acceptances(path, user):
    made = skipped = 0
    for number, entry in enumerate(_read(path, ACCEPTANCES_ROOT), start=1):
        with _guard(path.name, number):
            entry_made, entry_skipped = _load_inbox(entry, user)
        made += entry_made
        skipped += entry_skipped
    return made, skipped


# --- A 社: 出荷の知らせと注文回答の写し ---


def _apply_reply(order, word, user):
    """注文回答の写し。「作成」なら「送った」に、「送った」なら「相手が受け入れた」に進める (数えない)。"""
    from seizou.purchasing import services as purchasing_services
    from seizou.purchasing.models import PurchaseOrder

    if word not in REPLY_WORDS:
        raise ValueError(f"注文回答「{word}」は使えません。受け入れた だけを書けます。")
    current = PurchaseOrder.objects.get(pk=order.pk)
    if current.status == PurchaseOrder.STATUS_DRAFT:
        purchasing_services.mark_sent(current, user)
        current = PurchaseOrder.objects.get(pk=order.pk)
    if current.status == PurchaseOrder.STATUS_SENT:
        purchasing_services.mark_accepted(current, user)


def _load_notice_order(entry, user):
    """1 件の発注への出荷の知らせの並びと、注文回答の写し。(入れた数, 飛ばした数) を返す。"""
    from seizou.purchasing.models import PurchaseOrder

    _check_keys(entry, NOTICE_ORDER_KEYS, "発注")
    code = _text(entry.get("取引先コード"))
    partner = _partner_by_code(code)
    note = _text(entry.get("備考"))
    if not note:
        raise ValueError(
            "発注の備考が空です。種の発注は 取引先コード × 備考 で見分けるので、receipts.yaml と同じ字を書いてください。"
        )
    order = PurchaseOrder.objects.filter(partner=partner, note=note).order_by("pk").first()
    if order is None:
        raise ValueError(
            f"発注が見つかりません (取引先コード {code}・備考 {note})。受入の種 (receipts.yaml) に発注を足してください。"
        )
    made = skipped = 0
    for index, raw in enumerate(entry.get("出荷の知らせ") or [], start=1):
        _check_keys(raw, NOTICE_KEYS, f"{index} 件目の出荷の知らせ")
        number = _line_no(raw.get("行番号"), "出荷の知らせ")
        line = order.lines.filter(line_no=number).first()
        if line is None:
            raise ValueError(f"出荷の知らせの行番号 {number} の明細がその発注にありません。")
        shipment_no = _text(raw.get("相手の出荷番号"))
        if not shipment_no:
            raise ValueError(
                "相手の出荷番号が空です。出荷の知らせは 発注の明細 × 相手の出荷番号 で見分けるので、書いてください。"
            )
        if ShippingNotice.objects.filter(line=line, shipment_no=shipment_no).exists():
            skipped += 1
            continue
        services.record_shipping_notice(
            line,
            _decimal(raw.get("数量"), "数量"),
            _date(raw.get("出荷日"), "出荷日"),
            user,
            message_id=_message_id("shipping_notice", partner.code, note, line.line_no, shipment_no),
            shipment_no=shipment_no,
            supplier_order_no=_text(raw.get("相手の受注番号")),
        )
        made += 1
    word = _text(entry.get("注文回答"))
    if word:
        _apply_reply(order, word, user)
    return made, skipped


def _load_notices(path, user):
    made = skipped = 0
    for number, entry in enumerate(_read(path, NOTICES_ROOT), start=1):
        with _guard(path.name, number):
            entry_made, entry_skipped = _load_notice_order(entry, user)
        made += entry_made
        skipped += entry_skipped
    return made, skipped


def load_after_orders(seeds_dir, user) -> dict:
    """種のフォルダの届いた注文の写しと検収 (acceptances.yaml。B 社) を入れる。core の demo.load が受注の後・出荷の前に呼ぶ (出荷と納品書の種が、届いた注文の写しの受注を指すため)。返す物は {"入れた": n, "飛ばした": m}。"""
    seeds_dir = Path(seeds_dir)
    made = skipped = 0
    acceptances = seeds_dir / ACCEPTANCES_FILE
    if acceptances.is_file():
        made, skipped = _load_acceptances(acceptances, user)
    return {"入れた": made, "飛ばした": skipped}


def load(seeds_dir, user) -> dict:
    """種のフォルダの出荷の知らせと注文回答の写し (shipping_notices.yaml。A 社) を入れる。返す物は {"入れた": n, "飛ばした": m}。"""
    seeds_dir = Path(seeds_dir)
    made = skipped = 0
    notices = seeds_dir / NOTICES_FILE
    if notices.is_file():
        entry_made, entry_skipped = _load_notices(notices, user)
        made += entry_made
        skipped += entry_skipped
    return {"入れた": made, "飛ばした": skipped}
