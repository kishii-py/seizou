"""注残の盤 (注文残の一覧) のテンプレートの tag。読むだけで、表に書かない。

- board_totals: 絞り込みの後の全部の明細の合計 (数量・出荷済み・残)。土台の合計の行は annotate の列を足せないため。
"""

from django import template

from seizou.orders import board

register = template.Library()


@register.simple_tag
def board_totals(queryset):
    """{% board_totals paginator.object_list as sums %}。ページでなく一覧の全体の合計 {"qty", "shipped", "remaining"}。"""
    return board.totals_of(queryset)
