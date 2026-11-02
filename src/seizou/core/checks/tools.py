"""検査の層で使う道具 (01 の 10.7)。

表に直接触らずに状態を読む関数だけ。会社の検査はこの道具だけで書く (R13・R14 を破らない)。
中身は各部品の services に任せる (部品が無い間は呼べない)。
"""

from __future__ import annotations

from decimal import Decimal

from seizou.core.conf import setting  # noqa: F401  (会社の検査が使う)


def shipped_qty(order_line) -> Decimal:
    """受注の明細に対する出荷済みの数 (出荷から集計)。"""
    from seizou.orders import services

    return services.shipped_qty(order_line)


def remaining_qty(order_line) -> Decimal:
    """受注の明細の残 (受注の数 − 出荷済み。マイナスも返す)。"""
    from seizou.orders import services

    return services.remaining_qty(order_line)


def issued_qty(order_line) -> Decimal:
    """受注の明細に対する指示書の数量の合計。"""
    from seizou.workorders import services

    return services.issued_qty(order_line)


def recorded_qty(workorder_step) -> Decimal:
    """指示書の工程に対する実績の合計。"""
    from seizou.progress import services

    return services.recorded_qty(workorder_step)


def standard_price(item) -> Decimal | None:
    """品目の標準単価。"""
    return getattr(item, "standard_price", None)


def partner_of(order):
    """受注の得意先 (取引先)。"""
    return getattr(order, "customer", None)
