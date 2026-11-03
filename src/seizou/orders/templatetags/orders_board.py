"""注残の盤 (注文残の一覧) のテンプレートの tag。読むだけで、表に書かない。

- board_totals: 絞り込みの後の全部の明細の合計 (数量・出荷済み・残)。土台の合計の行は annotate の列を足せないため。
- can_write: 部品の model の新規登録 (や編集) の入口を出すか (core の access.can_write_model と同じ判定)。
"""

from django import template
from django.apps import apps

from seizou.orders import board

register = template.Library()


@register.simple_tag
def board_totals(queryset):
    """{% board_totals paginator.object_list as sums %}。ページでなく一覧の全体の合計 {"qty", "shipped", "remaining"}。"""
    return board.totals_of(queryset)


@register.simple_tag(takes_context=True)
def can_write(context, label, verb="create"):
    """{% can_write "workorders.WorkOrder" as can_issue %}。部品が無ければ偽。編集の判定が on のときだけ権限で偽になる。"""
    from seizou.core import access

    try:
        model = apps.get_model(label)
    except (LookupError, ValueError):
        return False
    request = context.get("request")
    return access.can_write_model(getattr(request, "user", None), model, verb)
