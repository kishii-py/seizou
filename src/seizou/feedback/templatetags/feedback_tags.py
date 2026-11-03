"""feedback の画面が使う tag。読むだけ (書くのは services の関数)。

部品の画面は R15 で get_context_data を上書きできないので、画面に足す物はここで渡す。
{% feedback_detail object as info %}   詳細の Issue の文 (info.issue_text) と、世話役の印 (info.can_manage)
"""

from django import template

from seizou.feedback import services

register = template.Library()


@register.simple_tag(takes_context=True)
def feedback_detail(context, feedback):
    """詳細に足す物。{"issue_text": GitHub の Issue に写す文, "can_manage": 世話役か}。request が無ければ世話役でない。"""
    request = context.get("request")
    user = getattr(request, "user", None) if request is not None else None
    return {
        "issue_text": services.export_markdown(feedback),
        "can_manage": user is not None and services.can_manage(user),
    }
