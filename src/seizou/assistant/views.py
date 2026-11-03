"""AI チャットの口 (htmx の POST だけを受ける。画面でない口なので関数の view)。

- ask: 質問を受け、services.answer の答えを吹き出しの部分 (assistant/_messages.html) で返す。
  失敗 (API の文・鍵が無い) も同じ形の吹き出しで 200 を返す (htmx は 4xx・5xx の返事を差し替えないため)。
- clear: 履歴を消し、中身の無い 200 を返す (吹き出しの並びが空になる)。
- approve: 提案のカードの「承認」と「このまま保存」を受け、services の approve が更新した提案のカード
  (assistant/_proposal.html) を返す。失敗 (見つからない・権限・ポカよけ) も同じカードで 200 を返す。
- この module は表に書かない。保存は services の approve が書く道具を呼ぶ。履歴は services の関数を通して session に置く。
- CSRF の見出しは土台の base.html の body の hx-headers が htmx の要求に付ける (R18)。
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_POST

from . import services

_HTMX_ONLY = "AI チャットの窓からの要求だけを受けます。"


@login_required
@require_POST
def ask(request):
    """質問を受けて答え、1 往復を履歴に残し、その往復の吹き出しを返す。"""
    if not request.headers.get("HX-Request"):
        return HttpResponseBadRequest(_HTMX_ONLY)
    question = request.POST.get("question", "")
    result = services.answer(
        question,
        page_url=request.POST.get("page_url", ""),
        screen_name=request.POST.get("screen_name", ""),
        user=request.user,
        history=services.history(request.session),
    )
    exchange = {
        "question": question.strip()[: services.MAX_QUESTION_CHARS],
        "answer": result["text"],
        "links": result["links"],
        "tools": result["tools"],
        "proposals": result["proposals"],
    }
    services.remember(request.session, exchange)
    return render(request, "assistant/_messages.html", {"exchanges": [exchange]})


@login_required
@require_POST
def clear(request):
    """履歴を消す。返す物は中身の無い 200。"""
    if not request.headers.get("HX-Request"):
        return HttpResponseBadRequest(_HTMX_ONLY)
    services.forget(request.session)
    return HttpResponse("")


@login_required
@require_POST
def approve(request):
    """提案を 1 つ保存してみて、その提案のカードを返す (confirm=1 は知らせを人が確かめた印)。"""
    if not request.headers.get("HX-Request"):
        return HttpResponseBadRequest(_HTMX_ONLY)
    proposal = services.approve(
        request.session,
        request.POST.get("proposal_id", ""),
        request.user,
        confirm=request.POST.get("confirm") == "1",
    )
    return render(request, "assistant/_proposal.html", {"proposal": proposal})
