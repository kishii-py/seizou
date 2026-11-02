"""指示書の表 (01 の 4 節)。

指示書は受注の明細を指し、1 つの明細に何枚でも出せる。工程は指示書ごとに順を持つ。
指示書の数量の合計は列に持たず、workorders.services の issued_qty で毎回出す。
文字数の上限は固定 (識別子 35・備考 500・コード 10)。
書くときは workorders.services を通す。
"""

from django.core.exceptions import FieldDoesNotExist
from django.db import models


class WorkOrder(models.Model):
    """指示書。指示書番号は core の連番が付ける。"""

    STATUS_ISSUED = "issued"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_ISSUED, "発行"),
        (STATUS_CANCELLED, "取消"),
    ]

    wo_no = models.CharField("指示書番号", max_length=35, unique=True)
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
