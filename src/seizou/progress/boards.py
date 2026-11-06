"""進捗の盤 (02 の 2 節の進捗の一覧) を作る、読むだけの関数。表に書かない (書くのは services.record_progress)。

盤の template tag (progress_board) と、盤の自動の更新の部分 (progress/_rows.html) の両方がここを呼ぶ。

- 盤の行は指示書の工程 1 つ。予定の数・実績の合計・残 (予定の数 − 実績の合計) を並べる。
- 盤に出すのは、指示書が取消でなく、受注も取消でない工程だけ。残が 0 の工程も出す。
  残が 0 以下の工程は done を真にし、盤は工程の字の右に「完了」の札を出す (現品票の読み取りの着地と同じ読み方)。
- 実績の合計は列に持たず annotate で出す (services.recorded_qty と同じ式)。残は Python で引く。
- 自動の更新の印 (after) は実績の pk。実績を直す・消す関数は無いので、after より大きい pk の実績が
  「前に見た後に入った実績」になる。
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import DecimalField, Max, Sum, Value
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils.http import urlencode

from .models import Progress

# 指示書の工程の表は、実績の外部キーの先として引く (workorders の models を import しない)。
_WorkOrderStep = Progress._meta.get_field("workorder_step").related_model

# 取消の状態の値 (指示書も受注も同じ字。orders の model を引かないので字で書く)。
CANCELLED = "cancelled"
# 実績の合計の小数の桁 (Progress.qty と同じ 2 桁)。
TOTAL_PLACES = Decimal("0.01")


def active_steps():
    """盤に出す指示書の工程 (指示書が取消でなく、受注も取消でない物)。発行日の新しい順 → 指示書番号 → 順。"""
    return (
        _WorkOrderStep.objects.exclude(workorder__status=CANCELLED)
        .exclude(workorder__order_line__order__status=CANCELLED)
        .select_related("workorder__order_line__item", "process")
        .order_by("-workorder__issued_on", "workorder__wo_no", "seq")
    )


def with_totals(steps):
    """工程ごとの実績の合計 (recorded_total) を足す。"""
    return steps.annotate(
        recorded_total=Coalesce(
            Sum("progress_records__qty"),
            Value(Decimal("0")),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    )


def parse_process(raw):
    """pill の工程の pk。0 より大きい整数の字なら int、ほかは None (= すべて)。"""
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def parse_after(raw):
    """自動の更新の印。0 以上の整数の字なら int、ほかは None。"""
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def newest_pk():
    """一番新しい実績の pk。実績が無ければ 0。"""
    return Progress.objects.aggregate(newest=Max("pk"))["newest"] or 0


def _pills():
    """先頭が「すべて」(pk は None)、続けて盤に出る予定のある工程 (並び順 → 工程コードの順)。"""
    base = reverse("progress:progress_list")
    # order_by で active_steps の並びを外す (外さないと並びの列が distinct に入り、工程が重なる)。
    processes = (
        active_steps()
        .order_by("process__sort_order", "process__code")
        .values("process_id", "process__name", "process__sort_order", "process__code")
        .distinct()
    )
    pills = [{"pk": None, "name": "すべて", "url": base}]
    for process in processes:
        pills.append(
            {
                "pk": process["process_id"],
                "name": process["process__name"],
                "url": f"{base}?{urlencode({'process': process['process_id']})}",
            }
        )
    return pills


def _row(step):
    # SQLite は集計の値を小数の桁なしで返す (7.00 が 7)。表の列と同じ小数 2 桁にそろえて、画面の桁を予定の数と合わせる。
    recorded = step.recorded_total.quantize(TOTAL_PLACES)
    remaining = step.planned_qty - recorded
    return {
        "step": step.pk,
        "workorder": step.workorder.pk,
        "wo_no": step.workorder.wo_no,
        "item": step.workorder.order_line.item,
        "seq": step.seq,
        "process_name": step.process.name,
        "planned": step.planned_qty,
        "recorded": recorded,
        "remaining": remaining,
        "over": remaining < 0,
        "done": remaining <= 0,
    }


def build_board(process_pk, after=None):
    """盤の中身の辞書。

    process_pk は pill で選んだ工程の pk (parse_process の値)。盤に出る予定の無い工程は None (= すべて) と同じ。
    after が None でなければ、行は「after より大きい pk の実績を持つ工程」だけ (変わった行)。
    鍵: process・pills・rows・count・newest・poll_query。
    """
    # 一番新しい pk を先に読む。行を読む間に入った実績は次の取りに行きでもう 1 度返る (取りこぼさない)。
    newest = newest_pk()
    pills = _pills()
    process = process_pk if any(pill["pk"] == process_pk for pill in pills[1:]) else None
    for pill in pills:
        pill["current"] = pill["pk"] == process

    steps = active_steps()
    if process is not None:
        steps = steps.filter(process_id=process)
    if after is not None:
        # 変わった工程の pk を先に取り、合計は全部の実績で出す (先に実績で絞ると合計が新しい分だけになる)。
        changed = Progress.objects.filter(pk__gt=after).values_list("workorder_step_id", flat=True)
        steps = steps.filter(pk__in=changed)
    rows = [_row(step) for step in with_totals(steps)]

    params = {"process": process} if process is not None else {}
    params["after"] = newest
    return {
        "process": process,
        "pills": pills,
        "rows": rows,
        "count": len(rows),
        "newest": newest,
        "poll_query": f"?{urlencode(params)}",
    }
