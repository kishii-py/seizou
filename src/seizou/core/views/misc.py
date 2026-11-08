"""ログインの画面 (RememberLoginView)・上の帯の知らせの鈴 (bell)・小さい道具 (core.views の package)。"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_GET

from seizou.core import conf


class RememberLoginView(LoginView):
    """ログイン。「ログイン状態を保存する」が外れていたら、ブラウザを閉じたときに切れる session にする。

    check が入っていれば、cookie の長さはログインからの最長 (会社の設定 session_max_hours) にする。操作しない間の時間切れと最長で切るのは core.middleware.SessionTimeoutMiddleware。
    """

    template_name = "core/login.html"

    def form_valid(self, form):
        # ログインが session の鍵を作り直すので、その後に長さを決める。
        response = super().form_valid(form)
        if not self.request.POST.get("remember"):
            self.request.session.set_expiry(0)
        else:
            self.request.session.set_expiry(conf.session_limits()[1])
        return response


def decimal_or_none(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - 数に読めない値 (空・字など) は None (読む側が「値が無い」と扱う。誤りでない)
        return None


# ---- 上の帯の知らせの鈴 (受け渡しの seizou-部品の6案-20261107/指示書.md の 4。本人の決め 11-07 18:2x〜18:4x) ----

BELL_NOT_HTMX = "上の帯の鈴からの要求だけを受けます。"


@login_required
@require_GET
def bell(request):
    """上の帯の鈴が開く小窓の中身 (core/_bell_modal.html。htmx の要求だけで、見出しが無ければ 400)。

    最近の知らせ (core.bell.notices。新しい順に 10 件) を並べ、鈴を開いた日時を今にして印を消す (書くのは services.open_bell = R13)。
    「新」の印は開く前の日時と比べる (開いた後の日時で比べると全部が古い扱いになる)。返す HTML の先頭に hx-swap-oob の印の箱
    (#seizou-bell-badge。数 0) を含め、上の帯の赤い数をその場で消す。
    GET で書く訳: 鈴を開いた = 見た、なので押した口の中で書き、別の POST を増やさない (指示書の「迷ったら開く口の中で書く」)。
    """
    from seizou.core import bell as bell_box
    from seizou.core import services as core_services

    if not request.headers.get("HX-Request"):
        return HttpResponseBadRequest(BELL_NOT_HTMX)
    since = core_services.bell_opened_at(request.user)
    notices = bell_box.notices(request.user, since=since)
    core_services.open_bell(request.user)
    return render(request, "core/_bell_modal.html", {"notices": notices})
