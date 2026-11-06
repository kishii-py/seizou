"""進捗の実績の表 (01 の 4 節)。

実績は指示書の工程を指し、1 つの工程に何回でも入れられる。予定を超えた実数もそのまま残す (現物優先)。
工程ごとの実績の合計は列に持たず、progress.services の recorded_qty で毎回出す。
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

    class Meta:
        verbose_name = "進捗の実績"
        verbose_name_plural = "進捗の実績"
        ordering = ["-recorded_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="progress_progress_qty_positive",
            ),
        ]

    def __str__(self):
        recorded_at = timezone.localtime(self.recorded_at).strftime("%Y-%m-%d %H:%M")
        return f"{self.workorder_step} 実績 {_qty_text(self.qty)} ({recorded_at})"
