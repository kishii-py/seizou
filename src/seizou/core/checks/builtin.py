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


class OverShipmentOnUpdate(Check):
    """R01: 出荷を直した数で、出荷の数の合計が受注の明細の数を超えたら (出荷の編集)。

    data の object は直す前の出荷 (土台の入力の画面が渡す)。合計は、出荷済みの数から直す前の数を引き、新しい数を足す。
    """

    name = "R01"
    action = "shipping.update_shipment"
    setting_key = "over_shipment"

    def run(self, data, user):
        shipment = data.get("object")
        qty = data.get("qty")
        if shipment is None or qty is None:
            return None
        line = shipment.order_line
        total = tools.shipped_qty(line) - _dec(shipment.qty) + _dec(qty)
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
        # 受注の入力は明細が複数 (lines = [{"order_line": 明細, "qty": 数}, ...])。1 行の形 (order_line・qty) も受ける。
        pairs = []
        for item in data.get("lines") or ():
            if isinstance(item, dict) and item.get("order_line") is not None and item.get("qty") is not None:
                pairs.append((item["order_line"], item["qty"]))
        if data.get("order_line") is not None and data.get("qty") is not None:
            pairs.append((data["order_line"], data["qty"]))
        for line, qty in pairs:
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


BUILTIN = (OverShipment(), OverShipmentOnUpdate(), OrderBelowShipped(), OverWorkorder(), OverProgress())

for _check in BUILTIN:
    register(_check)
