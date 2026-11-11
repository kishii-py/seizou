"""検査の層で使う道具 (検査の層の設計の決め)。

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


def has_active_staff() -> bool:
    """社員の一覧に使用中の社員が 1 人でも居るか (R26 が読む)。masters の部品が無ければ False。"""
    from django.apps import apps

    if not apps.is_installed("seizou.masters"):
        return False
    from seizou.masters import services

    return services.has_active_staff()


def has_active_defect_kinds() -> bool:
    """不良の区分の一覧に使用中の区分が 1 つでもあるか (R30 が読む)。masters の部品が無ければ False。"""
    from django.apps import apps

    if not apps.is_installed("seizou.masters"):
        return False
    from seizou.masters import services

    return services.has_active_defect_kinds()


def billed_by(shipment):
    """出荷が請求済みなら (種類, 記録) の組、でなければ None (R27 が読む。shipping.services.billed_by)。"""
    from seizou.shipping import services

    return services.billed_by(shipment)


def delete_refusal(shipment) -> str:
    """出荷を消せない訳の文 (消せるなら空の字。R27 が読む。shipping.services.delete_refusal)。"""
    from seizou.shipping import services

    return services.delete_refusal(shipment)


def billed_notice(delivery_note) -> str:
    """請求済みの出荷の入った納品書を取り消す前の知らせの文 (請求済みの出荷が無ければ空の字。R27 が読む。shipping.services.billed_notice)。"""
    from seizou.shipping import services

    return services.billed_notice(delivery_note)


def completes_without_lump_amount(purchase_order_line, *, adding_qty=None) -> bool:
    """一式の発注の明細を、一式の金額が載らないまま完了にするか (R29 が読む。purchasing.services の同じ名前の関数)。purchasing の部品が無ければ False。"""
    from django.apps import apps

    if not apps.is_installed("seizou.purchasing"):
        return False
    from seizou.purchasing import services

    return services.completes_without_lump_amount(purchase_order_line, adding_qty=adding_qty)


def same_customer_order_no(customer, customer_order_no, exclude_pk=None) -> list[str]:
    """同じ得意先で、得意先の注文番号の照合の字が同じ受注 (取消を除く) の受注番号の並び (R31 が読む。orders.services の同じ名前の関数)。"""
    from seizou.orders import services

    return services.same_customer_order_no(customer, customer_order_no, exclude_pk=exclude_pk)


def standard_price(item) -> Decimal | None:
    """品目の標準単価。"""
    return getattr(item, "standard_price", None)


def partner_of(order):
    """受注の得意先 (取引先)。"""
    return getattr(order, "customer", None)
