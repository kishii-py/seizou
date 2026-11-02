"""bookmarks の口。画面は作らない (上の帯の ☆ の小窓と、最初の画面の「よく使う画面」は template tag が描く)。

登録と解除の口は画面でないので関数の view (R15 は class だけを見る)。書くのは services の関数だけ (R13)。
断ったときだけ赤の知らせを出す (済んだことは ☆ の塗りで分かり、右上の知らせは小窓に重なるため出さない)。
戻る先は、同じホストを指す next のときだけ next。ほかは最初の画面。
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import services


@login_required
@require_POST
def toggle(request):
    """画面を登録するか外す。POST の url_name と on ("1" = 登録、それ以外 = 外す) と next (戻る先)。"""
    url_name = request.POST.get("url_name", "")
    try:
        if request.POST.get("on") == "1":
            services.add_bookmark(request.user, url_name)
        else:
            services.remove_bookmark(request.user, url_name)
    except ValueError as exc:
        messages.error(request, str(exc))
    return HttpResponseRedirect(_next_or_home(request))


def _next_or_home(request) -> str:
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return "/"
