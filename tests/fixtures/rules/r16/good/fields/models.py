"""R16 の違反しない見本 (会社ごとの項目は拡張の表に足す)。検査が読むだけで、動かさない。

部品の表を OneToOneField で指すのは継承でないので、違反にしない (部品の class を import して指しても同じ)。
"""

from django.db import models
from seizou.masters.models import Partner


class ItemExt(models.Model):
    item = models.OneToOneField("masters.Item", on_delete=models.CASCADE, related_name="ext", verbose_name="品目")
    ext01 = models.CharField("材質", max_length=100, blank=True)


class PartnerExt(models.Model):
    partner = models.OneToOneField(Partner, on_delete=models.CASCADE, related_name="ext", verbose_name="取引先")
    ext01 = models.CharField("地域", max_length=50, blank=True)
