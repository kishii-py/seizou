"""billing の画面 (02 の請求書)。一覧・詳細・作る・取り消す・作り直すは core の土台を継承する (R15)。

class の中に書くのは、土台が上書きを許す属性だけ。保存は services の関数だけが行う (R13)。
印刷用の HTML と PDF は関数の view。PDF を作れない環境 (PdfUnavailable) では、印刷用の HTML に知らせを付けて返す。
入金の記録と取消は土台の入力の画面。保存の後は services が返す入金の _detail (payment_detail = 請求書の詳細へ移るだけの口) へ移る。
一覧の「入金」と「残」は Invoice の method の列で、「残のある物だけ」(絞り込みの枠の印 f_unpaid=1。印の名前は変えない) は取消でない入金の合計が合計に足りない発行の請求書 = 一部入金も入る
(ダッシュボードの「入金の残っている請求書」と同じ数え方。本人の決め 11-05 09:3x。状態の絞りがあれば、状態はそちらを使う)。
"""

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse

from seizou.core import access
from seizou.core.reports import PdfUnavailable, render_html, render_pdf
from seizou.core.views import BaseDetailView, BaseFormView, BaseListView

from . import services
from .forms import InvoiceCancelForm, InvoiceForm, InvoiceReissueForm, PaymentCancelForm, PaymentForm
from .models import Invoice, Payment
from .reports import invoice_context

REPORT_TEMPLATE = "billing/invoice.html"
PDF_NOTICE = "PDF の準備がありません。印刷用の画面をお使いください"


def _unpaid_only(invoices):
    """「残のある物だけ」の印の条件 (取消でない入金の合計が合計に足りない請求書 = 一部入金も入る)。

    core.dashboard の _invoices_to_be_paid と同じ数え方 (保存した入金の金額を足すだけ。掛け算と丸めをしない)。
    状態を発行に絞るのは InvoiceListView.get_queryset。
    """
    paid = Coalesce(
        Sum("payments__amount", filter=Q(payments__status=Payment.STATUS_RECORDED)),
        Value(Decimal("0")),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    return invoices.annotate(paid_sum=paid).filter(paid_sum__lt=F("total"))


class InvoiceListView(BaseListView):
    """請求書の一覧。

    使う人: 経理 / いつ: 締日の後 / 何のために: 納品書の出た出荷をまとめた請求書を持つ / 次にすること: 「新規登録」か行から詳細へ (印刷)
    """

    model = Invoice
    screen_name = "請求書"
    screen_help = "得意先ごとに出荷をまとめた請求書。行を押すと詳細へ進み、そこから印刷用の画面を開けます。"
    columns = ("invoice_no", "customer", "issued_on", "period_from", "period_to", "total", ("payment_status_label", "入金"), {"name": "balance", "label": "残", "kind": "money"}, "status")
    search_fields = ("invoice_no", "customer__name", "customer__code")
    filters = (
        {"name": "status", "label": "状態", "kind": "choice", "field": "status"},
        {"name": "customer", "label": "得意先", "kind": "lookup", "field": "customer"},
        {"name": "issued", "label": "発行日", "kind": "date_range", "field": "issued_on"},
        {"name": "unpaid", "label": "残のある物だけ", "kind": "flag", "when": _unpaid_only},
    )
    ordering = ("-issued_on", "-id")
    template_name = "billing/invoice_list.html"

    def get_queryset(self):
        invoices = super().get_queryset().select_related("customer").prefetch_related("payments")
        if self.flag_on("unpaid"):
            # 入金の有無は印の when (_unpaid_only) が見る。状態の絞り (f_status) に読める値が無いときだけ、状態を発行に絞る (N120・N127)。
            if not self.filter_chosen("status"):
                invoices = invoices.filter(status=Invoice.STATUS_ISSUED)
        return invoices


class InvoiceDetailView(BaseDetailView):
    """請求書の詳細。

    使う人: 経理 / いつ: 請求書を印刷するとき / 何のために: 項目と明細を見る / 次にすること: 印刷用の画面か PDF を開いて得意先に送る
    """

    model = Invoice
    screen_name = "請求書の詳細"
    screen_help = "請求書の項目と明細。印刷用の画面と PDF は新しいタブで開きます。"
    # 明細のタブの表が出荷と受注の明細と部品を読むので、3 つを行と一緒に 1 回で引く (N174。表は C17)
    # 一式の行 (出荷が空) も同じ字が出るよう、明細の受注の明細から部品と受注を引く (出荷は空の外部キーを外部結合で引く。決め PRICE-1d)
    tabs = (("明細", "lines", ("shipment", "order_line__item", "order_line__order")),)
    template_name = "billing/invoice_detail.html"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("payments")


class InvoiceCreateView(BaseFormView):
    """請求書の新規登録の入力。

    使う人: 経理 / いつ: 締日の後 / 何のために: 得意先ごとに納品書の出た出荷をまとめて請求書を作る / 次にすること: 請求書の詳細から印刷

    消費税は services の _tax が、請求書の税率ごとの合計に 1 回だけ計算する (インボイス制度。丸める向きは取引先の消費税の端数の向き。この画面は計算しない)。
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


class InvoiceReissueView(BaseFormView):
    """請求書を作り直す入力。入口は、請求書の新規登録で同じ締め期間の 2 枚目が止まったときの右上の知らせのボタンだけ。

    使う人: 経理 / いつ: 同じ締め期間の請求書を作ろうとして止まったとき / 何のために: 前の請求書を取り消し、期間の全部の出荷で 1 枚に作り直す / 次にすること: 請求書の詳細から印刷
    """

    model = Invoice
    form_class = InvoiceReissueForm
    service = staticmethod(services.reissue_invoice)
    template_name = "billing/invoice_reissue.html"
    screen_name = "請求書を作り直す"
    screen_help = "同じ締め期間の請求書を取り消し、入っていた出荷と期間の中で残っていた出荷で 1 枚に作り直します。"


class PaymentCreateView(BaseFormView):
    """入金の記録の入力。

    使う人: 経理 / いつ: 得意先から入金があったとき / 何のために: 請求書ごとに入金の日付と金額と方法を記録して残を更新する / 次にすること: 請求書の詳細の「入金」の枠で残を確かめる
    """

    model = Invoice
    form_class = PaymentForm
    service = staticmethod(services.record_payment)
    template_name = "billing/payment_form.html"
    screen_name = "入金の記録"
    screen_help = "請求書に対する入金の日付・金額・方法を記録します。請求額との差は残として残り、多い入金も記録できます。"


class PaymentCancelView(BaseFormView):
    """入金を取り消す入力。

    使う人: 経理 / いつ: 入金を間違えて記録したとき / 何のために: 入金を取消にして残を戻す (記録は履歴に残る) / 次にすること: 正しい入金を記録し直す
    """

    model = Payment
    form_class = PaymentCancelForm
    service = staticmethod(services.cancel_payment)
    template_name = "billing/payment_cancel.html"
    screen_name = "入金を取り消す"
    screen_help = "記録した入金を取消にします。取消の記録は履歴に残り、請求書の残の金額に戻ります。"


@login_required
def payment_detail(request, pk):
    """保存と取消の後に請求書の詳細へ移るだけの口 (土台は保存の後に、services が返す Payment の _detail へ移す)。"""
    payment = get_object_or_404(Payment, pk=pk)
    return HttpResponseRedirect(reverse("billing:invoice_detail", args=[payment.invoice_id]))


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
