"""ブックマークの帯の ☆ と、最初の画面の「よく使う画面」が使う tag。読むだけ (書くのは services の関数)。

{% bookmark_star %}                 上の帯の ☆ と小窓 (bookmarks/_star_inner.html を描く。_star.html が呼ぶ)
{% bookmark_home as bookmarks %}    最初の画面に並べる登録 (見つからなくなった画面を除く。_home.html が使う)

_home.html は core の home.html が直に include するので、_home.html 自身を描く inclusion tag にすると
include と tag が互いを呼び続ける。そこで「よく使う画面」は並びを返す tag にし、描くのは _home.html にする。
画面の目録は services.catalog が process の中で 1 回だけ作る (要求ごとに URL の木を歩かない)。
"""

from django import template

from seizou.bookmarks import services

register = template.Library()


@register.inclusion_tag("bookmarks/_star_inner.html", takes_context=True)
def bookmark_star(context):
    request = context.get("request")
    user = getattr(request, "user", None)
    if request is None or not getattr(user, "is_authenticated", False):
        # ログインしていない人には空の物を渡す (テンプレートは何も出さない)。
        return {"user": user, "bookmarks": []}
    match = getattr(request, "resolver_match", None)
    url_name = (match.view_name or "") if match is not None else ""
    has_args = match is not None and bool(match.args or match.kwargs)
    screen = services.screen_of(url_name)
    # csrf_token は inclusion tag の仕組み (Django) が新しい context に写す。
    return {
        "user": user,
        "current_url_name": url_name,
        "current_label": screen.name if screen is not None else (context.get("screen_name") or ""),
        "can_bookmark": bool(url_name) and not has_args and services.bookmarkable(url_name),
        "bookmarked": services.is_bookmarked(user, url_name),
        "bookmarks": services.bookmarks_for(user),
        "next_path": request.get_full_path(),
    }


@register.simple_tag(takes_context=True)
def bookmark_home(context):
    request = context.get("request")
    return [item for item in services.bookmarks_for(getattr(request, "user", None)) if not item["lost"]]
