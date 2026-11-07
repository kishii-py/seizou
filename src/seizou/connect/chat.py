"""会話 (受注と発注の 1 件ごとの相手とのやり取り。C104) の「送り直す」の口。

画面でない口なので関数の view (R15 は class だけを見る。connect/reply.py の resend_reply と同じ形)。書くのは connect.services.resend_chat_message だけ (R13)。
権限は access.require_write。action は会話に書く画面と同じ connect.post_chat_message (core/access.py の FREE_ACTIONS = 編集の権限を問わない。
決め C104-1 (11-07 06:26)「取引先との会話を書ける人を編集の権限では絞らない」)。部署の門は置かない (検収と注文回答の送り直しと同じ扱い)。
htmx の要求には本文の無い 204 に HX-Trigger の seizou:saved (右上の知らせ。届けば緑、届かなければ黄) を付けて返し、会話の枠は seizou:refresh で取り直す。
htmx でない要求 (JS の無いとき) は messages に積んで、結び付いた詳細へ移る。
会話の添付 (C104 のその 2) の口も置く: 取りに行く口 fetch_chat_file (受けた側。相手の seizou へ取りに行き、添付の 1 行の断片を返す) と、
開く口 chat_file_download (ログインした人にだけファイルを返す。attachments/views.py の attachment_download と同じ形)。
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from seizou.attachments import services as attachment_services
from seizou.core import access

from . import services
from .models import ChatFile, ChatMessage

#: 権限の表の action の名前 (字で書かず関数から作る = 名前がずれない。会話に書く画面と同じ)
ACTION = access.action_name(services.post_chat_message)


def _detail_url(message) -> str:
    """言葉の結び付いた詳細の道 (発注の詳細 → 受注の詳細 → 受信した注文の詳細の順に引ける物)。引けなければ /。"""
    try:
        if message.purchase_order_id is not None:
            return reverse("purchasing:purchaseorder_detail", args=[message.purchase_order_id])
        if message.inbox is not None and message.inbox.order_id is not None:
            return reverse("orders:order_detail", args=[message.inbox.order_id])
        if message.inbox_id is not None:
            return reverse("connect:inbox_detail", args=[message.inbox_id])
    except NoReverseMatch:
        pass
    return "/"


def _refused(request, message, text):
    """断った字を右上の赤い知らせ (messages.error) に積み、結び付いた詳細へ移す (htmx なら HX-Redirect)。"""
    messages.error(request, text)
    url = _detail_url(message)
    if request.headers.get("HX-Request"):
        # 入力の土台の form_valid と同じく 200 と HX-Redirect (htmx が移る)。
        response = HttpResponse(status=200)
        response["HX-Redirect"] = url
        return response
    return HttpResponseRedirect(url)


@login_required
@require_POST
def resend_chat(request, pk):
    """相手に届いていない自社の言葉を送り直す。送れたかどうかは右上の知らせ (toast) に出す。"""
    message = get_object_or_404(ChatMessage.objects.select_related("inbox"), pk=pk, direction=ChatMessage.DIRECTION_SENT)
    try:
        access.require_write(request.user, ACTION)
    except access.AccessDenied as exc:
        # str(exc) は「権限: …」の形なので、知らせには文 (message) だけを出す。
        return _refused(request, message, exc.message)
    try:
        saved = services.resend_chat_message(message, request.user)
    except ValueError as exc:
        return _refused(request, message, str(exc))
    level = "warning" if getattr(saved, "saved_level", "") == "warning" else "success"
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=204)
        # JSON は既定の ensure_ascii のまま (見出しの値は latin-1 の字だけ。入力の土台の saved_in_modal と同じ)。
        response["HX-Trigger"] = json.dumps({"seizou:saved": {"message": saved.saved_message, "level": level}})
        return response
    if level == "warning":
        messages.warning(request, saved.saved_message)
    else:
        messages.success(request, saved.saved_message)
    return HttpResponseRedirect(_detail_url(message))


@login_required
@require_POST
def fetch_chat_file(request, pk):
    """相手から届いた言葉の添付を相手の seizou へ取りに行き、添付の 1 行の断片 (connect/_chat_file.html) を 200 で返す (hx-swap="outerHTML")。

    画面が開いた時の自動の取りに行きでは知らせを出さない (人の操作でないため)。POST の manual=1 (「取り直す」のボタン) のときだけ
    HX-Trigger の seizou:saved (受け取った = 緑、まだ = 黄) も付ける。もう受け取った・自社が付けた添付 (ValueError) のときは、
    取り直した今の行の断片をそのまま返し、知らせは出さない (2 つのタブから押して片方が先に受け取った時に 500 にしないため)。
    権限で断られたときも今の行の断片を返し、manual=1 なら赤の知らせの字を seizou:saved に載せない (黄で理由を出す)。
    """
    chat_file = get_object_or_404(ChatFile, pk=pk, message__direction=ChatMessage.DIRECTION_RECEIVED)
    manual = request.POST.get("manual") == "1"
    notice = None
    try:
        access.require_write(request.user, ACTION)
        saved = services.fetch_chat_file(chat_file, request.user)
    except access.AccessDenied as exc:
        saved = ChatFile.objects.get(pk=chat_file.pk)
        notice = ("warning", exc.message)
    except ValueError:
        saved = ChatFile.objects.get(pk=chat_file.pk)
        manual = False
    else:
        if saved.fetched_at is not None:
            notice = ("success", "添付を受け取りました。")
        else:
            notice = ("warning", f"添付をまだ受け取っていません。{saved.fetch_response}")
    response = render(request, "connect/_chat_file.html", {"file": saved, "message": saved.message})
    if manual and notice is not None:
        response["HX-Trigger"] = json.dumps({"seizou:saved": {"message": notice[1], "level": notice[0]}})
    return response


@never_cache
@login_required
def chat_file_download(request, pk):
    """会話の添付のファイルを返す。PNG・JPEG・WebP・PDF はブラウザの中で開き、ほかはダウンロード (attachments.services.opens_inline)。

    ファイル名は元の名前、形式は表の値。無い番号・まだ受け取っていない (ファイルが空)・storage にファイルが無いときは 404。
    """
    chat_file = get_object_or_404(ChatFile, pk=pk)
    if not chat_file.file:
        raise Http404("ファイルがまだありません。")
    try:
        handle = chat_file.file.open("rb")
    except FileNotFoundError:
        raise Http404("ファイルが見つかりません。") from None
    return FileResponse(
        handle,
        as_attachment=not attachment_services.opens_inline(chat_file.name),
        filename=chat_file.name,
        content_type=chat_file.mime,
    )
