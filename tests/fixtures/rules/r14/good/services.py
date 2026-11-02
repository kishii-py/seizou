"""R14 の違反しない見本 (ORM の問い合わせだけ)。検査が読むだけで、動かさない。

文字列の中の raw( や cursor( の文字は違反にしない。
"""

import logging

from django.db.models import Count, Sum

from .models import Item

logger = logging.getLogger(__name__)


def heavy_items(min_weight):
    return Item.objects.filter(weight__gte=min_weight).annotate(drawings=Count("drawing"))


def total_qty():
    # 名前つきの引数の extra は、呼び出しの名前でないので違反にしない。
    logger.info("注文の数を集計する", extra={"kind": "qty"})
    return Item.objects.aggregate(total=Sum("qty"))["total"]


def body_of(response):
    # 呼び出していない属性の raw は違反にしない。
    return response.raw
