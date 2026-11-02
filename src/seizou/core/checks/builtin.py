"""同梱のポカよけ (RULES.md の「同梱のポカよけ」。R01・R04・R06・R07)。

mode は会社の設定 (over_shipment・over_workorder) の値をそのまま使う。既定は warn。
文は RULES.md の番号の文と同じ趣旨で、何が起きて何をすればよいかを書く。
data の鍵は各 action を呼ぶ画面の土台と services の約束 (01 の 10.4 の引数の名前) に合わせる。
"""

from __future__ import annotations

from decimal import Decimal

from seizou.core.checks import Check, register
from seizou.core.checks import tools


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


class OverShipment(Check):
    """R01: 出荷の数の合計が、受注の明細の数を超えたら。"""

    name = "R01"
    action = "shipping.ship"
    setting_key = "over_shipment"

    def run(self, data, user):
        line = data.get("order_line")
        qty = data.get("qty")
        if line is None or qty is None:
            return None
        total = tools.shipped_qty(line) + _dec(qty)
        if total > _dec(line.qty):
            return (
                f"出荷の数の合計 ({total}) が受注の数 ({line.qty}) を超えます。"
                "数を確かめてください。"
            )
        return None


class OrderBelowShipped(Check):
    """R04: 出荷済みの数より少ない数量に、受注の明細を変えたら。"""

    name = "R04"
    action = "orders.update_order"
    setting_key = "over_shipment"

    def run(self, data, user):
        line = data.get("order_line")
        qty = data.get("qty")
        if line is None or qty is None:
            return None
        shipped = tools.shipped_qty(line)
        if _dec(qty) < shipped:
            return (
                f"受注の数 ({qty}) が出荷済みの数 ({shipped}) より少なくなります。"
                "数を確かめてください。"
            )
        return None


class OverWorkorder(Check):
    """R06: 指示書の数量の合計が、受注の明細の数を超えたら。"""

    name = "R06"
    action = "workorders.issue_workorder"
    setting_key = "over_workorder"

    def run(self, data, user):
        line = data.get("order_line")
        qty = data.get("qty")
        if line is None or qty is None:
            return None
        total = tools.issued_qty(line) + _dec(qty)
        if total > _dec(line.qty):
            return (
                f"指示書の数量の合計 ({total}) が受注の数 ({line.qty}) を超えます。"
                "数を確かめてください。"
            )
        return None


class OverProgress(Check):
    """R07: 進捗の実績の数が、その工程の予定の数を超えたら。"""

    name = "R07"
    action = "progress.record_progress"
    setting_key = "over_workorder"

    def run(self, data, user):
        step = data.get("workorder_step")
        qty = data.get("qty")
        if step is None or qty is None:
            return None
        total = tools.recorded_qty(step) + _dec(qty)
        if total > _dec(step.planned_qty):
            return (
                f"実績の合計 ({total}) が予定の数 ({step.planned_qty}) を超えます。"
                "数を確かめてください。"
            )
        return None


BUILTIN = (OverShipment(), OrderBelowShipped(), OverWorkorder(), OverProgress())

for _check in BUILTIN:
    register(_check)
