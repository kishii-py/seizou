"""画面の案内とガイドツアーの tag (読むだけ)。ダッシュボードの「はじめての方へ」の枠 (flow_guide)、上の帯の「案内」のボタンを出すか (screen_has_guide)、ツアーの段の並び (seizou_tour)。画面は今の要求の URL の名前で引き、画面の class の属性は使わない (R15)。

{% flow_guide as flow %}               業務の流れの段と件数の値 (core.dashboard.flow_guide)
{% today_tasks as today %}             ダッシュボードの「今日やること」の枠の値 (core.dashboard.today_tasks。home.html が読む。部署で開けない行と段と、その人が進められない行は外し、何も残らなければ None)
{% screen_has_guide as has_guide %}    今の画面にユースケースがあるか (_topbar.html が「案内」のボタンを出すかに使う)
{% seizou_tour %}                      今の画面のツアーの段の JSON と markup (core/_tour.html。段の無い画面は何も出さない)
"""

from django import template

from seizou.core import dashboard, screens, tours

register = template.Library()


@register.simple_tag
def flow_guide():
    return dashboard.flow_guide()


@register.simple_tag(takes_context=True)
def today_tasks(context):
    """「今日やること」の枠の値 (core.dashboard.today_tasks)。request の利用者で、部署で開けない行と「はじめての方へ」の段と、その人が進められない行 (作者の決め) を外す。行も段も残らなければ None (枠ごと出さない)。"""
    user = getattr(context.get("request"), "user", None)
    return dashboard.today_tasks(user)


@register.simple_tag(takes_context=True)
def screen_has_guide(context) -> bool:
    """今の要求の view の class に画面の名前とユースケースがあれば真。目録は歩かない (全画面の上の帯で呼ぶため)。"""
    match = getattr(context.get("request"), "resolver_match", None)
    view_class = getattr(getattr(match, "func", None), "view_class", None)
    if view_class is None or not getattr(view_class, "screen_name", ""):
        return False
    return bool(screens.usecase_from(view_class.__doc__))


@register.inclusion_tag("core/_tour.html", takes_context=True)
def seizou_tour(context):
    """今の画面のツアーの段 (core.tours.tour_for)。request の無い描き方とツアーの無い画面は steps が None (何も出さない)。"""
    match = getattr(context.get("request"), "resolver_match", None)
    url_name = (getattr(match, "view_name", "") or "") if match is not None else ""
    return {"steps": tours.tour_for(url_name) if url_name else None}
