"""画面の案内とガイドツアーの tag (読むだけ)。ダッシュボードの「はじめての方へ」の枠 (flow_guide)、上の帯の「案内」のボタンを出すか (screen_has_guide)、ツアーの段の並び (seizou_tour)。画面は今の要求の URL の名前で引き、画面の class の属性は使わない (R15)。

{% flow_guide as flow %}               ダッシュボードの枠の値 (core.dashboard.flow_guide。home.html が読む)
{% screen_has_guide as has_guide %}    今の画面にユースケースがあるか (_topbar.html が「案内」のボタンを出すかに使う)
"""

from django import template

from seizou.core import dashboard, screens

register = template.Library()


@register.simple_tag
def flow_guide():
    return dashboard.flow_guide()


@register.simple_tag(takes_context=True)
def screen_has_guide(context) -> bool:
    """今の要求の view の class に画面の名前とユースケースがあれば真。目録は歩かない (全画面の上の帯で呼ぶため)。"""
    match = getattr(context.get("request"), "resolver_match", None)
    view_class = getattr(getattr(match, "func", None), "view_class", None)
    if view_class is None or not getattr(view_class, "screen_name", ""):
        return False
    return bool(screens.usecase_from(view_class.__doc__))
