"""請求書の帳票の context (読むだけ。表に書かない)。

帳票のテンプレートは billing/invoice.html (core/report_base.html を継ぐ)。
数量・単価・金額は明細の写しをそのまま出し、ここでは計算し直さない。単価の単位の字は明細の price_unit_text (写しの単位を読む)。
品番・品名・単位・得意先の注文番号・会社の登録番号・住所・電話・振込先・得意先の名前・税率は発行の時の写し (billing.models の訳)。
写しが空でも今の値で補わない。会社名とロゴは写さないので、今の会社の設定を読む。
出荷日と出荷番号の欄は、出荷の行は明細の出荷日と出荷番号の写し (出荷を消した行も同じ字。依頼書 N403c)、
一式の行 (出荷に結び付かない行。作者の決め) は最後の出荷日と「受注 <番号>-<行> の一式」(InvoiceLine.shipped_on・ref_no)。
各行の customer_order_no は、得意先が自分の注文番号で照合するため (作者の決め)。空なら帳票に出ない。
前回の残 (previous_balance) は請求書を作る時の写し (会社の設定が「載せる」のときだけ。空なら None)。今回のご請求額 (billed_now) は
写しと合計の足し算だけで、写しが空なら None (帳票は今の字のまま)。写した後に入金と調整が変わっても、刷る字は変わらない (依頼書 N409b)。
"""

from decimal import Decimal

from seizou.core.services import company

#: 請求書の帳票のテンプレート (core/report_base.html を継ぐ。印刷用の画面・PDF・発行の時の写しが同じ名前を読む)
INVOICE_TEMPLATE = "billing/invoice.html"


def invoice_context(invoice):
    """請求書の帳票の context。

    company は、発行の時の写しの登録番号・住所・電話・振込先と、今の会社の設定の会社名とロゴを合わせた辞書
    (登録番号はインボイス制度の適格請求書の記載事項 = 作者の決め。帳票の土台 report_base.html は空でない鍵だけを出す)。
    """
    lines = []
    for row in invoice.lines.select_related("order_line__order"):
        lines.append(
            {
                "shipped_at": row.shipped_on,
                "shipment_no": row.ref_no,
                "item_code": row.part_no,
                "item_name": row.item_name,
                "customer_order_no": row.customer_order_no,
                "quantity": row.qty,
                "unit": row.unit,
                "unit_price": row.unit_price,
                "price_unit": row.price_unit_text,
                "amount": row.amount,
            }
        )
    current = company()
    previous_balance = invoice.previous_balance
    return {
        "company": {
            "name": current.name,
            "logo": current.logo,
            "registration_no": invoice.issuer_registration_no,
            "address": invoice.issuer_address,
            "phone": invoice.issuer_phone,
            "bank_account": invoice.issuer_bank_account,
        },
        "invoice": {
            "number": invoice.invoice_no,
            "issued_on": invoice.issued_on,
            "period_from": invoice.period_from,
            "period_to": invoice.period_to,
            "due_on": invoice.due_on,
        },
        "customer": {"name": invoice.customer_name},
        "lines": lines,
        "subtotal": invoice.subtotal,
        "tax": invoice.tax,
        "total": invoice.total,
        "previous_balance": previous_balance,
        "billed_now": None if previous_balance is None else previous_balance + invoice.total,
        "tax_rate_percent": _percent_text(invoice.tax_rate),
        "cancelled": invoice.status == invoice.STATUS_CANCELLED,
    }


def _percent_text(rate):
    """税率の写し (百分率の Decimal) の字。10.00 は「10」、8.00 は「8」、小数のある率は小数のまま (8.50 は「8.5」)。

    小数 2 桁にそろえてから末尾の 0 と小数点を落とす (Decimal の normalize() は 10 を「1E+1」にするので使わない)。
    """
    return format(Decimal(rate).quantize(Decimal("0.01")), "f").rstrip("0").rstrip(".")
