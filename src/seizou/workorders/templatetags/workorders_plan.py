"""生産計画の盤の template tag (読むだけ。表に書かない)。

{% plan_board as board %}: GET の盤の中身を plans.build_board で作る。
工程は view の kwargs の process_pk (無ければ印の付いた最初の工程)、期間は request.GET の start・weeks。
POST の plan_place は view の中で同じ関数を呼ぶので、盤の部分 (_plan_board.html) はこの tag を呼ばない。
"""

from django import template

from seizou.workorders import plans

register = template.Library()


@register.simple_tag(takes_context=True)
def plan_board(context):
    view = context.get("view")
    kwargs = getattr(view, "kwargs", None) or {}
    params = context["request"].GET
    process = plans.resolve_process(kwargs.get("process_pk"))
    return plans.build_board(
        process, plans.parse_start(params.get("start")), plans.parse_weeks(params.get("weeks"))
    )
