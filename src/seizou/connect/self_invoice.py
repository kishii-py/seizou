"""受けた仕入明細の詳細の仕入明細回答の「送り直す」の口 (作者の決め)。

画面でない口なので関数の view (R15 は class だけを見る)。書くのは connect.services.send_self_invoice_reply だけ (R13)。
権限は access.require_write。action は答えると同じ connect.answer_self_invoice (注文回答の送り直しが承認と同じ action を使う形)。
部署の門は置かない (検収と出荷の知らせと注文回答の送り直しと同じ扱い)。
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
from .models import ReceivedSelfInvoice

#: 権限の表の action の名前 (字で書かず関数から作る = 名前がずれない)
ACTION = access.action_name(services.answer_self_invoice)


@login_required
@require_POST
def resend_self_invoice_reply(request, pk):
    """仕入明細回答を得意先へ同じ番号で送り直し、受けた仕入明細の詳細へ戻る。送れたかどうかは知らせ (toast) に出す。"""
    received = get_object_or_404(ReceivedSelfInvoice, pk=pk)
    back = HttpResponseRedirect(reverse("connect:receivedselfinvoice_detail", args=[received.pk]))
    try:
        access.require_write(request.user, ACTION)
    except access.AccessDenied as exc:
        messages.error(request, exc.message)
        return back
    try:
        saved = services.send_self_invoice_reply(received, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return back
    if saved.reply_sent_at is not None:
        messages.success(request, "仕入明細回答を送りました。")
    else:
        messages.error(request, saved.reply_response)
    return back
