"""発注の表 (01 の 4 節)。外注管理の最小の形。

文字数の上限は固定 (識別子 35・備考 500・コード 10)。
金額は purchasing.services が数量 × 単価を取引先の小計の計算方法で丸めて書く (DB で計算しない)。
受入・検収・支給材は会期後。書くときは purchasing.services を通す。
"""

from django.db import models


class PurchaseOrder(models.Model):
    """発注。1 件に明細が複数。発注番号は core の連番が付ける。"""

    STATUS_DRAFT = "draft"
    STATUS_SENT = "sent"
    STATUS_ACCEPTED = "accepted"
    STATUS_DELIVERED = "delivered"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "作成"),
        (STATUS_SENT, "送った"),
        (STATUS_ACCEPTED, "相手が受けた"),
        (STATUS_DELIVERED, "納品された"),
        (STATUS_CANCELLED, "取消"),
    ]

    po_no = models.CharField("発注番号", max_length=35, unique=True)
    partner = models.ForeignKey(
        "masters.Partner",
        verbose_name="取引先",
        on_delete=models.PROTECT,
        related_name="purchase_orders",
    )
    order_date = models.DateField("発注日")
    status = models.CharField(
        "状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT
    )
    note = models.CharField("備考", max_length=500, blank=True)

    class Meta:
        verbose_name = "発注"
        verbose_name_plural = "発注"
        ordering = ["-order_date", "-id"]

    def __str__(self):
        return self.po_no


class PurchaseOrderLine(models.Model):
    """発注の明細。金額は purchasing.services が数量 × 単価を丸めて書く。"""

    purchase_order = models.ForeignKey(
        PurchaseOrder,
        verbose_name="発注",
        on_delete=models.CASCADE,
        related_name="lines",
    )
    line_no = models.PositiveIntegerField("行番号")
    item = models.ForeignKey(
        "masters.Item",
        verbose_name="部品",
        on_delete=models.PROTECT,
        related_name="purchase_order_lines",
    )
    qty = models.DecimalField("数量", max_digits=12, decimal_places=2)
    unit = models.CharField("単位", max_length=10, blank=True)
    unit_price = models.DecimalField("単価", max_digits=14, decimal_places=2)
    amount = models.DecimalField("金額", max_digits=14, decimal_places=2)
    due_date = models.DateField("納期", null=True, blank=True)
    note = models.CharField("備考", max_length=500, blank=True)

    class Meta:
        verbose_name = "発注の明細"
        verbose_name_plural = "発注の明細"
        ordering = ["purchase_order", "line_no"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="purchasing_purchaseorderline_qty_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gt=0),
                name="purchasing_purchaseorderline_unit_price_positive",
            ),
            models.UniqueConstraint(
                fields=["purchase_order", "line_no"],
                name="purchasing_purchaseorderline_line_no_unique",
            ),
        ]
