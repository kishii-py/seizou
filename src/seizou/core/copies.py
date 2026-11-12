"""帳票の写し (core.ReportCopy。R34) を開く 2 つの口。core.manual と同じく、関数の view を持つ core の module。

口 (core の道。core/urls.py):
  /copies/<pk>/     report_copy      写しの HTML の字をそのまま返す。
  /copies/<pk>/pdf/ report_copy_pdf  写しの HTML の字から PDF を作って返す。作れなければ、写しの HTML に知らせの 1 行を入れて返す。
  入口は詳細の画面の「発行の時の写し」の枠 (core/_report_copies.html。詳細の土台が、写しを持つ model の詳細でだけ写しを読む)。

写しは印刷用の画面と別の口で開く。請求書・納品書・発注書の印刷用の画面と PDF の口は今の記録で刷り、写しの口は発行した時
(発注は作った時と変えた時。版ごと) に残した字を返す。

門: どちらの口もログインが要り、金額の閲覧の門 (core.access.refuse_money_document) を通す。断る文は今の門の文のまま
(「<帳票の名前>の写しは金額が主な内容のため、…」)。部署の判定は通さない (印刷用の画面と同じ。詳細・小窓・候補の口は部署で絞らない作り
= core/views/base.py の opens_by_department の注)。
訳: 写しは単価と金額をいつも含み、空にできないため。納品書の印刷用の画面は金額の閲覧の無い人に単価と金額の枡を空で出すが、
写しは発行の時の字をそのまま返すので、納品書の写しも門を通す。出所 = 依頼書 N410b (R34 の 2 段目)。変えてよい条件 = 本人が決め直したとき
(金額を隠した写しも残す、と決めたときなど)。

PDF: 開く時に写しの HTML から作る (core.reports.render_html_pdf。発行の処理では PDF を作らない = R34)。字と数は発行の時のまま。
見た目の CSS とフォントとロゴは、写しの HTML が URL で指すので、開く時のファイルを読む。

表に書かない。写しを直す口と消す口は作らない (R34)。
"""

import re

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from seizou.core import access, reports
from seizou.core.models import ReportCopy

#: PDF を作れない置き場で、写しの HTML の <body> の直後に入れる知らせの 1 行 (返す時だけ入れ、表の字は変えない)
PDF_NOTICE_HTML = '<p role="alert">PDF の準備がありません。写しの画面をお使いください</p>'

_BODY_START = re.compile(r"<body\b[^>]*>", re.I)


def _copy_or_refusal(request, pk):
    """(写し, None) か、金額の門が断るときは (None, 403 の応答)。写しが無ければ 404。"""
    copy = get_object_or_404(ReportCopy, pk=pk)
    refused = access.refuse_money_document(request, f"{copy.title}の写し")
    if refused is not None:
        return None, refused
    return copy, None


@login_required
def report_copy(request, pk):
    """帳票の写しの HTML (発行の時の字のまま。新しいタブで開き、ブラウザで印刷できる)。金額の門を通す。読むだけ。"""
    copy, refused = _copy_or_refusal(request, pk)
    if refused is not None:
        return refused
    return HttpResponse(copy.html, content_type="text/html; charset=utf-8")


@login_required
def report_copy_pdf(request, pk):
    """帳票の写しの PDF (写しの HTML から開く時に作る)。作れない置き場では、写しの HTML の <body> の直後に知らせの 1 行を入れて返す。読むだけ。"""
    copy, refused = _copy_or_refusal(request, pk)
    if refused is not None:
        return refused
    try:
        data = reports.render_html_pdf(copy.html)
    except reports.PdfUnavailable:
        return HttpResponse(with_pdf_notice(copy.html), content_type="text/html; charset=utf-8")
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{copy.object_text}-{copy.revision}.pdf"'
    return response


def with_pdf_notice(html):
    """写しの HTML の字の <body> の直後に PDF_NOTICE_HTML を入れた字。<body> が無ければ頭に入れる。表の字は変えない (返す字だけ)。"""
    match = _BODY_START.search(html)
    if match is None:
        return PDF_NOTICE_HTML + html
    return html[: match.end()] + PDF_NOTICE_HTML + html[match.end() :]
