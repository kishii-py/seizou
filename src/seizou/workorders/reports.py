"""現品票の帳票の context を作る読むだけの関数 (02 の指示書、05 の 6 節)。表に書かない。

現品票は社内の札なので、会社の情報は名前だけを渡す (住所・電話・振込先・ロゴは出さない)。
品目・受注・得意先は指示書の外部キーをたどって読む (orders と masters を import しない)。
数は表の値をそのまま渡し、ここでもテンプレートでも計算しない。
"""

from __future__ import annotations

from seizou.core.services import company

#: 現品票の帳票のテンプレート (core/report_base.html を継ぐ)
WORKORDER_TAG_TEMPLATE = "workorders/workorder_tag.html"


def workorder_tag_context(workorder) -> dict:
    """指示書 1 枚の現品票の context。

    company = {"name"}、tag = 指示書の要点
    ({"number", "issued_on", "part_no", "item_name", "qty", "unit", "due_date", "customer", "order_no", "customer_order_no"}。
    unit は受注の明細の単位で、数量の後ろに出す = 見つけ print-06)、
    steps = 工程の順の {"seq", "process", "planned_qty", "start", "end"}、cancelled = 状態が取消なら真。
    """
    order_line = workorder.order_line
    order = order_line.order
    item = order_line.item
    steps = workorder.steps.select_related("process").order_by("seq")
    return {
        "company": {"name": company().name},
        "tag": {
            "number": workorder.wo_no,
            "issued_on": workorder.issued_on,
            "part_no": item.part_no,
            "item_name": item.name,
            "qty": workorder.qty,
            "unit": order_line.unit,
            "due_date": order_line.due_date,
            "customer": order.customer.name,
            "order_no": order.order_no,
            "customer_order_no": order.customer_order_no,
        },
        "steps": [
            {
                "seq": step.seq,
                "process": step.process.name,
                "planned_qty": step.planned_qty,
                "start": step.planned_start,
                "end": step.planned_end,
            }
            for step in steps
        ],
        "cancelled": workorder.status == workorder.STATUS_CANCELLED,
    }
