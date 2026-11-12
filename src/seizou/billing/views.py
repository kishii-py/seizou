"""billing の画面 (02 の請求書)。一覧・詳細・作る・取り消す・作り直すは core の土台を継承する (R15)。

class の中に書くのは、土台が上書きを許す属性だけ。保存は services の関数だけが行う (R13)。
印刷用の HTML と PDF は関数の view。PDF を作れない環境 (PdfUnavailable) では、印刷用の HTML に知らせを付けて返す。
入金の記録と取消は土台の入力の画面。保存の後は services が返す入金の _detail (payment_detail = 請求書の詳細へ移るだけの口) へ移る。
請求書の外で受け取った入金 (出荷を指す入金。作者の決め) の記録は ShipmentPaymentCreateView で、payment_detail は出荷の詳細へ移す。
調整 (値引き・振込手数料・相殺・端数・その他。依頼書 N409b) の記録と取消も土台の入力の画面。保存の後は services が返す調整の _detail
(adjustment_detail = 請求書の詳細へ移るだけの口) へ移る。入口は請求書の詳細の入金の枠 (小窓でなく画面の全体で開く)。
一覧の「入金」と「残」は Invoice の method の列で、「残のある物だけ」(絞り込みの枠の印 f_unpaid=1。印の名前は変えない) は残 (合計 − 取消でない入金 + 取消でない調整) が 0 より大きい発行の請求書 = 一部入金も入る
(ダッシュボードの「入金の残っている請求書」と同じ数え方。本人の決め 11-05 09:3x。状態の絞りがあれば、状態はそちらを使う)。
"""

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse

from seizou.core import access
from seizou.core.reports import PdfUnavailable, render_html, render_pdf
from seizou.core.views import BaseDetailView, BaseFormView, BaseListView
from django.db.models import Prefetch

from seizou.core.views import BaseModalView

from . import services
from .forms import (
    AdjustmentCancelForm,
    AdjustmentForm,
    InvoiceCancelForm,
    InvoiceClosingForm,
    InvoiceForm,
    InvoiceReissueForm,
    PaymentCancelForm,
    PaymentForm,
    ShipmentPaymentForm,
)
from .models import Adjustment, Invoice, Payment
from .models import InvoiceLine
from .reports import INVOICE_TEMPLATE, invoice_context

REPORT_TEMPLATE = INVOICE_TEMPLATE
PDF_NOTICE = "PDF の準備がありません。印刷用の画面をお使いください"


def _unpaid_only(invoices):
    """「残のある物だけ」の印の条件 (残 = 合計 − 取消でない入金 + 取消でない調整 が 0 より大きい請求書 = 一部入金も入る)。

    Invoice.balance と、core.dashboard の _invoices_to_be_paid と同じ式 (保存した入金と調整の金額を足し引きするだけ。掛け算と丸めをしない)。
    入金と調整は表ごとの副問い合わせの Sum で足す (結び付けを 2 つ並べると、入金と調整の数だけ行が増えて合計が倍になるため)。
    状態を発行に絞るのは InvoiceListView.get_queryset。
    """
    from django.db.models import OuterRef, Subquery

    money = DecimalField(max_digits=14, decimal_places=2)
    sums = {}
    for name, model, status in (
        ("paid_sum", Payment, Payment.STATUS_RECORDED),
        ("adjusted_sum", Adjustment, Adjustment.STATUS_RECORDED),
    ):
        rows = (
            model.objects.filter(invoice=OuterRef("pk"), status=status)
            .order_by()
            .values("invoice")
            .annotate(total=Sum("amount"))
            .values("total")
        )
        sums[name] = Coalesce(Subquery(rows, output_field=money), Value(Decimal("0")), output_field=money)
    return invoices.annotate(**sums).filter(total__gt=F("paid_sum") - F("adjusted_sum"))


class InvoiceListView(BaseListView):
    """請求書の一覧。

    使う人: 経理 / いつ: 締日の後 / 何のために: 出荷をまとめた請求書を持つ / 次にすること: 「新規登録」か行から詳細へ (印刷)
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
        invoices = super().get_queryset().select_related("customer").prefetch_related("payments", "adjustments")
        if self.flag_on("unpaid"):
            # 入金の有無は印の when (_unpaid_only) が見る。状態の絞り (f_status) に読める値が無いときだけ、状態を発行に絞る (開発の記録 docs/nights/20261104/N120.md・docs/nights/20261104/N127.md)。
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
    # 明細のタブの表が出荷と受注の明細と部品を読むので、3 つを行と一緒に 1 回で引く (開発の記録 docs/nights/20261106/N174.md。表は作者の決め)
    # 一式の行 (出荷が空) も同じ字が出るよう、明細の受注の明細から部品と受注を引く (出荷は空の外部キーを外部結合で引く。作者の決め)
    tabs = (("明細", "lines", ("shipment", "order_line__item", "order_line__order")),)
    template_name = "billing/invoice_detail.html"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("payments", "adjustments")


class InvoiceModalView(BaseModalView):
    """請求書の小窓。

    使う人: 経理 / いつ: 請求書の一覧で行の中身を画面を移らずに確かめたいとき / 何のために: 得意先・発行日・締め期間・合計・残・入金の状態・明細の先頭の数行を見る / 次にすること: 要れば「詳細を見る」で請求書の詳細へ

    請求書の一覧の行の「要点を見る」(目の印。core/_row_summary_link.html) が開く (受け渡しの seizou-部品の6案-20261107/指示書.md の 6。
    本人の決め 11-07 18:2x〜18:4x「5はmodalでいいかな」。形は受注の小窓 OrderModalView と同じ)。
    中身は billing/_modal.html (項目を手で描く。土台の先頭の 8 項目では「合計」と「状態」が切れるため)。読むだけ。
    """

    model = Invoice
    screen_name = "請求書の小窓"
    screen_help = "請求書の要点 (得意先・発行日・締め期間・合計・残・入金の状態・明細) を見るだけの小窓。請求書の一覧の行の目の印から開き、詳細へ移れます。"

    def get_queryset(self):
        # 入金と調整は残と入金の状態 (Invoice.balance・payment_status) が読む。明細は詳細の「明細」のタブと同じ 3 つ (出荷・部品・受注) を一緒に 1 回で読む
        # (一式の行と、出荷を消した出荷の行は出荷が空。小窓の表は出荷番号の写しを読む)。並びは InvoiceLine の Meta の ordering のまま。
        lines = InvoiceLine.objects.select_related("shipment", "order_line__item", "order_line__order")
        return Invoice.objects.select_related("customer").prefetch_related("payments", "adjustments", Prefetch("lines", queryset=lines))


class InvoiceCreateView(BaseFormView):
    """請求書の新規登録の入力。

    使う人: 経理 / いつ: 締日の後 / 何のために: 得意先ごとに出荷をまとめて請求書を作る / 次にすること: 請求書の詳細から印刷

    消費税は services の _tax が、請求書の税率ごとの合計に 1 回だけ計算する (インボイス制度。丸める向きは取引先の消費税の端数の向き。この画面は計算しない)。
    """

    model = Invoice
    form_class = InvoiceForm
    service = staticmethod(services.create_invoice)
    template_name = "billing/invoice_form.html"
    screen_name = "請求書の新規登録"
    screen_help = "得意先を選び、まだ請求書に入っていない出荷をまとめて請求書を作ります。"


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


class InvoiceClosingView(BaseFormView):
    """締日でまとめて請求書を作る入力 (締めの一括の 2 段目。依頼書 N402b)。入口は請求書の一覧の上の「締日でまとめて作る」。

    使う人: 経理 / いつ: 締日の後 / 何のために: 年月と締日を選び、その締日の得意先の請求書を 1 回でまとめて作る / 次にすること: 請求書の一覧で作った請求書を開いて印刷する

    欄の下の表 (billing/_invoice_closing_card.html) は form の plan (services.closing_plan) を描く。保存は services.create_closing_invoices。
    返す物は作った請求書の list (ClosedInvoices) なので、土台は保存の後に請求書の一覧へ移し、返す物の saved_message・saved_note・saved_level で知らせを出す。
    """

    model = Invoice
    form_class = InvoiceClosingForm
    service = staticmethod(services.create_closing_invoices)
    template_name = "billing/invoice_closing.html"
    screen_name = "締日でまとめて請求書を作る"
    screen_help = "年月と締日を選び、その日が締日の得意先の請求書をまとめて作ります。作る得意先と、作らない得意先とその訳を表で確かめてから押します。"


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


class ShipmentPaymentCreateView(BaseFormView):
    """請求書の外で受け取った入金の記録の入力。

    使う人: 経理 / いつ: 出荷の代金を請求書の外で受け取ったとき (現金など) / 何のために: 受け取った入金を残し、その出荷を請求書の候補から外す / 次にすること: 請求書の新規登録で残りの出荷を請求する

    対象は URL の pk の出荷 (出荷の表は入金の外部キーの先として引く。shipping の model を import しない)。入口は請求書の新規登録の出荷の行の
    「請求しない (別に受け取った)」(小窓)。テンプレートは土台の入力の画面と小窓のまま (billing/payment_form.html は請求書の残の 1 行を出すので使わない)。
    保存の後は services が返す入金の _detail (payment_detail) が出荷の詳細へ移す。
    """

    model = Payment._meta.get_field("shipment").related_model
    form_class = ShipmentPaymentForm
    service = staticmethod(services.record_outside_payment)
    screen_name = "請求書の外で受け取った入金の記録"
    screen_help = "請求書を作らずに受け取った出荷の代金 (現金など) の日付・金額・方法を記録します。記録した出荷は請求書の新規登録に並ばなくなり、ダッシュボードの今月の入金に入ります。"


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


class AdjustmentCreateView(BaseFormView):
    """調整の記録の入力 (依頼書 N409b)。入口は請求書の詳細の入金の枠の「調整を記録する」。

    使う人: 経理 / いつ: 振込手数料や値引きで請求書の残が消えないとき / 何のために: 調整を記録して請求書の残を消す / 次にすること: 請求書の詳細の入金の枠で残を確かめる

    頭の 1 行 (billing/_adjustment_create_card.html) は合計・入金済み・調整・残を読むので、入金と調整を請求書と一緒に引く。
    """

    model = Invoice
    form_class = AdjustmentForm
    service = staticmethod(services.record_adjustment)
    template_name = "billing/adjustment_form.html"
    screen_name = "調整の記録"
    screen_help = "請求書の残を、値引き・振込手数料・相殺・端数で増やすか減らします。残を減らすときはマイナスで入れます。"

    def get_queryset(self):
        return Invoice.objects.select_related("customer").prefetch_related("payments", "adjustments")


class AdjustmentCancelView(BaseFormView):
    """調整を取り消す入力 (依頼書 N409b)。入口は請求書の詳細の入金の枠の調整の行の「取り消す」。

    使う人: 経理 / いつ: 調整を間違えて記録したとき / 何のために: 調整を取消にして残を戻す (記録は履歴に残る) / 次にすること: 正しい調整を記録し直す

    前回の残に数えた調整の取消は、検査の層の R36 が黄の知らせ (知らせる) か赤の知らせ (止める) を出す (土台の check_words の字)。
    """

    model = Adjustment
    form_class = AdjustmentCancelForm
    service = staticmethod(services.cancel_adjustment)
    template_name = "billing/adjustment_cancel.html"
    screen_name = "調整を取り消す"
    screen_help = "記録した調整を取消にします。取消の記録は履歴に残り、請求書の残の金額に戻ります。"


@login_required
def payment_detail(request, pk):
    """保存と取消の後に請求書の詳細へ移るだけの口 (土台は保存の後に、services が返す Payment の _detail へ移す)。

    請求書の外で受け取った入金 (請求書が空。作者の決め) は、その出荷の詳細へ移す (請求書の無い入金で落ちないため)。
    """
    payment = get_object_or_404(Payment, pk=pk)
    if payment.invoice_id is None:
        return HttpResponseRedirect(reverse("shipping:shipment_detail", args=[payment.shipment_id]))
    return HttpResponseRedirect(reverse("billing:invoice_detail", args=[payment.invoice_id]))


@login_required
def adjustment_detail(request, pk):
    """保存と取消の後に請求書の詳細の入金の枠へ移るだけの口 (土台は保存の後に、services が返す Adjustment の _detail へ移す。payment_detail と同じ形)。"""
    adjustment = get_object_or_404(Adjustment, pk=pk)
    return HttpResponseRedirect(reverse("billing:invoice_detail", args=[adjustment.invoice_id]))


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
