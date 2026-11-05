"""受信した注文の詳細の「差出人を選ぶ」の口。

画面でない口なので関数の view (R15 は class だけを見る)。書くのは connect.services.choose_sender だけ (R13)。
権限は access.require_write で、action は承認と同じ connect.accept (差出人を選ぶのは承認の前の手なので、承認できる人が選ぶ。
core/access.py に行を足さない)。
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST

from seizou.core import access

from . import services
from .forms import SenderForm
from .models import Inbox

#: 権限の表の action の名前 (字で書かず関数から作る = 名前がずれない)
ACTION = access.action_name(services.accept)


@login_required
@require_POST
def choose_sender(request, pk):
    """差出人の取引先を選び、受信した注文の詳細へ戻る。選べたかどうかは知らせ (toast) に出す。"""
    inbox = get_object_or_404(Inbox, pk=pk)
    back = HttpResponseRedirect(reverse("connect:inbox_detail", args=[inbox.pk]))
    try:
        access.require_write(request.user, ACTION)
    except access.AccessDenied as exc:
        # str(exc) は「権限: …」の形なので、知らせには文 (message) だけを出す。
        messages.error(request, exc.message)
        return back
    form = SenderForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return back
    partner = form.cleaned_data["partner"]
    try:
        services.choose_sender(inbox, partner, request.user)
    except ValueError as exc:
        # 直接の行 (R12)・「未処理」でない行・得意先でない取引先。
        messages.error(request, str(exc))
        return back
    messages.success(request, f"差出人を {partner.name} にしました。「受け入れる」で受注にできます。")
    return back
