"""workorders の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して指示書を書く。

- 引数の順は 対象 → 値 → user (01 の 10.1)。
- user は記録のために受ける。記録の列が無いので、今は受けるだけで書かない。
- 中心の決まりに当たったら RuleViolation(code, message)。message は RULES.md の番号の文と、次にすること。
- 決まりの番号の無い入力の誤り (工程が無い・知らない鍵) は日本語の ValueError。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 受注より多い指示書のポカよけは検査の層 (seizou.core.checks) が services の前に見る。
  ここでは止めず、受注の数を超える数量もそのまま保存する (現物優先)。
- 指示書の数量の合計は issued_qty で出す (検査の層と同じ関数)。
  書く前に、受注の明細の行を select_for_update でロックする (01 の 10.1)。
- 受注 (Order・OrderLine) は書かない。
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from seizou.core.exceptions import RuleViolation
from seizou.core.services import next_number

from .models import WorkOrder, WorkOrderStep

_R02_MESSAGE = "数量と単価は 0 より大きい。0 より大きい数を入れてください。"
_R05_RULE = "取消した受注には、出荷も指示書も作れない。"
_STEP_KEYS = frozenset({"process", "planned_qty"})

# 受注の明細の表は、指示書の外部キーの先として引く (orders の models を import しない)。
_OrderLine = WorkOrder._meta.get_field("order_line").related_model


def _dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _refuse_bad_steps(steps):
    for step in steps:
        unknown = sorted(set(step) - _STEP_KEYS)
        if unknown:
            raise ValueError(
                f"知らない項目 ({'・'.join(unknown)}) があります。"
                "指示書の工程には process と planned_qty だけを渡してください。"
            )
        missing = sorted(_STEP_KEYS - set(step))
        if missing:
            raise ValueError(
                f"工程の項目 ({'・'.join(missing)}) がありません。"
                "指示書の工程には process と planned_qty を渡してください。"
            )


def issue_workorder(order_line, qty, steps, user):
    """受注の明細に指示書を発行する。指示書番号は core の連番が付ける。

    steps の 1 つは {"process": 工程, "planned_qty": 予定の数}。並びの順に順を 1 から付ける。
    """
    qty = _dec(qty)
    with transaction.atomic():
        locked = _OrderLine.objects.select_for_update().get(pk=order_line.pk)
        if locked.order.status == "cancelled":
            raise RuleViolation(
                "R05", f"{_R05_RULE}取消していない受注の明細を選んでください。"
            )
        if not qty > 0:
            raise RuleViolation("R02", _R02_MESSAGE)
        if not steps:
            raise ValueError("工程がありません。工程を 1 つ以上選んでください。")
        _refuse_bad_steps(steps)
        planned = [_dec(step["planned_qty"]) for step in steps]
        if not all(value > 0 for value in planned):
            raise RuleViolation("R02", _R02_MESSAGE)
        workorder = WorkOrder.objects.create(
            wo_no=next_number("workorder"),
            order_line=locked,
            qty=qty,
            issued_on=timezone.localdate(),
        )
        for seq, (step, planned_qty) in enumerate(zip(steps, planned), start=1):
            WorkOrderStep.objects.create(
                workorder=workorder,
                process=step["process"],
                seq=seq,
                planned_qty=planned_qty,
            )
        return workorder


def issued_qty(order_line):
    """受注の明細に対する指示書の数量の合計 (取消の指示書は数えない。列に持たない)。"""
    return (
        WorkOrder.objects.filter(order_line=order_line)
        .exclude(status=WorkOrder.STATUS_CANCELLED)
        .aggregate(
            total=Coalesce(
                Sum("qty"),
                Value(Decimal("0")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )["total"]
    )
