"""受注の表 (01 の 4 節)。

項目の名前は中小企業共通 EDI の注文の必須の項目を元にした (得意先の注文番号・受注日・行番号・単価・数量・単位・納期)。
文字数の上限は固定 (識別子 35・備考 500・コード 10)。
出荷済みの数と残の数は列に持たず、orders.services の shipped_qty・remaining_qty で毎回出す。
書くときは orders.services を通す。
"""

from django.db import models


class Order(models.Model):
    """受注。1 件に明細が複数。受注番号は core の連番が付ける。"""

    STATUS_OPEN = "open"
    STATUS_PARTIAL = "partial"
    STATUS_SHIPPED = "shipped"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_OPEN, "受注"),
        (STATUS_PARTIAL, "一部出荷"),
        (STATUS_SHIPPED, "出荷済"),
        (STATUS_CANCELLED, "取消"),
    ]

    SOURCE_MANUAL = "manual"
    SOURCE_INTAKE = "intake"
    SOURCE_INBOX = "inbox"
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "手入力"),
        (SOURCE_INTAKE, "読み取り"),
        (SOURCE_INBOX, "受信箱"),
    ]

    order_no = models.CharField("受注番号", max_length=35, unique=True)
    customer = models.ForeignKey(
        "masters.Partner",
        verbose_name="得意先",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    order_date = models.DateField("受注日")
    customer_order_no = models.CharField("得意先の注文番号", max_length=35, blank=True)
    status = models.CharField(
        "状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN
    )
    source = models.CharField(
        "入口", max_length=10, choices=SOURCE_CHOICES, default=SOURCE_MANUAL
    )
    note = models.CharField("備考", max_length=500, blank=True)

    class Meta:
        verbose_name = "受注"
        verbose_name_plural = "受注"
        ordering = ["-order_date", "-id"]


class OrderLine(models.Model):
    """受注の明細。金額は orders.services が数量 × 単価を丸めて書く。"""

    order = models.ForeignKey(
        Order,
        verbose_name="受注",
        on_delete=models.CASCADE,
        related_name="lines",
    )
    line_no = models.PositiveIntegerField("行番号")
    item = models.ForeignKey(
        "masters.Item",
        verbose_name="品目",
        on_delete=models.PROTECT,
        related_name="order_lines",
    )
    customer_part_no = models.CharField("得意先の品番", max_length=35, blank=True)
    qty = models.DecimalField("数量", max_digits=12, decimal_places=2)
    unit = models.CharField("単位", max_length=10, blank=True)
    unit_price = models.DecimalField("単価", max_digits=14, decimal_places=2)
    amount = models.DecimalField("金額", max_digits=14, decimal_places=2)
    due_date = models.DateField("納期", null=True, blank=True)
    note = models.CharField("備考", max_length=500, blank=True)

    class Meta:
        verbose_name = "受注の明細"
        verbose_name_plural = "受注の明細"
        ordering = ["order", "line_no"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="orders_orderline_qty_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gt=0),
                name="orders_orderline_unit_price_positive",
            ),
            models.UniqueConstraint(
                fields=["order", "line_no"],
                name="orders_orderline_line_no_unique",
            ),
        ]
