"""請求書の表 (01 の 4 節)。

請求書は得意先ごとに、出荷済みの出荷 (発行の納品書に入っている出荷) をまとめた物。
明細は出荷ごとに 1 行で、数量・単価・金額の写しを持つ (出した請求書の金額は、後で受注の単価を直しても変わらない)。
支払期日も作ったときに計算して残す (後で取引先の支払の欄を直しても、出した請求書の期日は変わらない)。
文字数の上限は固定 (識別子 35・コード 10)。
書くときは billing.services を通す。
入金 (Payment) は別の表。残と入金の状態は Invoice の method で出す (列に持たない)。
"""

from decimal import Decimal

from django.conf import settings
from django.db import models


class Invoice(models.Model):
    """請求書。請求書番号は core の連番が付ける。"""

    STATUS_ISSUED = "issued"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_ISSUED, "発行"),
        (STATUS_CANCELLED, "取消"),
    ]

    PAYMENT_UNPAID = "unpaid"
    PAYMENT_PARTIAL = "partial"
    PAYMENT_PAID = "paid"
    PAYMENT_LABELS = {PAYMENT_UNPAID: "未入金", PAYMENT_PARTIAL: "一部入金", PAYMENT_PAID: "入金済み"}

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

    def paid_total(self) -> Decimal:
        """取消でない入金の金額の合計 (Python で足す。self.payments.all() を絞るので、一覧の prefetch_related が効く)。"""
        return sum((row.amount for row in self.payments.all() if row.status == Payment.STATUS_RECORDED), Decimal("0"))

    def balance(self) -> Decimal:
        """残 = 合計 − 入金済み。多い入金ならマイナス (止めない)。"""
        return self.total - self.paid_total()

    def payment_status(self) -> str:
        """入金の状態。入金済みが 0 なら未入金、残が 0 より大きければ一部入金、ほかは入金済み。請求書の状態 (発行 / 取消) は見ない。"""
        if self.paid_total() == 0:
            return self.PAYMENT_UNPAID
        if self.balance() > 0:
            return self.PAYMENT_PARTIAL
        return self.PAYMENT_PAID

    def payment_status_label(self) -> str:
        """入金の状態の字 (一覧の「入金」の列と、詳細の「入金」の枠の印)。

        取消の請求書は「—」(状態の payment_status は請求書の状態を見ないまま = N56 の 1-1)。
        """
        if self.status == self.STATUS_CANCELLED:
            return "—"
        return self.PAYMENT_LABELS[self.payment_status()]


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


class Payment(models.Model):
    """請求書に対する入金。取消は行を消さず status を取消にして履歴に残す。

    残と状態は Invoice の method が取消でない入金を Python で足して出す (列に持たない)。書くときは billing.services を通す。
    """

    METHOD_TRANSFER = "transfer"
    METHOD_CASH = "cash"
    METHOD_BILL = "bill"
    METHOD_OTHER = "other"
    METHOD_CHOICES = [
        (METHOD_TRANSFER, "振込"),
        (METHOD_CASH, "現金"),
        (METHOD_BILL, "手形"),
        (METHOD_OTHER, "その他"),
    ]

    STATUS_RECORDED = "recorded"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_RECORDED, "記録"),
        (STATUS_CANCELLED, "取消"),
    ]

    invoice = models.ForeignKey(
        Invoice,
        verbose_name="請求書",
        on_delete=models.PROTECT,
        related_name="payments",
    )
    received_on = models.DateField("入金日")
    amount = models.DecimalField("金額", max_digits=14, decimal_places=2)
    method = models.CharField(
        "入金の方法", max_length=10, choices=METHOD_CHOICES, default=METHOD_TRANSFER
    )
    note = models.CharField("備考", max_length=500, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="記録した人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    recorded_at = models.DateTimeField("記録の日時", auto_now_add=True)
    status = models.CharField(
        "状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_RECORDED
    )

    class Meta:
        verbose_name = "入金"
        verbose_name_plural = "入金"
        ordering = ["-received_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="billing_payment_amount_positive",
            ),
        ]

    def __str__(self):
        return f"{self.invoice.invoice_no} {self.received_on:%Y-%m-%d}"
