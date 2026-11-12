"""受信した注文の詳細の注文回答の「送り直す」の口。

画面でない口なので関数の view (R15 は class だけを見る)。書くのは connect.services.send_reply だけ (R13)。
権限は access.require_write。action は connect.send_reply (受注の種類の編集の権限。金額に関わる操作ではない)。
送り直しは、すでに決まった答えを同じ中身でもう一度送るだけなので、受注を編集できる人なら、金額の閲覧が無くても送り直せる
(本人の決め 11-12 14:3x。前は承認と同じ connect.accept で、金額の閲覧の無い人は送り直せなかった)。
部署の門は置かない (検収と出荷の知らせの送り直しと同じ扱い)。
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
from .models import Inbox

#: 権限の表の action の名前 (字で書かず関数から作る = 名前がずれない)
ACTION = access.action_name(services.send_reply)


@login_required
@require_POST
def resend_reply(request, pk):
    """注文回答を相手へ送り直し、受信した注文の詳細へ戻る。送れたかどうかは知らせ (toast) に出す。"""
    inbox = get_object_or_404(Inbox, pk=pk)
    back = HttpResponseRedirect(reverse("connect:inbox_detail", args=[inbox.pk]))
    try:
        access.require_write(request.user, ACTION)
    except access.AccessDenied as exc:
        # str(exc) は「権限: …」の形なので、知らせには文 (message) だけを出す。
        messages.error(request, exc.message)
        return back
    try:
        saved = services.send_reply(inbox, request.user)
    except ValueError as exc:
        # 承認か断りをする前の受信した注文 (services.MESSAGE_REPLY_BEFORE_DECISION)。
        messages.error(request, str(exc))
        return back
    if saved.reply_sent_at is not None:
        messages.success(request, "注文回答を送りました。")
    else:
        messages.error(request, saved.reply_response)
    return back
