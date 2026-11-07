"""進捗の実績の表 (01 の 4 節)。

実績は指示書の工程を指し、1 つの工程に何回でも入れられる。予定を超えた実数もそのまま残す (現物優先)。
実績の数 (qty) は良品の数。不良の数は defect_qty に分けて持つ (C29-2)。工程の完了は、印 (completes_step) の付いた実績があるかで決める (PROG-1)。
工程ごとの実績の合計・不良の数の合計・完了の印の有る無しは列に持たず、progress.services の recorded_qty・recorded_defect_qty・step_completed で毎回出す。
文字数の上限は固定 (識別子 35・備考 500・コード 10)。
書くときは progress.services を通す。
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


def _qty_text(value) -> str:
    """数量の字。末尾の 0 を落とす (500.00 → 500、2.50 → 2.5)。stock・purchasing の models と同じ関数 (ほかの部品の model を import しない)。"""
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(dec.normalize(), "f") if dec != dec.to_integral_value() else str(dec.quantize(Decimal(1)))


class Progress(models.Model):
    """進捗の実績。人は services が user を書く (無ければ空)。"""

    workorder_step = models.ForeignKey(
        "workorders.WorkOrderStep",
        verbose_name="指示書の工程",
        on_delete=models.PROTECT,
        related_name="progress_records",
    )
    qty = models.DecimalField("実績の数", max_digits=12, decimal_places=2)
    # 不良の数 (C29-2 = b)。実績の数 (qty) は良品の数 (C29-1) なので、不良は分けて持つ。
    defect_qty = models.DecimalField("不良の数", max_digits=12, decimal_places=2, default=Decimal("0"))
    # 工程の完了の印 (PROG-1)。工程の完了は数でなく、この印の付いた実績があるかで決める。初めは付けない (PROG-1b = a)。
    completes_step = models.BooleanField("工程の完了", default=False)
    recorded_at = models.DateTimeField("日時")
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="人",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="progress_records",
    )
    note = models.CharField("備考", max_length=500, blank=True)

    #: 変更の履歴は指示書の詳細の「履歴」に出す (core.history が指示書の工程 → 指示書と辿る。決め HIST-1 の伝票の「実績」・PROG-1d・PROG-1e)。
    #: 実績は指示書の詳細の「進捗」のタブに出る物で、誰がいつ直したか・消したかも同じ詳細で追えるようにするため
    history_owner = "workorder_step.workorder"

    class Meta:
        verbose_name = "進捗の実績"
        verbose_name_plural = "進捗の実績"
        ordering = ["-recorded_at", "-id"]
        # R02 の実績の形「良品の数と不良の数はどちらも 0 以上で、合わせて 0 より大きい」(C103-Q2 = a)。form と services を通らずに表へ書いても止める。
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gte=0),
                name="progress_progress_qty_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(defect_qty__gte=0),
                name="progress_progress_defect_qty_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(qty__gt=0) | models.Q(defect_qty__gt=0),
                name="progress_progress_qty_or_defect_positive",
            ),
        ]

    def __str__(self):
        recorded_at = timezone.localtime(self.recorded_at).strftime("%Y-%m-%d %H:%M")
        return f"{self.workorder_step} 実績 {_qty_text(self.qty)} ({recorded_at})"
