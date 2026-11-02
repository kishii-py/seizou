"""shipping の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して出荷と納品書を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- 中心の決まりに当たったら RuleViolation(code, message)。message は RULES.md の番号の文と、次にすること。
- 分納を許さない設定 (partial_shipment が偽) の違反も RuleViolation で、code は設定の鍵の名前。
- 決まりの番号の無い入力の誤り (出荷の一覧が空) は日本語の ValueError。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 受注より多い出荷のポカよけは検査の層 (seizou.core.checks) が services の前に見る。
  ここでは止めず、残を超える数もそのまま保存する (現物優先)。
- 出荷済みの数と残の数は orders.services の関数で出す (検査の層と同じ関数)。
  集計の前に、受注の明細の行を select_for_update でロックする (01 の 10.1)。
- 受注 (Order・OrderLine) は書かない。出荷を作った後、同じトランザクションの中で orders.services.refresh_status を呼び、受注の状態を進める。
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from seizou.core.conf import setting
from seizou.core.exceptions import RuleViolation
from seizou.core.services import next_number
from seizou.orders.services import refresh_status, remaining_qty

from .models import DeliveryNote, Shipment

_R02_MESSAGE = "数量と単価は 0 より大きい。0 より大きい数を入れてください。"
_R05_RULE = "取消した受注には、出荷も指示書も作れない。"
_R08_RULE = "納品書の数量は出荷の数量と一致する。納品書を出した後の出荷は、納品書を取り消してから直す。"

# 受注の明細の表は、出荷の外部キーの先として引く (orders の models を import しない)。
_OrderLine = Shipment._meta.get_field("order_line").related_model


def _dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def ship(order_line, qty, shipped_at, user, *, note=""):
    """受注の明細を qty だけ出荷する。出荷番号は core の連番が付ける。"""
    qty = _dec(qty)
    with transaction.atomic():
        locked = _OrderLine.objects.select_for_update().get(pk=order_line.pk)
        if locked.order.status == "cancelled":
            raise RuleViolation(
                "R05", f"{_R05_RULE}取消していない受注の明細を選んでください。"
            )
        if not qty > 0:
            raise RuleViolation("R02", _R02_MESSAGE)
        if not setting("partial_shipment"):
            remaining = remaining_qty(locked)
            if qty != remaining:
                raise RuleViolation(
                    "partial_shipment",
                    f"分納を許さない設定です。残の数 ({remaining}) をそのまま出荷してください。",
                )
        shipment = Shipment.objects.create(
            shipment_no=next_number("shipment"),
            order_line=locked,
            qty=qty,
            shipped_at=shipped_at,
            note=note,
        )
        refresh_status(locked.order, user)
        return shipment


def create_delivery_note(customer, shipments, user):
    """得意先の出荷をまとめて納品書を作る。納品書番号は core の連番が付ける。

    納品書は数量の写しを持たない。結ばれた出荷は delivery_note.shipments.all() で引く。
    """
    pks = sorted({shipment.pk for shipment in shipments})
    if not pks:
        raise ValueError("出荷がありません。納品書にまとめる出荷を 1 つ以上選んでください。")
    with transaction.atomic():
        locked = list(Shipment.objects.select_for_update().filter(pk__in=pks).order_by("pk"))
        for shipment in locked:
            if shipment.order_line.order.customer_id != customer.pk:
                raise RuleViolation(
                    "R08",
                    f"{_R08_RULE}納品書は得意先ごとに作るので、"
                    f"出荷 {shipment.shipment_no} を外してください。",
                )
            current = shipment.delivery_note
            if current is not None and current.status == DeliveryNote.STATUS_ISSUED:
                raise RuleViolation(
                    "R08",
                    f"{_R08_RULE}出荷 {shipment.shipment_no} は納品書 {current.note_no} に入っているので、"
                    "外してください。",
                )
        note = DeliveryNote.objects.create(
            note_no=next_number("delivery_note"),
            customer=customer,
            issued_on=timezone.localdate(),
        )
        for shipment in locked:
            shipment.delivery_note = note
            shipment.save(update_fields=["delivery_note"])
        return note
