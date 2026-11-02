"""R14 の違反の見本 (raw)。services.py の中でも違反になる。検査が読むだけで、動かさない。"""

from .models import Item


def heavy_items(min_weight):
    return Item.objects.raw("SELECT * FROM masters_item WHERE weight >= %s", [min_weight])
