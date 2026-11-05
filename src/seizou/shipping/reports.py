"""納品書の帳票の context を作る読むだけの関数 (05 の 6 節)。表に書かない。

納品書の表は数量と金額の写しを持たないので、明細は納品書に結ばれた出荷そのものから作る (R08)。
数量は出荷の数そのまま。単価は受注の明細の単価。金額は数量 × 単価を、取引先 (空なら会社) の
小計の計算方法で丸めた値 (masters.services.round_amount。R03 と同じ 1 か所の丸め。DB で計算しない)。
税は扱わない (税は請求書と一緒)。
"""

from __future__ import annotations

from decimal import Decimal

from seizou.core.services import company
from seizou.masters.services import round_amount

#: 納品書の帳票のテンプレート (core/report_base.html を継ぐ)
DELIVERY_NOTE_TEMPLATE = "shipping/delivery_note.html"


def delivery_note_context(note) -> dict:
    """納品書 1 枚の帳票の context。

    company = 出荷元の {"name", "address", "phone", "logo"} (振込先は請求書だけに出すので渡さない。出荷ラベルと同じ形)、note = {"number", "date"}、customer = 得意先、
    lines = 出荷ごとの {"shipment_no", "shipped_at", "item_code", "item_name", "quantity", "unit", "unit_price", "amount"}、
    total = 金額の合計。
    """
    customer = note.customer
    shipments = note.shipments.select_related("order_line__item").order_by("shipped_at", "pk")
    lines = []
    total = Decimal("0")
    for shipment in shipments:
        order_line = shipment.order_line
        amount = round_amount(customer, shipment.qty * order_line.unit_price)
        lines.append(
            {
                "shipment_no": shipment.shipment_no,
                "shipped_at": shipment.shipped_at,
                "item_code": order_line.item.part_no,
                "item_name": order_line.item.name,
                "quantity": shipment.qty,
                "unit": order_line.unit,
                "unit_price": order_line.unit_price,
                "amount": amount,
            }
        )
        total += amount
    row = company()
    return {
        "company": {"name": row.name, "address": row.address, "phone": row.phone, "logo": row.logo},
        "note": {"number": note.note_no, "date": note.issued_on},
        "customer": customer,
        "lines": lines,
        "total": total,
    }


#: 出荷ラベルの帳票のテンプレート (core/report_base.html を継ぐ)
SHIPMENT_LABEL_TEMPLATE = "shipping/shipment_label.html"


def shipment_label_context(shipment) -> dict:
    """出荷 1 つのラベルの context (1 出荷 = 1 枚。表を読むだけ)。

    company = 出荷元の {"name", "address", "phone"} (振込先とロゴは出さない)、
    label = {"number", "shipped_at", "customer", "order_no", "customer_order_no", "part_no", "item_name", "qty", "unit"}。
    数量は出荷の数そのまま、単位は受注の明細の単位。
    """
    order_line = shipment.order_line
    order = order_line.order
    row = company()
    return {
        "company": {"name": row.name, "address": row.address, "phone": row.phone},
        "label": {
            "number": shipment.shipment_no,
            "shipped_at": shipment.shipped_at,
            "customer": order.customer.name,
            "order_no": order.order_no,
            "customer_order_no": order.customer_order_no,
            "part_no": order_line.item.part_no,
            "item_name": order_line.item.name,
            "qty": shipment.qty,
            "unit": order_line.unit,
        },
    }
