"""請求書の帳票の context (読むだけ。表に書かない)。

帳票のテンプレートは billing/invoice.html (core/report_base.html を継ぐ)。
数量・単価・金額は明細の写しをそのまま出し、ここでは計算し直さない。単価の単位の字は明細の写し (price_unit_text)。
出荷日と出荷番号の欄は、出荷の行は出荷の物、一式の行 (出荷に結び付かない行。決め PRICE-1d) は最後の出荷日と「受注 <番号>-<行> の一式」。
各行の customer_order_no は受注の「得意先の注文番号」の写し (得意先が自分の注文番号で照合するため。見つけ print-03)。空なら帳票に出ない。
"""

from seizou.core.services import company

from .services import TAX_RATE


def invoice_context(invoice):
    """請求書の帳票の context。

    company は core の会社の設定の 1 行 (登録番号を含む。インボイス制度の適格請求書の記載事項 = 決め LAW-3。帳票の土台 report_base.html が空でないときだけ出す)。
    """
    lines = []
    for row in invoice.lines.select_related("shipment", "order_line__item", "order_line__order"):
        item = row.order_line.item
        lines.append(
            {
                "shipped_at": row.shipped_on,
                "shipment_no": row.ref_no,
                "item_code": item.part_no,
                "item_name": item.name,
                "customer_order_no": row.order_line.order.customer_order_no,
                "quantity": row.qty,
                "unit": item.unit,
                "unit_price": row.unit_price,
                "price_unit": row.price_unit_text,
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
        "cancelled": invoice.status == invoice.STATUS_CANCELLED,
    }
