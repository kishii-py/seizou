"""core の公開する関数 (01 の 10.3)。

- company(): 会社の設定 (Company) の 1 行。無ければ既定で作る。
- next_number(kind): 会社ごとの連番。kind は "order"・"workorder"・"shipment"・"delivery_note"・"invoice"・"purchase_order"。
  形は選べる設定 order_number で決まる (seq = 連番 / ym_seq = 年月 + 連番。月ごとに 1 から)。
  トランザクションの中でだけ呼べる (外で呼ぶと RuntimeError)。行をロックして増やすので、同時に呼んでも重ならない。
"""

from __future__ import annotations

from django.db import connection, transaction
from django.utils import timezone

from seizou.core.conf import setting
from seizou.core.models import Company, NumberSequence

PREFIXES = {
    "order": "SO",
    "workorder": "WO",
    "shipment": "SH",
    "delivery_note": "DN",
    "invoice": "IV",
    "purchase_order": "PO",
}


def company() -> Company:
    """会社の設定の 1 行。無ければ既定の 1 行を作って返す。"""
    row = Company.objects.order_by("pk").first()
    if row is None:
        row = Company.objects.create(name="会社")
    return row


def next_number(kind: str) -> str:
    """kind の次の番号を返す。トランザクションの外で呼ぶと RuntimeError。"""
    if kind not in PREFIXES:
        raise KeyError(f"知らない連番の種類: {kind}")
    if not connection.in_atomic_block:
        raise RuntimeError("next_number はトランザクション (transaction.atomic) の中で呼ぶ")
    form = setting("order_number")
    if form == "ym_seq":
        period = timezone.localdate().strftime("%y%m")
    else:
        period = ""
    row, _created = NumberSequence.objects.select_for_update().get_or_create(
        kind=kind, period=period, defaults={"last": 0}
    )
    row.last += 1
    row.save(update_fields=["last"])
    prefix = PREFIXES[kind]
    if period:
        return f"{prefix}-{period}-{row.last:04d}"
    return f"{prefix}-{row.last:06d}"


__all__ = ["company", "next_number", "transaction"]
