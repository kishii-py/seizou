"""仕入明細の詳細の「送り直す」の口 (作者の決め)。

画面でない口なので関数の view (R15 は class だけを見る)。書くのは connect.services.send_self_invoice だけ (R13)。
権限は access.require_write (action は connect.send_self_invoice = core/access.py の発注の種類)。
部署の門は置かない (仕入明細の詳細は部署で開けるかを見ないので、その中のボタンも同じ扱い。検収の送り直しと同じ形)。
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST

from seizou.connect import services as connect_services
from seizou.core import access

from .models import SelfInvoice

#: 権限の表の action の名前 (字で書かず関数から作る = 名前がずれない)
ACTION = access.action_name(connect_services.send_self_invoice)


@login_required
@require_POST
def resend_self_invoice(request, pk):
    """仕入明細の今の版を同じ番号で相手へ送り直し、仕入明細の詳細へ戻る。送れたかどうかは知らせ (toast) に出す。"""
    self_invoice = get_object_or_404(SelfInvoice, pk=pk)
    back = HttpResponseRedirect(reverse("purchasing:selfinvoice_detail", args=[self_invoice.pk]))
    try:
        access.require_write(request.user, ACTION)
    except access.AccessDenied as exc:
        messages.error(request, exc.message)
        return back
    saved = connect_services.send_self_invoice(self_invoice, request.user)
    if saved.sent_at is not None:
        messages.success(request, "仕入明細を送りました。")
    else:
        messages.error(request, saved.response)
    return back
