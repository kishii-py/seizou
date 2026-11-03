"""在庫の表 (完成品の在庫。北極星 7 節の「在庫」)。

表は在庫の動き (StockMove) だけで、在庫数は列に持たない。在庫数は stock.services が部品ごとの動きの合計から
毎回出す (出荷済みの数と同じ考え = 01 の 10.4)。出荷は動きの表に書かず、出荷の表から集計で引く。
元の記録 (製造の完了 = 指示書、受入 = 受入の行) は contenttypes の種類と番号で指す (汎用の参照。attachments と同じ形)。
ほかの部品の表に列を足さず、ほかの部品の model を import しない (部品は外部キーの字で指す)。
書くときは stock.services を通す。数量の符号 (入庫は正、出庫は負) は services が種類で決める。
"""

from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes import fields as contenttypes_fields
from django.contrib.contenttypes.models import ContentType
from django.db import models

KIND_FINISHED = "finished"
KIND_RECEIVED = "received"
KIND_SHIPPED = "shipped"
KIND_ISSUED = "issued"
KIND_ADJUSTED = "adjusted"
#: 動きの種類 (値, 画面の字)。shipped は今は書かない (出荷は出荷の表から集計で引く)。choices には残す
KIND_CHOICES = (
    (KIND_FINISHED, "製造の完了"),
    (KIND_RECEIVED, "受入"),
    (KIND_SHIPPED, "出荷"),
    (KIND_ISSUED, "払い出し"),
    (KIND_ADJUSTED, "棚卸しの調整"),
)

_SIGNS = {
    KIND_FINISHED: 1,
    KIND_RECEIVED: 1,
    KIND_SHIPPED: -1,
    KIND_ISSUED: -1,
    KIND_ADJUSTED: 0,
}


def _qty_text(value) -> str:
    """数量の字。末尾の 0 を落とす (10.00 → 10、-2.50 → -2.5)。"""
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(dec.normalize(), "f") if dec != dec.to_integral_value() else str(dec.quantize(Decimal(1)))


def kind_sign(kind):
    """種類ごとの数量の符号。入庫 (製造の完了・受入) は +1、出庫 (出荷・払い出し) は -1、調整は 0 (符号を変えない)。"""
    return _SIGNS[kind]


class StockMove(models.Model):
    """在庫の動き。1 行 = 1 つの部品の 1 回の入庫か出庫か調整。在庫数はこの表の数量の合計 − 出荷の合計。"""

    item = models.ForeignKey(
        "masters.Item",
        verbose_name="部品",
        on_delete=models.PROTECT,
        related_name="stock_moves",
    )
    kind = models.CharField("種類", max_length=10, choices=KIND_CHOICES)
    qty = models.DecimalField("数量", max_digits=12, decimal_places=2)
    moved_on = models.DateField("日付")
    # 元の記録が消えても動きは残す (現物優先)。
    content_type = models.ForeignKey(
        ContentType,
        verbose_name="元の記録の種類",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    object_id = models.PositiveBigIntegerField("元の記録の番号", null=True, blank=True)
    source = contenttypes_fields.GenericForeignKey("content_type", "object_id")
    note = models.CharField("備考", max_length=500, blank=True)
    moved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="入れた人",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="stock_moves",
    )
    created_at = models.DateTimeField("入れた日時", auto_now_add=True)

    # 詳細と小窓に出さない列 (core/views.py の hidden_detail_fields が読む)。元の記録は source_label() で出す。
    detail_hidden_fields = ("content_type", "object_id")

    class Meta:
        verbose_name = "在庫の動き"
        verbose_name_plural = "在庫の動き"
        ordering = ("-moved_on", "-pk")
        indexes = [
            models.Index(fields=["item", "moved_on"]),
            models.Index(fields=["content_type", "object_id"]),
        ]
        constraints = [
            models.CheckConstraint(condition=~models.Q(qty=0), name="stock_stockmove_qty_nonzero"),
        ]

    def __str__(self):
        return f"{self.moved_on:%Y-%m-%d} {self.item.part_no} {self.get_kind_display()} {_qty_text(self.qty)}"

    def source_label(self) -> str:
        """元の記録の字 (一覧の「元の記録」の列)。元の記録が無ければ空の字。"""
        source = self.source
        return "" if source is None else str(source)
