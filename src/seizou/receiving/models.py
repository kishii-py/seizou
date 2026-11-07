"""入荷の表 (北極星 7 節の末尾。決め 11-06 18:3x)。

入荷 (Receiving) は、得意先から受け入れた現物の記録。出所は無償支給・有償支給・預かりの 3 つで、注文とは切り離す。
入荷の先は引当 (Allocation) に持つ。引当は入荷と受注の明細を数量で結ぶ (1 つの入荷を複数の明細に、1 つの明細に複数の入荷を)。
金額の欄は持たない。在庫の動き (stock) とはつながない (次の版で)。
ほかの部品の表に列を足さず、ほかの部品の model を import しない (部品・取引先・受注の明細は外部キーの字で指す)。
書くときは receiving.services を通す。
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

SOURCE_FREE = "free"
SOURCE_PAID = "paid"
SOURCE_CONSIGNED = "consigned"
#: 出所 (値, 画面の字)
SOURCE_CHOICES = (
    (SOURCE_FREE, "無償支給"),
    (SOURCE_PAID, "有償支給"),
    (SOURCE_CONSIGNED, "預かり"),
)


def _qty_text(value) -> str:
    """数量の字。末尾の 0 を落とす (10.00 → 10、2.50 → 2.5)。stock.models の _qty_text を写した。"""
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(dec.normalize(), "f") if dec != dec.to_integral_value() else str(dec.quantize(Decimal(1)))


class Receiving(models.Model):
    """入荷。1 行 = 得意先から 1 回に受け入れた 1 つの部品の現物 (支給材かワーク)。"""

    item = models.ForeignKey(
        "masters.Item",
        verbose_name="部品",
        on_delete=models.PROTECT,
        related_name="receivings",
    )
    partner = models.ForeignKey(
        "masters.Partner",
        verbose_name="支給元",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="receivings",
    )
    source = models.CharField("出所", max_length=10, choices=SOURCE_CHOICES)
    qty = models.DecimalField("数量", max_digits=12, decimal_places=2)
    received_on = models.DateField("入荷日")
    lot = models.CharField("ロット", max_length=50, blank=True)
    note = models.CharField("備考", max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="入れた人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="receivings",
    )
    created_at = models.DateTimeField("入れた日時", auto_now_add=True)

    class Meta:
        verbose_name = "入荷"
        verbose_name_plural = "入荷"
        ordering = ["-received_on", "-pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(qty__gt=0), name="receiving_receiving_qty_positive"),
        ]

    def __str__(self):
        return f"{self.received_on:%Y-%m-%d} {self.get_source_display()} {self.item.part_no} {_qty_text(self.qty)}"


class Allocation(models.Model):
    """引当。1 行 = 1 つの入荷のうち、1 つの受注の明細に充てた数。"""

    receiving = models.ForeignKey(
        Receiving,
        verbose_name="入荷",
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    order_line = models.ForeignKey(
        "orders.OrderLine",
        verbose_name="受注の明細",
        on_delete=models.PROTECT,
        related_name="receiving_allocations",
    )
    qty = models.DecimalField("数量", max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "引当"
        verbose_name_plural = "引当"
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(qty__gt=0), name="receiving_allocation_qty_positive"),
            models.UniqueConstraint(fields=["receiving", "order_line"], name="receiving_allocation_unique"),
        ]

    def __str__(self):
        line = self.order_line
        return f"{line.order.order_no}-{line.line_no} {line.item.part_no} {line.item.name} 引当 {_qty_text(self.qty)}"
