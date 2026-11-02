"""注文書の読み取りの表 (05 の 9 節)。

読み取った中身は、Claude の API が返して形を検査した辞書のまま JSON で持つ。
数と金額の列は持たない (数量と単価は読み取った字のまま。受注にするときに orders の services が数と金額を決める)。
人が確かめて「受注にする」を押すまで受注にはならない。書くときは intake.services を通す。
"""

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import models
from django.utils import timezone


class Extraction(models.Model):
    """注文書の読み取り。置いた注文書 1 枚を 1 行で持つ。

    受注との結びは 1 対 1 なので、1 つの読み取りから受注は 1 件だけ。
    """

    STATUS_EXTRACTED = "extracted"
    STATUS_ORDERED = "ordered"
    STATUS_DISCARDED = "discarded"
    STATUS_CHOICES = [
        (STATUS_EXTRACTED, "読み取った"),
        (STATUS_ORDERED, "受注にした"),
        (STATUS_DISCARDED, "捨てた"),
    ]

    customer = models.ForeignKey(
        "masters.Partner",
        verbose_name="得意先",
        on_delete=models.PROTECT,
        related_name="extractions",
    )
    file = models.FileField("元のファイル", upload_to="intake/%Y/%m/")
    extracted = models.JSONField("読み取った中身")
    status = models.CharField("状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_EXTRACTED)
    order = models.OneToOneField(
        "orders.Order",
        verbose_name="作った受注",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="extraction",
    )
    read_at = models.DateTimeField("読み取った日時", default=timezone.now)
    read_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="読み取った人",
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        verbose_name = "注文書の読み取り"
        verbose_name_plural = "注文書の読み取り"
        ordering = ["-read_at", "-id"]

    def __str__(self):
        read = timezone.localtime(self.read_at)
        return f"{self.customer.name} {read:%Y-%m-%d %H:%M}"

    def summary(self):
        """一覧に出す中身の要約 (例「X-100 ×10 ほか 1 行」)。extracted の lines の形が違えば空の字。"""
        lines = self.extracted.get("lines") if isinstance(self.extracted, dict) else None
        if not isinstance(lines, list) or not lines or not isinstance(lines[0], dict):
            return ""
        part_no = lines[0].get("part_no")
        qty = lines[0].get("qty")
        if not isinstance(part_no, str) or not part_no:
            return ""
        if isinstance(qty, bool) or not isinstance(qty, (str, int)):
            return ""
        try:
            qty_text = f"{Decimal(str(qty)).normalize():f}"
        except InvalidOperation:
            return ""
        text = f"{part_no} ×{qty_text}"
        if len(lines) > 1:
            text += f" ほか {len(lines) - 1} 行"
        return text
