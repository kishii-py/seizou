"""2 社の作り物の在庫と受入の種 (core/demo.py の PART_SEEDS が呼ぶ)。

load(seeds_dir, user) が、種のフォルダの stock_moves.yaml (在庫の動き) と receipts.yaml (受入。発注も一緒) を読み、
services の関数で入れる。どちらのファイルも無ければ何もせず {"入れた": 0, "飛ばした": 0} を返す (出力の行を出さない)。
在庫の動きの製造の完了は指示書の種 (workorders.yaml) の指示書を指す。指示書が見つからないときは、ほかの誤りと同じく
ValueError で止まる (指示書の種の無いフォルダに在庫の動きの種を置かない。core の試験は古い置き場を写すときに在庫の種も外す)。

- 書くのは services の関数だけ (stock.services・purchasing.services)。表に直接書かない。標準出力に書かない。
- 2 回入れても重ならない。自然な鍵で既にある物は飛ばす: 製造の完了 = その指示書に製造の完了の動きが既にある、
  払い出し = 同じ部品 × 日付 × 種類 × 数量の動きがある、発注 = 取引先コード × 備考、受入 = 発注の明細 × 受入日。
- 1 件でも services が断ったら、ファイルと「N 件目」を文に入れて ValueError を上げる
  (core の demo.load の transaction.atomic が全体を巻き戻す)。「注意」の文は足さない。
- 発注は相手に送らない (create_purchase_order のキーワード send を偽で渡す)。種の取引先に向き「送る」のつながりがあっても送らない。
- 数える物: 在庫の動き・発注・受入をそれぞれ 1 件と数える (受入が作る入庫の動きは数えない)。
- ほかの部品の services と model は関数の中で import する。取引先・部品・受注の明細の引き方は core/demo.py の物を写した
  (core の private な関数を import しない)。YAML の読み方は core の公開の seeds の関数を使う。
"""

from __future__ import annotations

from pathlib import Path

from django.contrib.contenttypes.models import ContentType

from seizou.core.seeds import (
    check_keys as _check_keys,
    date as _date,
    decimal as _decimal,
    guard as _guard,
    read as _read,
    text as _text,
)

from . import services
from .models import KIND_FINISHED, KIND_ISSUED, StockMove

MOVES_FILE = "stock_moves.yaml"
RECEIPTS_FILE = "receipts.yaml"
MOVES_ROOT = "在庫の動き"
RECEIPTS_ROOT = "発注"

KIND_WORDS = {"製造の完了": KIND_FINISHED, "払い出し": KIND_ISSUED}
FINISHED_KEYS = ("種類", "得意先の取引先コード", "得意先の注文番号", "行番号", "数量", "日付", "備考")
ISSUED_KEYS = ("種類", "自社品番", "数量", "日付", "備考")
ORDER_KEYS = ("取引先コード", "発注日", "備考", "明細", "受入")
ORDER_LINE_KEYS = ("自社品番", "数量", "単価", "納期")
RECEIPT_KEYS = ("行番号", "受入日", "数量", "備考")


# --- 引き方 (core/demo.py の _partner_by_code・_item_by_part_no・_order_line_by_key を写した) ---


def _partner_by_code(code):
    from seizou.masters.models import Partner

    partner = Partner.objects.filter(code=str(code).strip()).first()
    if partner is None:
        raise ValueError(f"取引先コード {code} が取引先にありません。取引先の種に行を足してください。")
    return partner


def _item_by_part_no(part_no):
    from seizou.masters.models import Item

    item = Item.objects.filter(part_no=str(part_no).strip()).first()
    if item is None:
        raise ValueError(f"自社品番 {part_no} が品目にありません。品目の種に行を足してください。")
    return item


def _order_line_by_key(customer, customer_order_no, line_no):
    """得意先 × 得意先の注文番号 × 行番号で、受注の明細を 1 つ引く。無い・2 つ以上は日本語の ValueError。"""
    from seizou.orders.models import OrderLine

    order_no = _text(customer_order_no)
    if not order_no:
        raise ValueError("得意先の注文番号が空です。受注の明細を指すので、書いてください。")
    try:
        number = int(str(line_no).strip())
    except ValueError as exc:
        raise ValueError(f"行番号「{line_no}」を数として読めません。") from exc
    found = list(
        OrderLine.objects.filter(order__customer=customer, order__customer_order_no=order_no, line_no=number)[:2]
    )
    where = f"取引先コード {customer.code}・得意先の注文番号 {order_no}・行番号 {number}"
    if not found:
        raise ValueError(f"受注の明細が見つかりません ({where})。受注の種に行を足してください。")
    if len(found) > 1:
        raise ValueError(f"受注の明細が 2 つ以上あります ({where})。")
    return found[0]


def _workorder_of(line):
    """受注の明細の取消でない指示書 (発行日 → pk の順の最初)。無ければ ValueError。"""
    from seizou.workorders.models import WorkOrder

    workorder = (
        WorkOrder.objects.filter(order_line=line)
        .exclude(status=WorkOrder.STATUS_CANCELLED)
        .order_by("issued_on", "pk")
        .first()
    )
    if workorder is None:
        raise ValueError("その受注の明細に指示書がありません。指示書の種に行を足してください。")
    return workorder


# --- 在庫の動き ---


def _load_move(entry, user):
    """1 件の在庫の動き。入れたら真、自然な鍵で既にあれば偽。"""
    kind = KIND_WORDS.get(_text(entry.get("種類") if isinstance(entry, dict) else None))
    if kind is None:
        raise ValueError(f"種類は {'・'.join(KIND_WORDS)} のどれかにしてください。")
    if kind == KIND_FINISHED:
        _check_keys(entry, FINISHED_KEYS, "在庫の動き")
        customer = _partner_by_code(entry.get("得意先の取引先コード"))
        line = _order_line_by_key(customer, entry.get("得意先の注文番号"), entry.get("行番号"))
        workorder = _workorder_of(line)
        exists = StockMove.objects.filter(
            kind=KIND_FINISHED,
            content_type=ContentType.objects.get_for_model(workorder),
            object_id=workorder.pk,
        ).exists()
        if exists:
            return False
        services.record_finished(
            workorder,
            _decimal(entry.get("数量"), "数量"),
            user,
            moved_on=_date(entry.get("日付"), "日付"),
            note=_text(entry.get("備考")),
        )
        return True
    _check_keys(entry, ISSUED_KEYS, "在庫の動き")
    item = _item_by_part_no(entry.get("自社品番"))
    qty = _decimal(entry.get("数量"), "数量")
    moved_on = _date(entry.get("日付"), "日付")
    if StockMove.objects.filter(item=item, moved_on=moved_on, kind=KIND_ISSUED, qty=-abs(qty)).exists():
        return False
    services.record_issued(item, qty, moved_on, user, note=_text(entry.get("備考")))
    return True


def _load_moves(path, user):
    made = skipped = 0
    for number, entry in enumerate(_read(path, MOVES_ROOT), start=1):
        with _guard(path.name, number):
            if _load_move(entry, user):
                made += 1
            else:
                skipped += 1
    return made, skipped


# --- 発注と受入 ---


def _load_order(entry, user):
    """1 件の発注と、その受入の並び。(入れた数, 飛ばした数) を返す (発注と受入をそれぞれ 1 件と数える)。"""
    from seizou.purchasing import services as purchasing_services
    from seizou.purchasing.models import PurchaseOrder, Receipt

    _check_keys(entry, ORDER_KEYS, "発注")
    partner = _partner_by_code(entry.get("取引先コード"))
    note = _text(entry.get("備考"))
    if not note:
        raise ValueError("発注の備考が空です。種の発注は備考を自然な鍵にするので、固有の字を書いてください。")
    made = skipped = 0
    order = PurchaseOrder.objects.filter(partner=partner, note=note).order_by("pk").first()
    if order is None:
        lines = []
        for index, raw in enumerate(entry.get("明細") or [], start=1):
            _check_keys(raw, ORDER_LINE_KEYS, f"{index} 行目の明細")
            lines.append(
                {
                    "item": _item_by_part_no(raw.get("自社品番")),
                    "qty": _decimal(raw.get("数量"), "数量"),
                    "unit_price": _decimal(raw.get("単価"), "単価"),
                    "due_date": _date(raw.get("納期"), "納期") if raw.get("納期") not in (None, "") else None,
                }
            )
        order = purchasing_services.create_purchase_order(
            partner, lines, user, order_date=_date(entry.get("発注日"), "発注日"), note=note, send=False
        )
        made += 1
    else:
        skipped += 1
    for index, raw in enumerate(entry.get("受入") or [], start=1):
        _check_keys(raw, RECEIPT_KEYS, f"{index} 件目の受入")
        try:
            line_no = int(str(raw.get("行番号")).strip())
        except ValueError as exc:
            raise ValueError(f"受入の行番号「{raw.get('行番号')}」を数として読めません。") from exc
        line = order.lines.filter(line_no=line_no).first()
        if line is None:
            raise ValueError(f"受入の行番号 {line_no} の明細がその発注にありません。")
        received_on = _date(raw.get("受入日"), "受入日")
        if Receipt.objects.filter(line=line, received_on=received_on).exists():
            skipped += 1
            continue
        purchasing_services.receive(
            line, _decimal(raw.get("数量"), "数量"), received_on, user, note=_text(raw.get("備考"))
        )
        made += 1
    return made, skipped


def _load_receipts(path, user):
    made = skipped = 0
    for number, entry in enumerate(_read(path, RECEIPTS_ROOT), start=1):
        with _guard(path.name, number):
            entry_made, entry_skipped = _load_order(entry, user)
        made += entry_made
        skipped += entry_skipped
    return made, skipped


def load(seeds_dir, user) -> dict:
    """種のフォルダの在庫の動きと受入を入れる。返す物は {"入れた": n, "飛ばした": m}。"""
    seeds_dir = Path(seeds_dir)
    made = skipped = 0
    moves = seeds_dir / MOVES_FILE
    if moves.is_file():
        entry_made, entry_skipped = _load_moves(moves, user)
        made += entry_made
        skipped += entry_skipped
    receipts = seeds_dir / RECEIPTS_FILE
    if receipts.is_file():
        entry_made, entry_skipped = _load_receipts(receipts, user)
        made += entry_made
        skipped += entry_skipped
    return {"入れた": made, "飛ばした": skipped}
