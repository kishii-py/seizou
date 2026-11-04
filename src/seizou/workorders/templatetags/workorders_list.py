"""指示書の一覧の製造向けの絞り込みの欄の tag。読むだけで、表に書かない。

GET の読み方は workorders.worklist の 1 か所 (一覧の view の get_queryset と同じ関数を呼ぶ)。
"""

from django import template

from seizou.workorders import worklist

register = template.Library()


@register.simple_tag(takes_context=True)
def workorder_filters(context):
    """{% workorder_filters as worklist_filters %}。{"process", "unaccepted", "today", "processes"} を返す。

    受ける名前を filters にしない (土台の一覧の絞りの欄 core/_filters.html が同じ名前の context を読むため。N119)。
    """
    filters = worklist.list_filters(context["request"].GET)
    filters["processes"] = worklist.processes_for_filter()
    return filters
