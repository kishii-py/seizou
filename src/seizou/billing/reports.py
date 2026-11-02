"""請求書の帳票の context (読むだけ。表に書かない)。

帳票のテンプレートは billing/invoice.html (core/report_base.html を継ぐ)。
数量・単価・金額は明細の写しをそのまま出し、ここでは計算し直さない。
"""

from seizou.core.services import company

from .services import TAX_RATE


def invoice_context(invoice):
    """請求書の帳票の context。company は core の会社の設定の 1 行。"""
    lines = []
    for row in invoice.lines.select_related("shipment__order_line__item"):
        shipment = row.shipment
        item = shipment.order_line.item
        lines.append(
            {
                "shipped_at": shipment.shipped_at,
                "shipment_no": shipment.shipment_no,
                "item_code": item.part_no,
                "item_name": item.name,
                "quantity": row.qty,
                "unit": item.unit,
                "unit_price": row.unit_price,
                "amount": row.amount,
            }
        )
    return {
        "company": company(),
        "invoice": {
            "number": invoice.invoice_no,
            "issued_on": invoice.issued_on,
            "period_from": invoice.period_from,
            "period_to": invoice.period_to,
            "due_on": invoice.due_on,
        },
        "customer": {"name": invoice.customer.name},
        "lines": lines,
        "subtotal": invoice.subtotal,
        "tax": invoice.tax,
        "total": invoice.total,
        "tax_rate_percent": int(TAX_RATE * 100),
    }
