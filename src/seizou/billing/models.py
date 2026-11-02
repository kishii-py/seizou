"""請求書の表 (01 の 4 節)。

請求書は得意先ごとに、出荷済みの出荷 (発行の納品書に入っている出荷) をまとめた物。
明細は出荷ごとに 1 行で、数量・単価・金額の写しを持つ (出した請求書の金額は、後で受注の単価を直しても変わらない)。
支払期日も作ったときに計算して残す (後で取引先の支払の欄を直しても、出した請求書の期日は変わらない)。
文字数の上限は固定 (識別子 35・コード 10)。
書くときは billing.services を通す。
"""

from django.db import models


class Invoice(models.Model):
    """請求書。請求書番号は core の連番が付ける。"""

    STATUS_ISSUED = "issued"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_ISSUED, "発行"),
        (STATUS_CANCELLED, "取消"),
    ]

    invoice_no = models.CharField("請求書番号", max_length=35, unique=True)
    customer = models.ForeignKey(
        "masters.Partner",
        verbose_name="得意先",
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    issued_on = models.DateField("発行日")
    period_from = models.DateField("期間の初め")
    period_to = models.DateField("期間の終わり")
    due_on = models.DateField("支払期日", null=True, blank=True)
    subtotal = models.DecimalField("小計", max_digits=14, decimal_places=2)
    tax = models.DecimalField("消費税", max_digits=14, decimal_places=2)
    total = models.DecimalField("合計", max_digits=14, decimal_places=2)
    status = models.CharField(
        "状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_ISSUED
    )

    class Meta:
        verbose_name = "請求書"
        verbose_name_plural = "請求書"
        ordering = ["-issued_on", "-id"]

    def __str__(self):
        return self.invoice_no


class InvoiceLine(models.Model):
    """請求書の明細。出荷 1 つに 1 行。数量・単価・金額は作ったときの写し。"""

    invoice = models.ForeignKey(
        Invoice,
        verbose_name="請求書",
        on_delete=models.CASCADE,
        related_name="lines",
    )
    shipment = models.ForeignKey(
        "shipping.Shipment",
        verbose_name="出荷",
        on_delete=models.PROTECT,
        related_name="invoice_lines",
    )
    qty = models.DecimalField("数量", max_digits=12, decimal_places=2)
    unit_price = models.DecimalField("単価", max_digits=14, decimal_places=2)
    amount = models.DecimalField("金額", max_digits=14, decimal_places=2)

    class Meta:
        verbose_name = "請求書の明細"
        verbose_name_plural = "請求書の明細"
        ordering = ["invoice", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="billing_invoiceline_qty_positive",
            ),
            # 1 つの出荷を発行の請求書 2 つに入れない約束は DB の制約にしない
            # (取り消した請求書の出荷は、また請求できるため)。services が行をロックして見る。
            models.UniqueConstraint(
                fields=["invoice", "shipment"],
                name="billing_invoiceline_invoice_shipment_unique",
            ),
        ]

    def __str__(self):
        return f"{self.invoice.invoice_no} {self.shipment.shipment_no}"
