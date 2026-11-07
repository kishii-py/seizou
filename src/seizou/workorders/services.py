"""workorders の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して指示書を書く。

- 引数の順は 対象 → 値 → user (01 の 10.1)。
- user は記録のために受け、変更の履歴 (core.services.record_history。決め HIST-1 の伝票の「指示書」) に書く。書く前に core.history.saved_state で前を取り、
  書いた後に同じ transaction.atomic の中で残す (止まって戻ったら履歴も残らない)。工程の行は指示書の詳細の「履歴」に出る (WorkOrderStep.history_owner)。
- 中心の決まりに当たったら RuleViolation(code, message)。message は RULES.md の番号の文と、次にすること。
- 決まりの番号の無い入力の誤り (工程が無い・知らない鍵) は日本語の ValueError。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 受注より多い指示書のポカよけは検査の層 (seizou.core.checks) が services の前に見る。
  ここでは止めず、受注の数を超える数量もそのまま保存する (現物優先)。
- 指示書の数量の合計は issued_qty で出す (検査の層と同じ関数)。
  書く前に、受注の明細の行を select_for_update でロックする (01 の 10.1)。
- 受注 (Order・OrderLine) は書かない。
- 指示書の取消は cancel_workorder。実績が 1 件でも入った指示書は取り消せない (実績は progress を import せず、工程の逆の関係で数える)。
  取り消せない理由の文は cancel_refusal の 1 か所 (取消の確かめのカードも同じ文を出す)。
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import FieldDoesNotExist
from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from seizou.core import history
from seizou.core.exceptions import RuleViolation
from seizou.core.services import next_number, record_history

from .models import WorkOrder, WorkOrderStep

_R02_MESSAGE = "発行できません。数量と単価は 0 より大きい。0 より大きい数を入れてください。"
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
                f"発行できません。知らない項目 ({'・'.join(unknown)}) があります。"
                "指示書の工程には process と planned_qty だけを渡してください。"
            )
        missing = sorted(_STEP_KEYS - set(step))
        if missing:
            raise ValueError(
                f"発行できません。工程の項目 ({'・'.join(missing)}) がありません。"
                "指示書の工程には process と planned_qty を渡してください。"
            )


def issue_workorder(order_line, qty, steps, user):
    """受注の明細に指示書を発行する。指示書番号は core の連番が付ける。

    steps の 1 つは {"process": 工程, "planned_qty": 予定の数}。並びの順に順を 1 から付ける。
    訳: 取消した受注に指示書を作らず (R05)、0 以下の数量と予定の数で指示書を作らないため (R02)。出所 = RULES.md R05・R02。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    qty = _dec(qty)
    with transaction.atomic():
        locked = _OrderLine.objects.select_for_update().get(pk=order_line.pk)
        if locked.order.status == "cancelled":
            raise RuleViolation(
                "R05", f"発行できません。{_R05_RULE}取消していない受注の明細を選んでください。"
            )
        if not qty > 0:
            raise RuleViolation("R02", _R02_MESSAGE)
        if not steps:
            raise ValueError("発行できません。工程がありません。工程を 1 つ以上選んでください。")
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
        record_history(workorder, user)
        for seq, (step, planned_qty) in enumerate(zip(steps, planned), start=1):
            created = WorkOrderStep.objects.create(
                workorder=workorder,
                process=step["process"],
                seq=seq,
                planned_qty=planned_qty,
            )
            record_history(created, user)
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


def plan_step(step, start, end, user):
    """指示書の工程の予定に開始日と終了日を置く (生産計画の盤。02 の 4 節)。

    start と end は日付。両方 None で未計画に戻す。片方だけ None と start > end は ValueError。
    取消した受注の指示書の工程は R05 で止める。取消の指示書は止めない (盤に出さないだけ)。
    書くのは planned_start・planned_end の 2 列だけ (数は書かない)。開始日と終了日の前と後は、変更の履歴に user で残す (変わった時だけ)。
    訳: 取消した受注の指示書の工程に予定を置かないため。出所 = RULES.md R05。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    with transaction.atomic():
        locked = WorkOrderStep.objects.select_for_update().get(pk=step.pk)
        if locked.workorder.order_line.order.status == "cancelled":
            raise RuleViolation(
                "R05", f"保存できません。{_R05_RULE}取消していない受注の指示書を選んでください。"
            )
        if (start is None) != (end is None):
            raise ValueError("保存できません。開始日か終了日の片方だけが入っています。開始日と終了日は、両方を入れるか両方を空にしてください。")
        if start is not None and start > end:
            raise ValueError(
                "保存できません。開始日が終了日より後です。開始日を終了日と同じ日か前の日にしてください。"
            )
        before = history.saved_state(locked)
        locked.planned_start = start
        locked.planned_end = end
        locked.save(update_fields=["planned_start", "planned_end"])
        record_history(locked, user, before=before)
        return locked


def _progress_count(steps):
    """工程に入った実績の件数 (読むだけ)。progress の部品が無ければ 0。

    実績の表は工程の逆の関係 progress_records の先として引く (progress を import しない)。
    """
    try:
        records = WorkOrderStep._meta.get_field("progress_records").related_model
    except FieldDoesNotExist:
        return 0
    return records._default_manager.filter(workorder_step__in=steps).count()


def cancel_refusal(workorder) -> str:
    """指示書を取り消せない理由の文 (読むだけ)。取り消せるなら空の字。

    指示書の工程に入った実績の件数 (_progress_count) で文を作る。cancel_workorder と取消の確かめのカード
    (workorders_tags の workorder_cancel_refusal) が同じ文を使う。指示書の状態は見ない。
    訳: 実績の入った指示書を取り消さないため (取り消すと、生産計画と進捗の盤から実績ごと外れる)。出所 = AI の案 (docs/nights/20261102/N26.md)。
    変えてよい条件 = Issue「決まりの提案」でこの訳に対して議論してから。
    """
    count = _progress_count(workorder.steps.all())
    if not count:
        return ""
    return f"取り消せません。指示書 {workorder.wo_no} には実績が {count} 件入っています。"


def cancel_workorder(workorder, user, *, reason=""):
    """実績の無い指示書を取消にする。reason は「取消の理由」の列に残す。

    取消の指示書は issued_qty に数えず、生産計画と進捗の盤にも出ない。取消した受注の指示書も取り消せる。
    実績が入っていれば、何も変えずに ValueError (文は cancel_refusal の 1 か所)。
    """
    with transaction.atomic():
        locked = WorkOrder.objects.select_for_update().get(pk=workorder.pk)
        list(WorkOrderStep.objects.select_for_update().filter(workorder=locked).order_by("pk"))
        if locked.status == WorkOrder.STATUS_CANCELLED:
            raise ValueError("取り消せません。この指示書は取り消し済みです。指示書の詳細を開き直してください。")
        refusal = cancel_refusal(locked)
        if refusal:
            raise ValueError(refusal)
        limit = WorkOrder._meta.get_field("cancel_reason").max_length
        if len(reason) > limit:
            raise ValueError(f"取り消せません。取消の理由が {limit} 字を超えます。理由を短くしてください。")
        before = history.saved_state(locked)
        locked.status = WorkOrder.STATUS_CANCELLED
        locked.cancel_reason = reason
        locked.save(update_fields=["status", "cancel_reason"])
        record_history(locked, user, before=before)
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
        locked.saved_message = "取り消しました。"
        return locked

