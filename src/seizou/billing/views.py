"""billing の画面 (02 の請求書)。一覧・詳細・作る・取り消すは core の土台を継承する (R15)。

class の中に書くのは、土台が上書きを許す属性だけ。保存は services の関数だけが行う (R13)。
印刷用の HTML と PDF は関数の view。PDF を作れない環境 (PdfUnavailable) では、印刷用の HTML に知らせを付けて返す。
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from seizou.core import access
from seizou.core.reports import PdfUnavailable, render_html, render_pdf
from seizou.core.views import BaseDetailView, BaseFormView, BaseListView

from . import services
from .forms import InvoiceCancelForm, InvoiceForm
from .models import Invoice
from .reports import invoice_context

REPORT_TEMPLATE = "billing/invoice.html"
PDF_NOTICE = "PDF の準備がありません。印刷用の画面をお使いください"


class InvoiceListView(BaseListView):
    """請求書の一覧。

    使う人: 経理 / いつ: 締日の後 / 何のために: 納品書の出た出荷をまとめた請求書を持つ / 次にすること: 「新規登録」か行から詳細へ (印刷)
    """

    model = Invoice
    screen_name = "請求書"
    screen_help = "得意先ごとに出荷をまとめた請求書。行を押すと詳細へ進み、そこから印刷用の画面を開けます。"
    columns = ("invoice_no", "customer", "issued_on", "period_from", "period_to", "total", "status")
    search_fields = ("invoice_no", "customer__name", "customer__code")
    ordering = ("-issued_on", "-id")


class InvoiceDetailView(BaseDetailView):
    """請求書の詳細。

    使う人: 経理 / いつ: 請求書を印刷するとき / 何のために: 項目と明細を見る / 次にすること: 印刷用の画面か PDF を開いて得意先に送る
    """

    model = Invoice
    screen_name = "請求書の詳細"
    screen_help = "請求書の項目と明細。印刷用の画面と PDF は新しいタブで開きます。"
    tabs = (("明細", "lines"),)
    template_name = "billing/invoice_detail.html"


class InvoiceCreateView(BaseFormView):
    """請求書の新規登録の入力。

    使う人: 経理 / いつ: 締日の後 / 何のために: 得意先ごとに納品書の出た出荷をまとめて請求書を作る / 次にすること: 請求書の詳細から印刷
    """

    model = Invoice
    form_class = InvoiceForm
    service = staticmethod(services.create_invoice)
    template_name = "billing/invoice_form.html"
    screen_name = "請求書の新規登録"
    screen_help = "得意先を選び、納品書の出た出荷をまとめて請求書を作ります。"


class InvoiceCancelView(BaseFormView):
    """請求書を取り消す確かめの入力。

    使う人: 経理 / いつ: 請求を作り直すとき / 何のために: 発行の請求書を取り消す / 次にすること: 請求書を作り直す
    """

    model = Invoice
    form_class = InvoiceCancelForm
    service = staticmethod(services.cancel_invoice)
    template_name = "billing/invoice_cancel.html"
    screen_name = "請求書を取り消す"
    screen_help = "発行の請求書を取り消します。明細は残ります。"


@login_required
def invoice_print(request, pk):
    """印刷用の HTML (新しいタブで開き、ブラウザで印刷する)。"""
    refused = access.refuse_money_document(request, "請求書")
    if refused is not None:
        return refused
    invoice = get_object_or_404(Invoice, pk=pk)
    return HttpResponse(render_html(REPORT_TEMPLATE, invoice_context(invoice)))


@login_required
def invoice_pdf(request, pk):
    """PDF。作れない環境では、印刷用の HTML の上に知らせを出して返す。"""
    refused = access.refuse_money_document(request, "請求書")
    if refused is not None:
        return refused
    invoice = get_object_or_404(Invoice, pk=pk)
    context = invoice_context(invoice)
    try:
        data = render_pdf(REPORT_TEMPLATE, context)
    except PdfUnavailable:
        return HttpResponse(render_html(REPORT_TEMPLATE, {**context, "pdf_notice": PDF_NOTICE}))
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{invoice.invoice_no}.pdf"'
    return response
