"""progress の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して実績を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は実績の「人」(recorded_by) に書く。無ければ空。
- 中心の決まりに当たったら RuleViolation(code, message)。message は RULES.md の番号の文と、次にすること。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 予定より多い実績のポカよけは検査の層 (seizou.core.checks) が services の前に見る。
  ここでは止めず、予定を超えた実数もそのまま記録する (現物優先)。
- 工程ごとの実績の合計は recorded_qty で出す (検査の層と同じ関数)。
  書く前に、指示書の工程の行を select_for_update でロックする (01 の 10.1)。
- 指示書 (WorkOrder・WorkOrderStep) は書かない。
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from seizou.core.exceptions import RuleViolation

from .models import Progress

_R02_MESSAGE = "保存できません。数量と単価は 0 より大きい。0 より大きい数を入れてください。"

# 指示書の工程の表は、実績の外部キーの先として引く (workorders の models を import しない)。
_WorkOrderStep = Progress._meta.get_field("workorder_step").related_model


def _dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def record_progress(workorder_step, qty, recorded_at, user, *, note=""):
    """指示書の工程に実績を qty だけ記録する。"""
    qty = _dec(qty)
    with transaction.atomic():
        locked = _WorkOrderStep.objects.select_for_update().get(pk=workorder_step.pk)
        if not qty > 0:
            raise RuleViolation("R02", _R02_MESSAGE)
        return Progress.objects.create(
            workorder_step=locked,
            qty=qty,
            recorded_at=recorded_at,
            recorded_by=user if getattr(user, "pk", None) is not None else None,
            note=note,
        )


def recorded_qty(workorder_step):
    """指示書の工程に対する実績の合計 (列に持たない)。"""
    return Progress.objects.filter(workorder_step=workorder_step).aggregate(
        total=Coalesce(
            Sum("qty"),
            Value(Decimal("0")),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    )["total"]
