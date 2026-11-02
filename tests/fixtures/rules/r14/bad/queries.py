"""R14 の違反の見本 (extra)。検査が読むだけで、動かさない。"""

from .models import Item


def items_with_flag():
    return Item.objects.filter(active=True).extra(select={"is_heavy": "weight > 10"})
