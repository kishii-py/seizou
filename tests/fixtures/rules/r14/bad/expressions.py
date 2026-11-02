"""R14 の違反の見本 (RawSQL の import と呼び出し)。検査が読むだけで、動かさない。"""

from django.db.models.expressions import RawSQL

from .models import Item


def items_with_weight_rank():
    return Item.objects.annotate(rank=RawSQL("SELECT 1", []))
