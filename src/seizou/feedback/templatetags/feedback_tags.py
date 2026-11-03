"""feedback の画面が使う tag。読むだけ (書くのは services の関数)。

部品の画面は R15 で get_context_data を上書きできないので、画面に足す物はここで渡す。
{% feedback_detail object as info %}   詳細の Issue の文 (info.issue_text) と、世話役の印 (info.can_manage) と、削除の印 (info.can_delete)
{% feedback_panel as panel %}          入力の画面の AI の段の状態 (鍵があるか・会話・開いていた画面)
{% feedback_filter as filter %}        一覧の絞り込みの選択肢と今の値
"""

from django import template

from seizou.feedback import chat, services
from seizou.feedback.models import Feedback

register = template.Library()


@register.simple_tag(takes_context=True)
def feedback_panel(context):
    """AI の段の状態。{"available", "turns", "screen", "no_key_message", "max_questions"}。例外を出さない。

    screen は POST の screen、無ければ GET の screen (保存で止まった画面の描き直しでも、開いていた画面を失わない)。
    request が無ければ会話は空。
    """
    request = context.get("request")
    session = getattr(request, "session", None) if request is not None else None
    screen = ""
    if request is not None:
        screen = request.POST.get("screen") or request.GET.get("screen") or ""
    return {
        "available": chat.is_available(),
        "turns": chat.chat(session)["turns"],
        "screen": screen,
        "no_key_message": chat.MESSAGE_NO_KEY,
        "max_questions": chat.MAX_QUESTIONS,
    }


@register.simple_tag(takes_context=True)
def feedback_detail(context, feedback):
    """詳細に足す物。{"issue_text": GitHub の Issue に写す文, "can_manage": 世話役か, "can_delete": 下書きを消せるか, "screenshots": 画面の絵 (画像の添付ファイル。付けた順)}。

    request が無ければ世話役でなく、消せない。
    """
    request = context.get("request")
    user = getattr(request, "user", None) if request is not None else None
    return {
        "issue_text": services.export_markdown(feedback),
        "screenshots": services.screenshots_of(feedback),
        "can_manage": user is not None and services.can_manage(user),
        "can_delete": user is not None and services.can_delete(feedback, user),
    }


@register.simple_tag(takes_context=True)
def feedback_filter(context):
    """一覧の絞り込みの選択肢と今の値。{"statuses", "kinds", "status", "kind"}。例外を出さない。

    今の値は services.list_filter が GET から読む (外の値は空の字)。request が無ければ空の字。
    """
    request = context.get("request")
    params = getattr(request, "GET", {}) if request is not None else {}
    return {"statuses": Feedback.STATUS_CHOICES, "kinds": Feedback.KIND_CHOICES, **services.list_filter(params)}
