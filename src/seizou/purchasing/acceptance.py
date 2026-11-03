"""発注の詳細の検収の「送り直す」の口。

画面でない口なので関数の view (R15 は class だけを見る)。書くのは connect.services.send_acceptance だけ (R13)。
権限は access.require_write (action は connect.send_acceptance = core/access.py の購買の種類)。
部署の門は置かない (発注の詳細は部署で開けるかを見ないので、その中のボタンも同じ扱い)。
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

from .models import Receipt

#: 権限の表の action の名前 (字で書かず関数から作る = 名前がずれない)
ACTION = access.action_name(connect_services.send_acceptance)


@login_required
@require_POST
def resend_acceptance(request, pk):
    """受入の検収を相手へ送り直し、発注の詳細へ戻る。送れたかどうかは知らせ (toast) に出す。"""
    receipt = get_object_or_404(Receipt.objects.select_related("line__purchase_order"), pk=pk)
    back = HttpResponseRedirect(
        reverse("purchasing:purchaseorder_detail", args=[receipt.line.purchase_order_id])
    )
    try:
        access.require_write(request.user, ACTION)
    except access.AccessDenied as exc:
        # str(exc) は「権限: …」の形なので、知らせには文 (message) だけを出す。
        messages.error(request, exc.message)
        return back
    saved = connect_services.send_acceptance(receipt, request.user)
    if saved.acceptance_sent_at is not None:
        messages.success(request, "検収を送りました。")
    else:
        messages.error(request, saved.acceptance_response)
    return back
