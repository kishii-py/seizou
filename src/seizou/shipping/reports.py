"""納品書の帳票の context を作る読むだけの関数 (帳票の設計の決め)。表に書かない。

納品書の表は数量と金額の写しを持たないので、明細は納品書に結ばれた出荷そのものから作る (R08)。
数量は出荷の数そのまま。単価は受注の明細の単価 (発行の請求書の出荷の行に入った出荷は、その請求書の明細の写しの単価・単価の単位・金額)。金額は shipping.services.amount_of_shipment (部品の単位は数量 × 単価を
取引先 (空なら会社) の小計の計算方法で丸めた値。一式の明細は明細を出し終えた出荷で一式の全額、ほかの出荷は 0 = 作者の決め)
(masters.services.round_amount。R03 と同じ 1 か所の丸め。DB で計算しない)。単価の後ろの字は受注の明細の price_unit_text (式なら「式」)。
税は扱わない (税は請求書と一緒)。
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import FieldDoesNotExist

from seizou.core.services import company

from .models import Shipment
from .services import amount_of_shipment

#: 納品書の帳票のテンプレート (core/report_base.html を継ぐ)
DELIVERY_NOTE_TEMPLATE = "shipping/delivery_note.html"


def _issued_invoice_lines(note) -> dict:
    """納品書の出荷のうち、発行の請求書の出荷の行に入った物の {出荷の pk: 請求書の明細} (読むだけ。問い合わせ 1 回)。

    請求書の部品が無ければ空の dict。読み方は shipping.services.invoiced_shipments と同じ (請求書の明細の表は出荷の逆の関係
    invoice_lines の先として引き、「発行」の値は明細の外部キー invoice の先の表の STATUS_ISSUED から読む。billing を import しない)。
    1 つの出荷が発行の請求書の明細に 2 行あれば、明細の pk の小さい方を返す。一式の行は出荷を指さないので入らない。
    """
    try:
        invoice_lines = Shipment._meta.get_field("invoice_lines").related_model
    except FieldDoesNotExist:
        return {}
    issued = invoice_lines._meta.get_field("invoice").related_model.STATUS_ISSUED
    found = {}
    rows = invoice_lines._default_manager.filter(shipment__delivery_note=note, invoice__status=issued).order_by(
        "shipment_id", "pk"
    )
    for line in rows:
        found.setdefault(line.shipment_id, line)
    return found


def delivery_note_context(note) -> dict:
    """納品書 1 枚の帳票の context。

    company = 出荷元の {"name", "address", "phone", "logo"} (振込先は請求書だけに出すので渡さない。出荷ラベルと同じ形)、note = {"number", "date"}、customer = 得意先、
    lines = 出荷ごとの {"shipment_no", "shipped_at", "item_code", "item_name", "customer_order_no", "quantity", "unit", "unit_price", "price_unit", "amount"}
    (customer_order_no は受注の「得意先の注文番号」の写し。得意先が自分の注文番号で照合するため = 作者の決め。空なら帳票に出ない)、
    total = 行に出した金額の合計、cancelled = 状態が取消なら真。
    発行の請求書の出荷の行に入った出荷 (_issued_invoice_lines) は、unit_price・price_unit・amount を請求書の明細の
    unit_price・price_unit_text・amount にする。ほかの出荷は受注の明細の単価と amount_of_shipment。
    数量・品番・品名・単位・得意先の注文番号は、どちらも出荷と受注から読む。

    訳: 発行の請求書に入った出荷は請求書の明細の写しで刷り、受注の単価を後で直しても、同じ出荷の金額を渡した納品書と請求書で同じにするため。
    請求書に入っていない出荷は受注の明細の単価で刷る (納品書は写しを持たない = 本人の決め 11-07 23:4x)。
    出所 = AI の案 (開発の記録 docs/nights/20261111/N376.md)。
    変えてよい条件 = 本人が納品書にも写しを持たせると決めたとき (そのときは納品書の写しを読む)。
    """
    customer = note.customer
    shipments = note.shipments.select_related("order_line__item", "order_line__order").order_by("shipped_at", "pk")
    invoiced = _issued_invoice_lines(note)
    lines = []
    total = Decimal("0")
    for shipment in shipments:
        order_line = shipment.order_line
        invoice_line = invoiced.get(shipment.pk)
        if invoice_line is not None:
            unit_price = invoice_line.unit_price
            price_unit = invoice_line.price_unit_text
            amount = invoice_line.amount
        else:
            unit_price = order_line.unit_price
            price_unit = order_line.price_unit_text
            amount = amount_of_shipment(shipment, partner=customer)
        lines.append(
            {
                "shipment_no": shipment.shipment_no,
                "shipped_at": shipment.shipped_at,
                "item_code": order_line.item.part_no,
                "item_name": order_line.item.name,
                "customer_order_no": order_line.order.customer_order_no,
                "quantity": shipment.qty,
                "unit": order_line.unit,
                "unit_price": unit_price,
                "price_unit": price_unit,
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
        "cancelled": note.status == note.STATUS_CANCELLED,
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
