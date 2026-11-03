from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """利用者。会社のプロジェクトの AUTH_USER_MODEL はいつもこれを指す。"""

    class Meta:
        verbose_name = "利用者"
        verbose_name_plural = "利用者"


class Company(models.Model):
    """会社の設定。1 行だけ。組み立ての命令が書く。

    連番の形と選べる設定は settings の SEIZOU (seizou.core.conf) が持つ。
    端数と税の既定は会社で持ち、取引先ごとの値 (取引先マスター) が優先する。
    """

    ROUNDING_CHOICES = [
        ("round", "四捨五入"),
        ("ceil", "切上"),
        ("floor", "切捨"),
    ]
    TAX_CHOICES = [
        ("total", "トータル"),
        ("line_round", "各行四捨五入"),
        ("line_floor", "各行切捨"),
    ]

    name = models.CharField("会社名", max_length=100)
    short_name = models.CharField("略称", max_length=35, blank=True)
    theme = models.CharField("画面の色", max_length=35, default="light")
    rounding = models.CharField("端数の扱い", max_length=10, choices=ROUNDING_CHOICES, default="round")
    tax_mode = models.CharField("税の扱い", max_length=10, choices=TAX_CHOICES, default="total")
    address = models.CharField("住所", max_length=100, blank=True)
    phone = models.CharField("電話", max_length=35, blank=True)
    bank_account = models.CharField("振込先", max_length=100, blank=True)
    logo = models.FileField("ロゴ", upload_to="company/", blank=True)

    class Meta:
        verbose_name = "会社"
        verbose_name_plural = "会社"

    def __str__(self):
        return self.name


class NumberSequence(models.Model):
    """連番の今の値 (seizou.core.services.next_number が使う)。

    kind は番号の種類 (order・workorder・shipment・delivery_note・invoice)。
    period は年月 (設定 order_number が ym_seq のとき "2611" の形。seq のときは空)。
    """

    kind = models.CharField("種類", max_length=35)
    period = models.CharField("期間", max_length=10, blank=True, default="")
    last = models.PositiveIntegerField("最後の番号", default=0)

    class Meta:
        verbose_name = "連番"
        verbose_name_plural = "連番"
        constraints = [
            models.UniqueConstraint(fields=["kind", "period"], name="core_numbersequence_kind_period"),
        ]

    def __str__(self):
        return f"{self.kind} {self.period} {self.last}"


class SettingOverride(models.Model):
    """選べる設定の画面からの上書き。seizou.core.conf.setting が答えの YAML より先に読む。

    1 鍵 1 行。履歴は持たない (変更は logging の 1 行)。値は答えの YAML と同じ形 (真偽・字・字の並び・辞書)。
    """

    key = models.CharField("設定の鍵", max_length=50, unique=True)
    value = models.JSONField("値")
    updated_at = models.DateTimeField("変えた日時", auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="変えた人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = "設定の上書き"
        verbose_name_plural = "設定の上書き"
        ordering = ("key",)

    def __str__(self):
        return f"{self.key} = {self.value!r}"
