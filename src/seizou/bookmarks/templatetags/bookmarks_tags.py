"""ブックマークの帯の ☆ と、左のメニューの組と、最初の画面の「よく使う画面」が使う tag。読むだけ (書くのは services の関数)。

{% bookmark_star %}                 上の帯の ☆ と小窓 (bookmarks/_star_inner.html を描く。_star.html が呼ぶ)
{% bookmark_home as bookmarks %}    最初の画面に並べる登録 (見つからなくなった画面を除く。_home.html が使う)
{% bookmark_menu as bookmarks %}    左のメニューの「ブックマーク」の組に並べる登録 (見つからなくなった画面を除き、今の画面の印つき。_menu.html が使う)

登録の並び (services.bookmarks_for) は 1 つの要求で 1 回だけ読み、request に持たせる (_bookmarks_of)。上の帯の ☆・左のメニュー・
「よく使う画面」が同じ並びを使うので、画面ごとに登録の表を 3 回読まない。

_home.html は core の home.html が直に include するので、_home.html 自身を描く inclusion tag にすると
include と tag が互いを呼び続ける。そこで「よく使う画面」は並びを返す tag にし、描くのは _home.html にする。
画面の目録は services.catalog が process の中で 1 回だけ作る (要求ごとに URL の木を歩かない)。
"""

from django import template

from seizou.bookmarks import services
from seizou.core.context_processors import mark_active

register = template.Library()


def _bookmarks_of(request) -> list[dict]:
    """その人の登録の並び (services.bookmarks_for) を、1 つの要求で 1 回だけ読んで request に持たせる。request が無ければ空の並び (読まない)。

    訳: 上の帯の ☆ (bookmark_star)・左のメニューの組 (bookmark_menu)・最初の画面の「よく使う画面」(bookmark_home) が同じ並びを使う。
    全部の画面に出る左のメニューのために、登録の表を画面ごとに何度も読まない。
    """
    if request is None:
        return []
    items = getattr(request, "_seizou_bookmarks", None)
    if items is None:
        items = services.bookmarks_for(getattr(request, "user", None))
        request._seizou_bookmarks = items
    return items


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
        "bookmarks": _bookmarks_of(request),
        "next_path": request.get_full_path(),
    }


@register.simple_tag(takes_context=True)
def bookmark_home(context):
    return [item for item in _bookmarks_of(context.get("request")) if not item["lost"]]


@register.simple_tag(takes_context=True)
def bookmark_menu(context):
    """左のメニューの「ブックマーク」の組に並べる登録 (bookmarks/_menu.html。受け渡しの seizou-部品の6案-20261107/指示書.md の 5)。

    見つからなくなった画面は並べない (上の帯の ☆ の小窓で「消す」)。今の画面の印 (active) は左のメニューの入口と同じ決め方
    (core.context_processors.mark_active = URL の先頭が合う物のうち最も長い 1 つ)。request が無いときとログインしていない人は空の並び。
    並びの辞書は写してから印を付ける (request に持たせた並びを変えない)。
    """
    request = context.get("request")
    items = [dict(item) for item in _bookmarks_of(request) if not item["lost"]]
    mark_active(items, getattr(request, "path", "") or "")
    return items
