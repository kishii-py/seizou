"""指示書の表 (01 の 4 節)。

指示書は受注の明細を指し、1 つの明細に何枚でも出せる。工程は指示書ごとに順を持つ。
指示書の数量の合計は列に持たず、workorders.services の issued_qty で毎回出す。
文字数の上限は固定 (識別子 35・備考 500・コード 10)。
書くときは workorders.services を通す。
指示書の進みの状態 (未着手・着手済・完了・取消) は列に持たず、工程の実績から WorkOrder.progress_state で毎回出す
(初見テスト R5 の候補 C27。本人の決め 11-07 00:36 = WO-1 b)。
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

#: 実績の合計の型 (WorkOrderStep.planned_qty と同じ桁)
_QTY_FIELD = models.DecimalField(max_digits=12, decimal_places=2)
_ZERO = Decimal("0")


def _dec(value) -> Decimal:
    """集計の値を Decimal で受ける (SQLite は集計の値を桁なしで返すことがある)。None は 0。"""
    if value is None:
        return _ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


class WorkOrder(models.Model):
    """指示書。指示書番号は core の連番が付ける。"""

    STATUS_ISSUED = "issued"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_ISSUED, "発行"),
        (STATUS_CANCELLED, "取消"),
    ]

    # 進みの状態 (表の列でなく、工程の実績から出す。progress_state)。字は C27-1 = a の 4 つ。
    PROGRESS_NOT_STARTED = "not_started"
    PROGRESS_STARTED = "started"
    PROGRESS_DONE = "done"
    PROGRESS_CANCELLED = "cancelled"
    PROGRESS_LABELS = {
        PROGRESS_NOT_STARTED: "未着手",
        PROGRESS_STARTED: "着手済",
        PROGRESS_DONE: "完了",
        PROGRESS_CANCELLED: "取消",
    }

    wo_no =models.CharField("指示書番号", max_length=35, unique=True)
    order_line = models.ForeignKey(
        "orders.OrderLine",
        verbose_name="受注の明細",
        on_delete=models.PROTECT,
        related_name="workorders",
    )
    qty = models.DecimalField("数量", max_digits=12, decimal_places=2)
    issued_on = models.DateField("発行日")
    status = models.CharField(
        "状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_ISSUED
    )
    # 書くのは services.cancel_workorder だけ。
    cancel_reason = models.CharField("取消の理由", max_length=500, blank=True)
    # 製造部が受けた (着手した) 印。空 = 未着手。書くのは services.accept_workorder だけ。
    accepted_at = models.DateTimeField("着手日時", null=True, blank=True)
    # 受けた人。書くのは services.accept_workorder だけ (進捗の実績の「人」と同じ形)。
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="着手者",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="accepted_workorders",
    )

    class Meta:
        verbose_name = "指示書"
        verbose_name_plural = "指示書"
        ordering = ["-issued_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="workorders_workorder_qty_positive",
            ),
        ]

    def __str__(self):
        return self.wo_no

    def accepted_label(self) -> str:
        """一覧の「受けた」の列の字 (表示だけ)。未着手は「未」、受けた物は日時 (YYYY-MM-DD HH:MM)。"""
        if self.accepted_at is None:
            return "未"
        return timezone.localtime(self.accepted_at).strftime("%Y-%m-%d %H:%M")

    @classmethod
    def progress_state_of(cls, status, steps, totals) -> str:
        """指示書の進みの状態の値 (PROGRESS_LABELS の鍵)。決め方はここの 1 か所 (詳細・一覧・小窓・生産管理ボード・段の帯・生産計画の盤)。

        status は指示書の状態の値、steps は指示書の工程の並び (順は問わない)、totals は工程の pk → 実績の合計
        (辞書に無い工程は 0。進捗の部品が無ければ空の辞書)。上から順に最初に当たった物:
        取消 = 指示書が取消 / 完了 = 最後の工程 (順が最大) の実績の合計が、その工程の予定の数以上
        (生産計画の盤の棒の「完了」= plans.bar_state と同じ) / 着手済 = どれかの工程の実績の合計が 0 より大きい /
        未着手 = ほか (工程の無い指示書と、進捗の部品が無い置き場を含む)。着手の印は読まない。
        訳: 状態を記録せずに実績だけから出し、記録した状態と実績が食い違わないようにするため。出所 = 本人の決め 11-07 00:36 (WO-1 = b)「b / 開始の進捗が必要な場合はまた別途だね」と、C27-4 の答え「そもそもstatusは進捗から計算すればいいのでは？」。字の 4 つは C27-1 = a (11-07 00:2x)。
        変えてよい条件 = 完了の数え方を良品と不良の合計に替える依頼 (C29-2 = b) がこの関数を直すとき。ほかは本人が決め方を変えたとき。
        """
        if status == cls.STATUS_CANCELLED:
            return cls.PROGRESS_CANCELLED
        steps = list(steps)
        if steps:
            last = max(steps, key=lambda step: (step.seq, step.pk))
            if totals.get(last.pk, _ZERO) >= last.planned_qty:
                return cls.PROGRESS_DONE
        if any(totals.get(step.pk, _ZERO) > 0 for step in steps):
            return cls.PROGRESS_STARTED
        return cls.PROGRESS_NOT_STARTED

    def _steps_and_totals(self):
        """(工程の並び, 工程の pk → 実績の合計)。読むだけ。

        工程を実績の合計 (recorded_total) 付きで prefetch してあれば (指示書の一覧と生産管理ボード)、問い合わせをしない。
        無ければ工程を実績の合計付きで 1 回の問い合わせで読む (progress を import せず、工程の逆の関係で集計する)。
        進捗の部品が無い置き場では、合計は空の辞書。
        """
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("steps")
        if prefetched is not None and all(hasattr(step, "recorded_total") for step in prefetched):
            steps = list(prefetched)
            return steps, {step.pk: _dec(step.recorded_total) for step in steps}
        try:
            WorkOrderStep._meta.get_field("progress_records")
        except FieldDoesNotExist:
            return list(self.steps.all()), {}
        steps = list(
            self.steps.order_by().annotate(
                recorded_total=Coalesce(Sum("progress_records__qty"), Value(_ZERO), output_field=_QTY_FIELD)
            )
        )
        return steps, {step.pk: _dec(step.recorded_total) for step in steps}

    def progress_state(self) -> str:
        """進みの状態の値 (未着手・着手済・完了・取消の鍵)。決め方は progress_state_of。取消は工程を読まない。"""
        if self.status == self.STATUS_CANCELLED:
            return self.PROGRESS_CANCELLED
        steps, totals = self._steps_and_totals()
        return self.progress_state_of(self.status, steps, totals)

    def progress_state_label(self) -> str:
        """進みの状態の字 (一覧の「状態」の列と CSV。印は workorders_tags の workorder_badge)。"""
        return self.PROGRESS_LABELS[self.progress_state()]

    @property
    def progress_records(self):
        """この指示書の工程に入った進捗の実績 (読むだけ。詳細の「進捗」のタブが使う)。

        progress を import せず、工程の逆の関係 (progress の外部キー) の先の表として引く。
        progress の部品が無いときは AttributeError (土台の hasattr が偽になり、タブを出さない)。
        """
        try:
            records = WorkOrderStep._meta.get_field("progress_records").related_model
        except FieldDoesNotExist:
            raise AttributeError("progress_records") from None
        return records._default_manager.filter(workorder_step__workorder=self)


class WorkOrderStep(models.Model):
    """指示書の工程。順は 1 から。予定の数は検査の層が実績の合計と比べる。"""

    workorder = models.ForeignKey(
        WorkOrder,
        verbose_name="指示書",
        on_delete=models.CASCADE,
        related_name="steps",
    )
    process = models.ForeignKey(
        "masters.Process",
        verbose_name="工程",
        on_delete=models.PROTECT,
        related_name="workorder_steps",
    )
    seq = models.PositiveIntegerField("順")
    planned_qty = models.DecimalField("予定の数", max_digits=12, decimal_places=2)
    # 生産計画の盤で置く日。空 = 未計画。書くのは services.plan_step だけ。
    planned_start = models.DateField("開始日", null=True, blank=True)
    planned_end = models.DateField("終了日", null=True, blank=True)

    class Meta:
        verbose_name = "指示書の工程"
        verbose_name_plural = "指示書の工程"
        ordering = ["workorder", "seq"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(planned_qty__gt=0),
                name="workorders_workorderstep_planned_qty_positive",
            ),
            models.UniqueConstraint(
                fields=["workorder", "seq"],
                name="workorders_workorderstep_seq_unique",
            ),
        ]

    def __str__(self):
        return f"{self.workorder.wo_no} {self.seq} {self.process.name}"
