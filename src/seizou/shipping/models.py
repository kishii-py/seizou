"""出荷の表 (01 の 4 節)。

出荷は受注の明細を指し、1 つの明細に何回でも出荷できる (分納)。
納品書は得意先ごとに出荷をまとめた物で、数量と金額の写しを持たない。
納品書の明細は、その納品書に結ばれた出荷そのもの (R08 の「納品書の数量は出荷の数量と一致する」)。
文字数の上限は固定 (識別子 35・備考 500・コード 10)。
書くときは shipping.services を通す。
"""

from django.db import models


class DeliveryNote(models.Model):
    """納品書。納品書番号は core の連番が付ける。"""

    STATUS_ISSUED = "issued"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_ISSUED, "発行"),
        (STATUS_CANCELLED, "取消"),
    ]

    note_no = models.CharField("納品書番号", max_length=35, unique=True)
    customer = models.ForeignKey(
        "masters.Partner",
        verbose_name="得意先",
        on_delete=models.PROTECT,
        related_name="delivery_notes",
    )
    issued_on = models.DateField("発行日")
    status = models.CharField(
        "状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_ISSUED
    )

    class Meta:
        verbose_name = "納品書"
        verbose_name_plural = "納品書"
        ordering = ["-issued_on", "-id"]

    def __str__(self):
        return self.note_no


class Shipment(models.Model):
    """出荷。出荷番号は core の連番が付ける。出荷済みの数は orders.services の shipped_qty が集計する。"""

    shipment_no = models.CharField("出荷番号", max_length=35, unique=True)
    order_line = models.ForeignKey(
        "orders.OrderLine",
        verbose_name="受注の明細",
        on_delete=models.PROTECT,
        related_name="shipments",
    )
    qty = models.DecimalField("数量", max_digits=12, decimal_places=2)
    shipped_at = models.DateField("出荷日")
    delivery_note = models.ForeignKey(
        DeliveryNote,
        verbose_name="納品書",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="shipments",
    )
    note = models.CharField("備考", max_length=500, blank=True)

    class Meta:
        verbose_name = "出荷"
        verbose_name_plural = "出荷"
        ordering = ["-shipped_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="shipping_shipment_qty_positive",
            ),
        ]

    def __str__(self):
        return self.shipment_no
