"""マスターの表 (01 の 4 節)。

文字数の上限は固定 (識別子 35・名称 100・備考 500・コード 10)。
ほかの部品はこの表を外部キーで指してよいが、書くときは masters.services を通す。
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from seizou.core.models import Company

#: 締日と支払日の 31 は月末の印
MONTH_END_DAY = 31


def day_label(day):
    """月の中の日の画面の字。31 は「月末」、ほかは「n 日」、空は None。"""
    if day is None:
        return None
    return "月末" if day == MONTH_END_DAY else f"{day} 日"


class Partner(models.Model):
    """取引先。取引先の種類は得意先・外注先・両方のどれか。

    金額と消費税の端数の扱いが空のときは、会社 (core の Company) の既定に従う。
    支払の条件は 支払の月 (当月・翌月・翌々月) と 支払日 (1〜31。31 は月末) の 2 列。画面には 1 つの字で出す。
    """

    ROLE_CUSTOMER = "customer"
    ROLE_SUBCONTRACTOR = "subcontractor"
    ROLE_BOTH = "both"
    ROLE_CHOICES = [
        (ROLE_CUSTOMER, "得意先"),
        (ROLE_SUBCONTRACTOR, "外注先"),
        (ROLE_BOTH, "両方"),
    ]

    PAYMENT_CURRENT = "current"
    PAYMENT_NEXT = "next"
    PAYMENT_AFTER_NEXT = "after_next"
    PAYMENT_MONTH_CHOICES = [
        (PAYMENT_CURRENT, "当月"),
        (PAYMENT_NEXT, "翌月"),
        (PAYMENT_AFTER_NEXT, "翌々月"),
    ]

    code = models.CharField("取引先コード", max_length=10, unique=True)
    name = models.CharField("名前", max_length=100)
    short_name = models.CharField("略称", max_length=100, blank=True)
    role = models.CharField("取引先の種類", max_length=15, choices=ROLE_CHOICES)
    # 締日は空欄か 1〜31 (31 は月末)。
    closing_day = models.PositiveSmallIntegerField(
        "締日",
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
    )
    # 支払の月は空 (未設定) か 当月・翌月・翌々月。支払日は空か 1〜31 (31 は月末。締日と同じ約束)。
    payment_month = models.CharField(
        "支払の月", max_length=10, choices=PAYMENT_MONTH_CHOICES, blank=True, default=""
    )
    payment_day = models.PositiveSmallIntegerField(
        "支払日",
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
    )
    rounding = models.CharField(
        "金額の端数の扱い", max_length=10, choices=Company.ROUNDING_CHOICES, blank=True
    )
    tax_mode = models.CharField(
        "消費税の端数の扱い", max_length=10, choices=Company.TAX_CHOICES, blank=True
    )
    note = models.CharField("備考", max_length=500, blank=True)

    class Meta:
        verbose_name = "取引先"
        verbose_name_plural = "取引先"
        ordering = ["code"]

    def __str__(self):
        return self.name

    def get_closing_day_display(self):
        """締日の画面の字 (31 は「月末」、ほかは「n 日」)。表の値は数のまま。一覧と詳細の土台が読む。"""
        return day_label(self.closing_day)

    def get_payment_day_display(self):
        """支払の条件の画面の字。支払の月と合わせて 1 つにする (例「翌月 25 日」「翌月末」)。表の値は 2 列のまま。"""
        month = self.get_payment_month_display() if self.payment_month else ""
        if self.payment_day is None:
            return month or None
        if self.payment_day == MONTH_END_DAY:
            return f"{month}末" if month else day_label(self.payment_day)
        return f"{month} {day_label(self.payment_day)}" if month else day_label(self.payment_day)

    @property
    def is_customer(self):
        return self.role in (self.ROLE_CUSTOMER, self.ROLE_BOTH)

    @property
    def is_subcontractor(self):
        return self.role in (self.ROLE_SUBCONTRACTOR, self.ROLE_BOTH)


class Item(models.Model):
    """部品マスター (部品)。品番は会社の中で一意。"""

    part_no = models.CharField("自社品番", max_length=35, unique=True)
    name = models.CharField("品名", max_length=100)
    drawing_no = models.CharField("図面番号", max_length=35, blank=True)
    drawing = models.FileField("図面ファイル", upload_to="drawings/", blank=True)
    # 図面の表示用の PNG (元が TIFF などのとき、保存のときに 1 回だけ作る)。書くのは masters.services だけ。
    drawing_preview = models.FileField(
        "図面の表示用", upload_to="drawings/previews/", blank=True, editable=False
    )
    unit = models.CharField("単位", max_length=10, blank=True)
    standard_price = models.DecimalField(
        "標準単価 (円)", max_digits=14, decimal_places=2, null=True, blank=True
    )
    note = models.CharField("備考", max_length=500, blank=True)
    # 図面の表示用の絵を作れなかった理由の 1 文 (空 = 作れた、か作る必要が無い)。書くのは masters.services だけ。
    # 最後に置く (土台の小窓は先頭の 8 項目を出すので、その外に置き、小窓では黄色の 1 行で出す)。
    drawing_preview_error = models.CharField(
        "図面の表示用の誤り", max_length=500, blank=True, editable=False
    )

    class Meta:
        verbose_name = "部品"
        verbose_name_plural = "部品"
        ordering = ["part_no"]

    def __str__(self):
        return f"{self.part_no} {self.name}"

    @property
    def display_drawing(self):
        """絵として出す図面のファイル (読むだけ)。出す物が無ければ None。

        表示用の絵を作れなかった (誤りの字がある) ときは None (壊れた絵を出さない)。
        表示用の PNG があればそれ。無ければ元の図面が PNG・JPEG ならそれ。PDF などは None (link だけ)。
        """
        if self.drawing_preview_error:
            return None
        if self.drawing_preview:
            return self.drawing_preview
        if self.drawing and self.drawing.name.lower().endswith((".png", ".jpg", ".jpeg")):
            return self.drawing
        return None


class PartnerItem(models.Model):
    """取引先ごとの品番。取引先の品番を自社の部品に対応させる。"""

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

    def __str__(self):
        partner = self.partner.short_name or self.partner.name
        return f"{partner} {self.partner_part_no}"


class Process(models.Model):
    """工程。"""

    code = models.CharField("工程コード", max_length=10, unique=True)
    name = models.CharField("名前", max_length=100)
    is_outsourced = models.BooleanField("外注か", default=False)
    sort_order = models.PositiveIntegerField("並び順", default=0)
    use_in_plan = models.BooleanField("計画に使う", default=False)

    class Meta:
        verbose_name = "工程"
        verbose_name_plural = "工程"
        ordering = ["sort_order", "code"]

    def __str__(self):
        return f"{self.code} {self.name}"


class ItemProcess(models.Model):
    """標準の工程の並び。"""

    item = models.ForeignKey(
        Item,
        verbose_name="部品",
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

    def __str__(self):
        return f"{self.item.part_no} {self.process}"
