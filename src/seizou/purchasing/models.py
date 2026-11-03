"""発注の表 (01 の 4 節)。協力企業管理の最小の形。

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
    STATUS_REJECTED = "rejected"
    STATUS_DELIVERED = "delivered"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "作成"),
        (STATUS_SENT, "送った"),
        (STATUS_ACCEPTED, "相手が受け入れた"),
        (STATUS_REJECTED, "断られた"),
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
    # 注文回答 (相手が承認か断りをした結果)。connect の受け口が purchasing.services の mark_accepted・mark_rejected で書く。
    # 日時はこちらに届いて当てた時刻 (相手の時計は使わない)。
    replied_at = models.DateTimeField("注文回答の日時", null=True, blank=True)
    reply_note = models.CharField("断りの理由", max_length=500, blank=True)
    # 当てた注文回答の番号。同じ番号が 2 回届いたら 2 回目は何もしない (R11 の考え。DB の一意の制約も)。
    reply_message_id = models.UUIDField("注文回答の番号", null=True, blank=True, unique=True)

    # 詳細と小窓に出さない列 (core/views.py の hidden_detail_fields が読む)。uuid は画面に出さない。
    detail_hidden_fields = ("reply_message_id",)

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
