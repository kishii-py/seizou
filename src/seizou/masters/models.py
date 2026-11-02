"""マスターの表 (01 の 4 節)。

文字数の上限は固定 (識別子 35・名称 100・備考 500・コード 10)。
ほかの部品はこの表を外部キーで指してよいが、書くときは masters.services を通す。
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from seizou.core.models import Company


class Partner(models.Model):
    """取引先。役は得意先・外注先・両方のどれか。

    小計と消費税の計算方法が空のときは、会社 (core の Company) の既定に従う。
    """

    ROLE_CUSTOMER = "customer"
    ROLE_SUBCONTRACTOR = "subcontractor"
    ROLE_BOTH = "both"
    ROLE_CHOICES = [
        (ROLE_CUSTOMER, "得意先"),
        (ROLE_SUBCONTRACTOR, "外注先"),
        (ROLE_BOTH, "両方"),
    ]

    code = models.CharField("取引先コード", max_length=10, unique=True)
    name = models.CharField("名前", max_length=100)
    short_name = models.CharField("略称", max_length=100, blank=True)
    role = models.CharField("役", max_length=15, choices=ROLE_CHOICES)
    # 締日は空欄か 1〜31 (31 は月末)。
    closing_day = models.PositiveSmallIntegerField(
        "締日",
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
    )
    payment_day = models.CharField("支払日", max_length=2, blank=True)
    rounding = models.CharField(
        "小計の計算方法", max_length=10, choices=Company.ROUNDING_CHOICES, blank=True
    )
    tax_mode = models.CharField(
        "消費税の計算方法", max_length=10, choices=Company.TAX_CHOICES, blank=True
    )
    note = models.CharField("備考", max_length=500, blank=True)

    class Meta:
        verbose_name = "取引先"
        verbose_name_plural = "取引先"
        ordering = ["code"]

    def __str__(self):
        return self.name

    @property
    def is_customer(self):
        return self.role in (self.ROLE_CUSTOMER, self.ROLE_BOTH)

    @property
    def is_subcontractor(self):
        return self.role in (self.ROLE_SUBCONTRACTOR, self.ROLE_BOTH)


class Item(models.Model):
    """部品マスター (品目)。品番は会社の中で一意。"""

    part_no = models.CharField("自社品番", max_length=35, unique=True)
    name = models.CharField("品名", max_length=100)
    drawing_no = models.CharField("図面番号", max_length=35, blank=True)
    drawing = models.FileField("図面ファイル", upload_to="drawings/", blank=True)
    unit = models.CharField("単位", max_length=10, blank=True)
    standard_price = models.DecimalField(
        "標準単価", max_digits=14, decimal_places=2, null=True, blank=True
    )
    note = models.CharField("備考", max_length=500, blank=True)

    class Meta:
        verbose_name = "品目"
        verbose_name_plural = "品目"
        ordering = ["part_no"]

    def __str__(self):
        return f"{self.part_no} {self.name}"


class PartnerItem(models.Model):
    """取引先ごとの品番。取引先の品番を自社の品目に対応させる。"""

    partner = models.ForeignKey(
        Partner,
        verbose_name="取引先",
        on_delete=models.PROTECT,
        related_name="partner_items",
    )
    item = models.ForeignKey(
        Item,
        verbose_name="自社品番",
        on_delete=models.PROTECT,
        related_name="partner_items",
    )
    partner_part_no = models.CharField("取引先の品番", max_length=35)
    partner_item_name = models.CharField("取引先の品名", max_length=100, blank=True)

    class Meta:
        verbose_name = "取引先の品番"
        verbose_name_plural = "取引先の品番"
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "partner_part_no"],
                name="masters_partneritem_partner_part_no_unique",
            ),
        ]


class Process(models.Model):
    """工程。"""

    code = models.CharField("工程コード", max_length=10, unique=True)
    name = models.CharField("名前", max_length=100)
    is_outsourced = models.BooleanField("外注か", default=False)
    sort_order = models.PositiveIntegerField("並び順", default=0)

    class Meta:
        verbose_name = "工程"
        verbose_name_plural = "工程"
        ordering = ["sort_order", "code"]


class ItemProcess(models.Model):
    """標準の工程の並び。"""

    item = models.ForeignKey(
        Item,
        verbose_name="品目",
        on_delete=models.CASCADE,
        related_name="item_processes",
    )
    process = models.ForeignKey(
        Process,
        verbose_name="工程",
        on_delete=models.PROTECT,
        related_name="item_processes",
    )
    seq = models.PositiveIntegerField("順")

    class Meta:
        verbose_name = "標準の工程の並び"
        verbose_name_plural = "標準の工程の並び"
        ordering = ["item", "seq"]
