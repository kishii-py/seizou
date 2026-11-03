"""発注の表 (01 の 4 節)。協力企業管理の最小の形。

文字数の上限は固定 (識別子 35・備考 500・コード 10)。
金額は purchasing.services が数量 × 単価を取引先の小計の計算方法で丸めて書く (DB で計算しない)。
受入 (Receipt) の金額も、purchasing.services が受け入れた数 × 発注の単価を同じ方法で丸めて書く。検収は受入の後に connect.services.send_acceptance が相手へ送る (列は Receipt の acceptance_*)。支給材は会期後。書くときは purchasing.services を通す。
受入の数と残は列に持たず、purchasing.services の集計 (received_qty・remaining_qty・annotate_received) で出す。
"""

from decimal import Decimal
from functools import cached_property

from django.conf import settings
from django.db import models
from django.utils import timezone


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

    @property
    def receipts(self):
        """この発注の受入 (読むだけ。詳細の「受入」のタブが .all() と .count() を読む)。"""
        return Receipt.objects.filter(line__purchase_order=self).select_related("line__purchase_order", "line__item")

    @property
    def lines_with_received(self):
        """明細に受入の数 (received_total) と受入の残 (remaining_total) を足した物 (読むだけ。詳細の明細の表が回す)。"""
        from . import services

        return services.annotate_received(self.lines.select_related("item").order_by("line_no", "pk"))


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

    def __str__(self):
        # 受注の明細と同じ形 (候補の口が str(obj) を返す)。発注と部品を読むので、引く側は select_related を付ける。
        return f"{self.purchase_order.po_no}-{self.line_no} {self.item.part_no} {self.item.name}"

    @cached_property
    def shipping_notice(self):
        """出荷の知らせの要約 (読むだけ)。発注の詳細の明細の表が読む。"""
        from . import services

        return services.shipping_notice_summary(self)


class Receipt(models.Model):
    """受入。協力企業から受け入れた物の記録。発注の明細ごとに何回でも入れられる (分納)。

    金額は purchasing.services が 受け入れた数 × 明細の単価 を発注の取引先の方法で丸めて書く (DB で計算しない)。
    列の名前を amount にするのは、金額の閲覧の無い人に土台が「—」を出すため (access.is_money_name)。
    """

    line = models.ForeignKey(
        PurchaseOrderLine,
        verbose_name="発注の明細",
        on_delete=models.PROTECT,
        related_name="receipts",
    )
    received_on = models.DateField("受入日")
    qty = models.DecimalField("数量", max_digits=12, decimal_places=2)
    amount = models.DecimalField("金額", max_digits=14, decimal_places=2)
    note = models.CharField("備考", max_length=500, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="入れた人",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="receipts",
    )
    # 検収 (受入を相手の seizou へ送った記録)。connect.services.send_acceptance が purchasing.services の
    # assign_acceptance_number・record_acceptance_response で書く。
    # 番号は 1 回目に付け、送り直すときに使い回す (相手は同じ番号を 1 件にまとめる = R11)。画面に出さない。
    acceptance_message_id = models.UUIDField("検収の番号", null=True, blank=True, unique=True)
    # 相手の受け口が 200 を返したときだけ入れる。空 = まだ相手に届いていない (か、送る物ではない)。
    acceptance_sent_at = models.DateTimeField("検収を送った日時", null=True, blank=True)
    acceptance_response = models.CharField("検収の相手の返事", max_length=500, blank=True)

    # 詳細と小窓に出さない列 (core/views.py の hidden_detail_fields が読む)。uuid は画面に出さない。
    detail_hidden_fields = ("acceptance_message_id",)

    class Meta:
        verbose_name = "受入"
        verbose_name_plural = "受入"
        ordering = ("-received_on", "-pk")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="purchasing_receipt_qty_positive",
            ),
        ]

    def __str__(self):
        line = self.line
        return f"{self.received_on:%Y-%m-%d} {line.purchase_order.po_no}-{line.line_no} {line.item.part_no} {_qty_text(self.qty)}"

    def acceptance_state(self):
        """発注の詳細の「検収」の節に出す字。送る物でない受入は空の字 (テンプレートが薄い「—」を出す)。

        送れた (相手の受け口が 200) なら「送った <日時>」、送れていなくて返事があれば
        「届いていない: <相手の返事>」。
        """
        if self.acceptance_sent_at is not None:
            return f"送った {timezone.localtime(self.acceptance_sent_at):%Y-%m-%d %H:%M}"
        if self.acceptance_response:
            return f"届いていない: {self.acceptance_response}"
        return ""


def _qty_text(value) -> str:
    """数量の字。末尾の 0 を落とす (10.00 → 10、2.50 → 2.5)。"""
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(dec.normalize(), "f") if dec != dec.to_integral_value() else str(dec.quantize(Decimal(1)))
