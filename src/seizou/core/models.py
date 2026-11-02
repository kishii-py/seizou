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
