"""2 社の作り物の出荷の知らせと検収の種 (core/demo.py の PART_SEEDS が呼ぶ)。

load_after_orders(seeds_dir, user) が acceptances.yaml (B 社。受信した注文の写しと検収) を、
load(seeds_dir, user) が shipping_notices.yaml (A 社。出荷の知らせと注文回答の写し) を読み、services の関数で入れる。
core の demo.load は load_after_orders を受注の後・出荷の前に (出荷と納品書の種が受信した注文の写しの受注を指すため)、
load を部品の種の段で呼び、2 つの数を同じ種類に足す。
ファイルが無ければ何もせず {"入れた": 0, "飛ばした": 0} を返す (出力の行を出さない)。

- 書くのは services の関数だけ (connect.services・purchasing.services)。表に直接書かない。標準出力に書かない。
- 受信した注文の写しは、受け口 (署名の確かめ) を通さずに受信箱に 1 行入れ、受け入れて受注にする (入口は「受信した注文」)。
  相手の品番は部品の取引先の表 (partner_items.csv) で自社の品目に引く。受け入れは send=False で呼び、
  注文回答を相手に送らない (受信した注文の返事の欄に書くだけ)。2 社のつながりは両向き。
  受信した注文の写しに「承認した日時」があれば、受け入れの承認した日時をその値にする (無ければ読み込みの日時)。
- 出荷の知らせは発注の明細に付ける。注文回答の写しは発注の状態を「作成」→「送った」→「相手が受け入れた」に進める
  (相手に送らず、送った注文の行も作らない)。
- 相手の会社に HTTP を送らない。受け入れは send=False で呼び、注文回答を相手に送らない (返事の欄に書くだけ)。
  2 社のつながりは両向き。
- 2 回入れても重ならない。自然な鍵で既にある物は飛ばす (上書きしない): 受信した注文の写し = 取引先 × 相手の発注番号 の
  受注が既にある、検収 = 受注の明細 × 検収日、出荷の知らせ = 発注の明細 × 相手の出荷番号。
  番号は自然な鍵の字から uuid5 で作る (入れ直しても同じ番号。表の一意の制約が 2 つ目の守り)。
- 1 件でも services が断ったら、ファイルと「N 件目」を文に入れて ValueError を上げる
  (core の demo.load の transaction.atomic が全体を巻き戻す)。「注意」の文は足さない。
- 数える物: 受信した注文の写し・検収・出荷の知らせをそれぞれ 1 件と数える (発注の状態を進めるのは数えない)。
- ほかの部品の services と model は関数の中で import する。取引先の引き方は stock/demo_seed.py の物を写した
  (core の private な関数を import しない)。YAML の読み方は core の公開の seeds の関数を使う。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from seizou.core.seeds import (
    check_keys as _check_keys,
    date as _date,
    decimal as _decimal,
    guard as _guard,
    read as _read,
    text as _text,
)

from . import services
from .models import Acceptance, ShippingNotice

ACCEPTANCES_FILE = "acceptances.yaml"
ACCEPTANCES_ROOT = "受信した注文"
INBOX_KEYS = ("取引先コード", "相手の発注番号", "発注日", "届いた日時", "承認した日時", "明細", "検収")
INBOX_LINE_KEYS = ("相手の品番", "品名", "数量", "単位", "単価", "納期")
ACCEPTANCE_KEYS = ("行番号", "検収日", "数量")

NOTICES_FILE = "shipping_notices.yaml"
NOTICES_ROOT = "発注"
NOTICE_ORDER_KEYS = ("取引先コード", "備考", "注文回答", "出荷の知らせ")
NOTICE_KEYS = ("行番号", "出荷日", "数量", "相手の出荷番号", "相手の受注番号")
REPLY_WORDS = ("受け入れた",)


def _datetime(raw, label):
    """日時の字か datetime を、timezone 付きの datetime にする (無ければ会社の時刻の帯)。空なら None。

    PyYAML は 2026-10-28 10:15:00 を timezone の無い datetime にして返す。datetime は date の子なので先に見る。
    時刻の無い日 (date だけ) は日時として読まない。
    """
    import datetime

    from django.utils import timezone

    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    message = f"{label}「{raw}」を日時 (YYYY-MM-DD HH:MM:SS) として読めません。"
    if isinstance(raw, datetime.datetime):
        value = raw
    elif isinstance(raw, datetime.date):
        raise ValueError(message)
    else:
        try:
            value = datetime.datetime.fromisoformat(str(raw).strip())
        except ValueError as exc:
            raise ValueError(message) from exc
    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    return value


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


# --- B 社: 受信した注文の写しと検収 ---


def _inbox_payload(entry, order_no, message_id):
    """受信した注文の写しの中身 (相手が送る注文のデータと同じ形のうち、受ける側が読む鍵だけ)。数は字で渡す。"""
    raw_lines = entry.get("明細") or []
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValueError("受信した注文の明細が空です。明細を 1 行以上書いてください。")
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
    """1 件の受信した注文の写しと、その受注の明細への検収の並び。(入れた数, 飛ばした数) を返す。"""
    from seizou.orders.models import Order

    _check_keys(entry, INBOX_KEYS, "受信した注文")
    partner = _partner_by_code(entry.get("取引先コード"))
    order_no = _text(entry.get("相手の発注番号"))
    if not order_no:
        raise ValueError(
            "相手の発注番号が空です。受信した注文の写しは 取引先 × 相手の発注番号 を自然な鍵にするので、書いてください。"
        )
    made = skipped = 0
    order = Order.objects.filter(customer=partner, customer_order_no=order_no).order_by("pk").first()
    if order is None:
        message_id = _message_id("inbox", partner.code, order_no)
        payload = _inbox_payload(entry, order_no, message_id)
        accepted_at = _datetime(entry.get("承認した日時"), "承認した日時")
        inbox = services.record_inbox(
            partner,
            payload,
            user,
            message_id=message_id,
            received_at=_datetime(entry.get("届いた日時"), "届いた日時"),
        )
        order = services.accept(inbox, {}, user, send=False, accepted_at=accepted_at)
        # 受け入れは渡した行を書き換えず、表の行を取り直して書くので、受注から取り直す (飛ばす道と同じ式)。
        inbox = order.inbox_message
        made += 1
    else:
        inbox = getattr(order, "inbox_message", None)
        if inbox is None:
            raise ValueError(
                f"受注 {order.order_no} (得意先の注文番号 {order_no}) は受信した注文から作った物ではありません。"
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


def _apply_reply(order, word, user, replied_at=None):
    """注文回答の写し。「作成」なら「送った」に、「送った」なら「相手が受け入れた」に進める (数えない)。

    replied_at は注文回答の日時の写し (無ければ services が今にする)。
    """
    from seizou.purchasing import services as purchasing_services
    from seizou.purchasing.models import PurchaseOrder

    if word not in REPLY_WORDS:
        raise ValueError(f"注文回答「{word}」は使えません。受け入れた だけを書けます。")
    current = PurchaseOrder.objects.get(pk=order.pk)
    if current.status == PurchaseOrder.STATUS_DRAFT:
        purchasing_services.mark_sent(current, user)
        current = PurchaseOrder.objects.get(pk=order.pk)
    if current.status == PurchaseOrder.STATUS_SENT:
        purchasing_services.mark_accepted(current, user, replied_at=replied_at)


def _reply_time(order, notice_days):
    """注文回答の写しの日時。発注日の翌日の 9:00 (会社の時刻の帯)。出荷の知らせがそれより早ければ、
    いちばん早い出荷日の 9:00 (発注日より前にはしない)。

    種を読んだ時刻にすると、流れの枠で「相手が受け入れた」が出荷の知らせと受入の後に並ぶため (精査 A9 の 3 節の 2)。
    """
    import datetime

    from django.utils import timezone

    day = order.order_date + datetime.timedelta(days=1)
    if notice_days:
        day = min(day, min(notice_days))
    day = max(day, order.order_date)
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(9, 0)))


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
    notice_days = []
    for index, raw in enumerate(entry.get("出荷の知らせ") or [], start=1):
        _check_keys(raw, NOTICE_KEYS, f"{index} 件目の出荷の知らせ")
        number = _line_no(raw.get("行番号"), "出荷の知らせ")
        notice_days.append(_date(raw.get("出荷日"), "出荷日"))
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
        _apply_reply(order, word, user, replied_at=_reply_time(order, notice_days))
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
    """種のフォルダの受信した注文の写しと検収 (acceptances.yaml。B 社) を入れる。core の demo.load が受注の後・出荷の前に呼ぶ (出荷と納品書の種が、受信した注文の写しの受注を指すため)。返す物は {"入れた": n, "飛ばした": m}。"""
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
