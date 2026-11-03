"""受注の表 (01 の 4 節)。

項目の名前は中小企業共通 EDI の注文の必須の項目を元にした (得意先の注文番号・受注日・行番号・単価・数量・単位・納期)。
文字数の上限は固定 (識別子 35・備考 500・コード 10)。
出荷済みの数と残の数は列に持たず、orders.services の shipped_qty・remaining_qty で毎回出す。
書くときは orders.services を通す。
"""

from functools import cached_property

from django.apps import apps
from django.db import models


def _part_model(app_label, name):
    """下流の部品の model を名前で引く (部品の module を import しない)。部品が入っていなければ AttributeError。

    土台の詳細は hasattr でタブを外すので、部品の無い会社ではそのタブが出ない。
    """
    try:
        return apps.get_model(app_label, name)
    except LookupError as exc:
        raise AttributeError(f"{app_label}.{name} が入っていない") from exc


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
        (SOURCE_INBOX, "届いた注文"),
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

    def __str__(self):
        return self.order_no

    @property
    def workorders(self):
        """この受注の明細から発行した指示書 (取消も含む)。

        受注の詳細のタブのための読むだけの入口 (土台の tabs は名前で引く)。表は下流の部品の物で、orders は書かない。
        """
        return _part_model("workorders", "WorkOrder").objects.filter(order_line__order=self)

    @property
    def shipments(self):
        """この受注の明細の出荷。

        受注の詳細のタブのための読むだけの入口 (土台の tabs は名前で引く)。表は下流の部品の物で、orders は書かない。
        """
        return _part_model("shipping", "Shipment").objects.filter(order_line__order=self)

    @property
    def invoices(self):
        """この受注の出荷が入った請求書 (取消も含む。1 枚に同じ受注の出荷が 2 つあっても 1 行)。

        受注の詳細のタブのための読むだけの入口 (土台の tabs は名前で引く)。表は下流の部品の物で、orders は書かない。
        """
        return _part_model("billing", "Invoice").objects.filter(lines__shipment__order_line__order=self).distinct()


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
        verbose_name="部品",
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

    def __str__(self):
        text = f"{self.order.order_no}-{self.line_no} {self.item.part_no} {self.item.name}"
        # 集計 (services.annotate_remaining) を通した明細だけ、末尾に受注の残りを足す (出荷の入力の候補の口の字。
        # 出荷の画面の JS が「 残り n」を読んで数量の欄の下に写す)。集計の無い明細は今の字のまま。
        remaining = getattr(self, "remaining_total", None)
        if remaining is None:
            return text
        return f"{text} 残り {remaining.normalize():f}"

    @property
    def status_label(self):
        """受注の状態の表示名 (注文残の一覧の列に使う。表示だけ)。"""
        return self.order.get_status_display()

    @cached_property
    def board(self):
        """注残の盤の判断の値 (読むだけ)。一覧の columns が board__<名前> で読む。関数は orders.board。"""
        from .board import BoardRow

        return BoardRow(self)

    @cached_property
    def acceptance(self):
        """検収の要約 (読むだけ)。受注の詳細の明細の表が読む。関数は orders.services.acceptance_summary。"""
        from . import services

        return services.acceptance_summary(self)
