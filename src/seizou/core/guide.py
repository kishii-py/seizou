"""画面の「案内」の小窓の口 (読むだけ)。その画面のユースケースの 4 行 (screens.usecase_rows) と、業務の流れの前後の画面 (dashboard.flow_neighbors) を出す。htmx の要求だけに答え、ほかは 400。目録に無い・ユースケースの無い画面は 404。

/guide/<anchor>/ (core:screen_guide)。anchor は URL の名前の : を _ に替えた字 (画面の土台が context に置く screen_anchor)。
画面は目録 (manual.screen_for_anchor) で引き、anchor の字は HTML に戻さない (目録の画面の字だけを出す)。
ユースケースの 4 行は部署の権限を見ない (中身は画面の class の docstring の字だけで、数も金額も出さない)。
前後の画面への link は、その人が部署で開ける段だけで決める (dashboard.flow_neighbors に人を渡す。開けない画面への link を出さない。504 回目の直し)。
"""

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from seizou.core import dashboard, tours
from seizou.core.manual import screen_for_anchor
from seizou.core.screens import usecase_rows

#: 小窓の中身のテンプレート (小窓の枠は base.html の #seizou-modal の 1 か所)
GUIDE_TEMPLATE = "core/_screen_guide.html"

#: htmx でない要求 (新しいタブ・JS の無いとき) に返す 1 文
NOT_HTMX = "この口は画面の上の帯の「案内」から開きます。"


@login_required
@never_cache
def screen_guide(request, anchor: str):
    """上の帯の「案内」が開く小窓の中身 (その画面のユースケースの 4 行と、流れの前後の画面への link)。"""
    if not request.headers.get("HX-Request"):
        return HttpResponseBadRequest(NOT_HTMX)
    screen = screen_for_anchor(anchor)
    if screen is None:
        raise Http404("この画面の案内はありません。")
    rows = usecase_rows(screen.usecase)
    if not rows:
        # ユースケースの無い画面はボタンを出さないので、ここに来るのは手で打ったときだけ。
        raise Http404("この画面の案内はありません。")
    return render(
        request,
        GUIDE_TEMPLATE,
        {
            "screen": screen,
            "rows": rows,
            "neighbors": dashboard.flow_neighbors(screen.url_name, request.user),
            "has_tour": tours.tour_for(screen.url_name) is not None,
        },
    )
