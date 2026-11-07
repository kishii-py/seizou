"""部品の詳細の「取引先」のタブの単価の行 (決め C19-1 の「取引先の行ごとに数量帯の単価を並べる」・PRICE-2)。読むだけで、表に書かない。

- item_price_rows: {% item_price_rows object tab.rows as price_rows %}。タブの行 (部品の取引先) ごとにその行の単価の行を並べた物と、
  共通の単価の行と、数量の字に添える単位の字を返す。{"rows": [{"obj", "url", "prices"}], "common": [単価の行], "qty_unit": 字}。
  画面の class は文脈を足す口を持たない (R15 で tab_for を上書きできない) ので、view でなく tag で組む (orders_detail の tag と同じ形)。
  単価の行は services.item_prices の 1 回の問い合わせを partner_item_id で分ける (タブの行の数だけ問い合わせを増やさない)。
  qty_unit を tag で組む訳: テンプレートからは関数に引数を渡せないため (単価の後ろの字は引数の要らない ItemPrice.unit_text を読む)。
"""

from django import template

from .. import services
from ..models import PRICE_BASIS_UNIT, price_basis_text

register = template.Library()


@register.simple_tag
def item_price_rows(item, rows):
    by_partner_item, common = {}, []
    for price in services.item_prices(item):
        if price.partner_item_id is None:
            common.append(price)
        else:
            by_partner_item.setdefault(price.partner_item_id, []).append(price)
    return {
        "rows": [{"obj": row["obj"], "url": row["url"], "prices": by_partner_item.get(row["obj"].pk, [])} for row in rows],
        "common": common,
        "qty_unit": price_basis_text(PRICE_BASIS_UNIT, item.unit),
    }
