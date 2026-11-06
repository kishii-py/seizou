"""shipping の画面 (02 の出荷)。画面は全部 core の土台を継承する (R15)。

出荷の入力は、土台が検査の層 (受注より多い出荷のポカよけ) を通してから services.ship を呼ぶ。
止めるか、知らせて「このまま保存」か、通すかは会社の設定に従う (画面では数を比べない)。
出荷の編集は services.update_shipment を呼ぶ (数量と出荷日と備考だけ。R01 は検査の層が編集にも当てる)。
発行の納品書に入った出荷は、先に納品書を取り消してから編集する (R08)。
一括出荷の入力は、注文残の盤で選んだ明細をまとめて services.ship_lines に渡す (検査の層は一括出荷の R01。N124)。
model は出荷にする (小窓のテンプレートは model の app の shipping/_form_modal.html から選ばれるため)。

納品書は、出荷を選んでまとめる画面 (services.create_delivery_note) と一覧と詳細を土台の上に置く。
納品書を取り消す画面は services.cancel_delivery_note を呼び、納品書の詳細の「取り消す」から入る。
印刷用の HTML と PDF は画面でない口なので、関数の view にした (connect の受け口と同じ形)。
PDF を作れない環境 (WeasyPrint か OS の pango が無い) では、印刷用の HTML に知らせを付けて返す。
出荷ラベル (1 出荷 = 1 枚) の印刷用の HTML と PDF も関数の view で、出荷の詳細の「ラベル」「PDF」から入る。
帳票の QR の中身 (qr_text) は view が絶対 URL で足す (ラベルは出荷の詳細、納品書は納品書の詳細)。context の関数は request を受けない。
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse

from seizou.core import access
from seizou.core import reports
from seizou.core.views import BaseDetailView, BaseFormView, BaseListView
from seizou.masters.services import active_partners

from . import services
from .forms import DeliveryNoteCancelForm, DeliveryNoteForm, ShipmentBulkForm, ShipmentForm, ShipmentUpdateForm
from .models import DeliveryNote, Shipment
from .reports import (
    DELIVERY_NOTE_TEMPLATE,
    SHIPMENT_LABEL_TEMPLATE,
    delivery_note_context,
    shipment_label_context,
)

#: PDF を作れないときに印刷用の HTML の上に出す知らせ (05 の 6 節の文)
PDF_NOTICE = "PDF の準備がありません。印刷用の画面をお使いください。"


def _customer_choices():
    """出荷と納品書の一覧の得意先の絞りの候補。取引先の種類が得意先か両方で、使用中の物だけ (N135 の active_partners)。並びは取引先の ordering。"""
    partner = DeliveryNote._meta.get_field("customer").related_model
    rows = active_partners().filter(role__in=(partner.ROLE_CUSTOMER, partner.ROLE_BOTH))
    return [(row.pk, str(row)) for row in rows]


class ShipmentListView(BaseListView):
    """出荷の一覧。

    使う人: 出荷 / いつ: 製品ができて送るときと、出荷の記録を見るとき / 何のために: 受注の明細ごとの出荷を見る / 次にすること: 「新規登録」で出荷を記録し、納品書を作る
    """

    model = Shipment
    screen_name = "出荷"
    screen_help = "出荷の一覧。出荷日の新しい順に並びます。得意先と部品の名前を押すと小窓が開きます。"
    columns = (
        "shipment_no",
        ("order_line__order__order_no", "受注番号"),
        ("order_line__order__customer", "得意先"),
        ("order_line__item", "部品"),
        "qty",
        "shipped_at",
        ("delivery_note__note_no", "納品書番号"),
    )
    search_fields = (
        "shipment_no",
        "order_line__order__order_no",
        "order_line__item__part_no",
        "order_line__order__customer__name",
    )
    filters = (
        {"name": "customer", "label": "得意先", "kind": "lookup", "field": "order_line__order__customer", "choices": _customer_choices},
        {"name": "shipped", "label": "出荷日", "kind": "date_range", "field": "shipped_at"},
    )
    ordering = ("-shipped_at", "-id")

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("order_line__order__customer", "order_line__item", "delivery_note")
        )


class ShipmentDetailView(BaseDetailView):
    """出荷の詳細。

    使う人: 出荷 / いつ: 出荷の中身を確かめるときと、直すとき / 何のために: 出荷の項目を見る / 次にすること: 「編集」で直すか、納品書を作る
    """

    model = Shipment
    screen_name = "出荷の詳細"
    screen_help = "出荷の項目。右上の「編集」で数量と出荷日と備考を直せます。発行の納品書に入った出荷は、先に納品書を取り消してから編集します。"
    template_name = "shipping/shipment_detail.html"


class ShipmentCreateView(BaseFormView):
    """出荷の新規登録の入力。

    使う人: 出荷 / いつ: 製品ができて送るとき / 何のために: 受注の明細ごとに出荷を記録する / 次にすること: 納品書を作り、ラベルを印刷する
    """

    model = Shipment
    form_class = ShipmentForm
    service = staticmethod(services.ship)
    template_name = "shipping/shipment_form.html"
    screen_name = "出荷の新規登録"
    screen_help = (
        "受注の明細と数量と出荷日を入れて、出荷を保存します。"
        "受注の残を超える数は、会社の設定に従って止めるか知らせます。"
    )


class ShipmentBulkCreateView(BaseFormView):
    """一括出荷の入力 (注文残の盤で選んだ明細を、同じ出荷日でまとめて出荷する)。

    使う人: 生産管理 / いつ: 同じ日に送る明細がいくつもあるとき / 何のために: 選んだ明細の出荷を 1 回の保存で記録する / 次にすること: 納品書を作り、ラベルを印刷する
    """

    model = Shipment
    form_class = ShipmentBulkForm
    service = staticmethod(services.ship_lines)
    template_name = "shipping/shipment_bulk_form.html"
    screen_name = "一括出荷"
    screen_help = (
        "生産管理ボードで選んだ明細を、同じ出荷日でまとめて出荷します。数量の既定は残の数です。"
        "どれか 1 行が決まりに当たったときは、全部を保存しません。"
    )


class ShipmentUpdateView(BaseFormView):
    """出荷の編集の入力。

    使う人: 出荷 / いつ: 数量や出荷日を直すとき / 何のために: 出荷の項目を直す (納品書に入った物は先に納品書を取り消す) / 次にすること: 出荷の詳細で確かめる
    """

    model = Shipment
    form_class = ShipmentUpdateForm
    service = staticmethod(services.update_shipment)
    screen_name = "出荷の編集"
    screen_help = (
        "数量と出荷日と備考を直します。発行の納品書に入った出荷は、先に納品書を取り消してから直します。"
        "受注の残を超える数は、会社の設定に従って止めるか知らせます。"
    )


class DeliveryNoteListView(BaseListView):
    """納品書の一覧。

    使う人: 出荷 / いつ: 出荷と同時 / 何のために: 得意先に渡す納品書を持つ / 次にすること: 行から詳細へ (印刷用の画面と PDF)。請求は経理
    """

    model = DeliveryNote
    screen_name = "納品書"
    screen_help = "納品書の一覧。発行日の新しい順に並びます。行を押すと、印刷用の画面と PDF の入口がある詳細へ移ります。"
    columns = ("note_no", "customer", "issued_on", "status")
    search_fields = ("note_no", "customer__name")
    filters = (
        {"name": "customer", "label": "得意先", "kind": "lookup", "field": "customer", "choices": _customer_choices},
        {"name": "issued", "label": "発行日", "kind": "date_range", "field": "issued_on"},
        {"name": "status", "label": "状態", "kind": "choice", "field": "status"},
    )
    ordering = ("-issued_on", "-id")

    def get_queryset(self):
        return super().get_queryset().select_related("customer")


class DeliveryNoteDetailView(BaseDetailView):
    """納品書の詳細。

    使う人: 出荷 / いつ: 納品書を印刷するとき / 何のために: 項目とまとめた出荷を見る / 次にすること: 印刷用の画面か PDF を開いて得意先に渡す
    """

    model = DeliveryNote
    screen_name = "納品書の詳細"
    screen_help = "納品書の項目と、まとめた出荷。印刷用の画面と PDF は新しいタブで開きます。"
    tabs = (("出荷", "shipments"),)
    template_name = "shipping/deliverynote_detail.html"


class DeliveryNoteCreateView(BaseFormView):
    """納品書を作る入力。

    使う人: 出荷 / いつ: 出荷と同時 / 何のために: 同じ得意先の出荷を 1 枚の納品書にまとめる / 次にすること: 納品書の詳細から印刷
    """

    model = DeliveryNote
    form_class = DeliveryNoteForm
    service = staticmethod(services.create_delivery_note)
    screen_name = "納品書を作る"
    screen_help = "同じ得意先の出荷を選んで、1 枚の納品書にまとめます。納品書番号は保存のときに付きます。"


class DeliveryNoteCancelView(BaseFormView):
    """納品書を取り消す確かめの入力。

    使う人: 出荷 / いつ: 出荷を直すときと、納品書を作り直すとき / 何のために: 発行の納品書を取り消す / 次にすること: 出荷を直して納品書を作り直す
    """

    model = DeliveryNote
    form_class = DeliveryNoteCancelForm
    service = staticmethod(services.cancel_delivery_note)
    template_name = "shipping/deliverynote_cancel.html"
    screen_name = "納品書を取り消す"
    screen_help = "発行の納品書を取り消します。この納品書の出荷は請求できなくなり、出荷を直してから新しい納品書にまとめ直せます。"


@login_required
def deliverynote_print(request, pk):
    """納品書の印刷用の HTML (ブラウザの印刷で A4 1 枚)。"""
    note = get_object_or_404(DeliveryNote.objects.select_related("customer"), pk=pk)
    context = {
        **delivery_note_context(note),
        "qr_text": request.build_absolute_uri(reverse("shipping:deliverynote_detail", args=[pk])),
        "hide_money": not access.can_see_money(request.user),  # 金額の閲覧が無い人 (on) には単価と金額の枡を空で出す
    }
    return HttpResponse(reports.render_html(DELIVERY_NOTE_TEMPLATE, context))


@login_required
def deliverynote_pdf(request, pk):
    """納品書の PDF。作れなければ、印刷用の HTML の上に知らせを出して返す。"""
    note = get_object_or_404(DeliveryNote.objects.select_related("customer"), pk=pk)
    context = {
        **delivery_note_context(note),
        "qr_text": request.build_absolute_uri(reverse("shipping:deliverynote_detail", args=[pk])),
        "hide_money": not access.can_see_money(request.user),  # 金額の閲覧が無い人 (on) には単価と金額の枡を空で出す
    }
    try:
        data = reports.render_pdf(DELIVERY_NOTE_TEMPLATE, context)
    except reports.PdfUnavailable:
        return HttpResponse(reports.render_html(DELIVERY_NOTE_TEMPLATE, {**context, "pdf_notice": PDF_NOTICE}))
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{note.note_no}.pdf"'
    return response


@login_required
def shipment_label(request, pk):
    """出荷ラベルの印刷用の HTML (ブラウザの印刷で A4 縦の上半分)。QR の中身は出荷の詳細の URL。"""
    shipment = get_object_or_404(
        Shipment.objects.select_related("order_line__order__customer", "order_line__item"), pk=pk
    )
    context = {
        **shipment_label_context(shipment),
        "qr_text": request.build_absolute_uri(reverse("shipping:shipment_detail", args=[pk])),
    }
    return HttpResponse(reports.render_html(SHIPMENT_LABEL_TEMPLATE, context))


@login_required
def shipment_label_pdf(request, pk):
    """出荷ラベルの PDF。作れなければ、印刷用の HTML の上に知らせを出して返す。"""
    shipment = get_object_or_404(
        Shipment.objects.select_related("order_line__order__customer", "order_line__item"), pk=pk
    )
    context = {
        **shipment_label_context(shipment),
        "qr_text": request.build_absolute_uri(reverse("shipping:shipment_detail", args=[pk])),
    }
    try:
        data = reports.render_pdf(SHIPMENT_LABEL_TEMPLATE, context)
    except reports.PdfUnavailable:
        return HttpResponse(reports.render_html(SHIPMENT_LABEL_TEMPLATE, {**context, "pdf_notice": PDF_NOTICE}))
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{shipment.shipment_no}.pdf"'
    return response
