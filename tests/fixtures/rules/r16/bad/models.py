"""R16 の違反の見本 (会社の側で部品の表を継承して列を足す)。検査が読むだけで、動かさない。"""

import seizou.masters.models as masters_models
from django.db import models
from seizou.masters.models import Item


class MaterialItem(Item):
    material = models.CharField("材質", max_length=100, blank=True)


class RegionPartner(masters_models.Partner):
    region = models.CharField("地域", max_length=50, blank=True)
