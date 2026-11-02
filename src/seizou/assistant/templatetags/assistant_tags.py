"""AI チャットの窓の状態を渡す tag。窓 (assistant/_panel.html) は全部の画面で描かれるので、ここで例外を出さない。"""

from django import template

from seizou.assistant import services

register = template.Library()


@register.simple_tag(takes_context=True)
def assistant_panel(context):
    """窓の状態。{"available": 鍵があるか, "exchanges": 履歴の往復, "no_key_message": 鍵が無いときの 1 文}。

    context に request か session が無ければ、履歴は空にする。
    """
    request = context.get("request")
    session = getattr(request, "session", None) if request is not None else context.get("session")
    exchanges = services.history(session) if hasattr(session, "get") else []
    return {
        "available": services.is_available(),
        "exchanges": exchanges,
        "no_key_message": services.MESSAGE_NO_KEY,
    }
