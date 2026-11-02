"""shipping の画面 (02 の出荷)。画面は全部 core の土台を継承する (R15)。

出荷の入力は、土台が検査の層 (受注より多い出荷のポカよけ) を通してから services.ship を呼ぶ。
止めるか、知らせて「このまま保存」か、通すかは会社の設定に従う (画面では数を比べない)。
出荷を直す画面は作らない (出荷を直す services の関数が無い)。

納品書は、出荷を選んでまとめる画面 (services.create_delivery_note) と一覧と詳細を土台の上に置く。
印刷用の HTML と PDF は画面でない口なので、関数の view にした (connect の受け口と同じ形)。
PDF を作れない環境 (WeasyPrint か OS の pango が無い) では、印刷用の HTML に知らせを付けて返す。
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from seizou.core import reports
from seizou.core.views import BaseDetailView, BaseFormView, BaseListView

from . import services
from .forms import DeliveryNoteForm, ShipmentForm
from .models import DeliveryNote, Shipment
from .reports import DELIVERY_NOTE_TEMPLATE, delivery_note_context

#: PDF を作れないときに印刷用の HTML の上に出す知らせ (05 の 6 節の文)
PDF_NOTICE = "PDF の準備がありません。印刷用の画面をお使いください。"


class ShipmentListView(BaseListView):
    model = Shipment
    screen_name = "出荷"
    screen_help = "出荷の一覧。出荷日の新しい順に並びます。得意先と品目の名前を押すと小窓が開きます。"
    columns = (
        "shipment_no",
        ("order_line__order__order_no", "受注番号"),
        ("order_line__order__customer", "得意先"),
        ("order_line__item", "品目"),
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
    ordering = ("-shipped_at", "-id")

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("order_line__order__customer", "order_line__item", "delivery_note")
        )


class ShipmentDetailView(BaseDetailView):
    model = Shipment
    screen_name = "出荷の詳細"
    screen_help = "出荷の項目。出荷を直す画面はありません。"


class ShipmentCreateView(BaseFormView):
    model = Shipment
    form_class = ShipmentForm
    service = staticmethod(services.ship)
    screen_name = "出荷を入れる"
    screen_help = (
        "受注の明細と数量と出荷日を入れて、出荷を保存します。"
        "受注の残を超える数は、会社の設定に従って止めるか知らせます。"
    )


class DeliveryNoteListView(BaseListView):
    model = DeliveryNote
    screen_name = "納品書"
    screen_help = "納品書の一覧。発行日の新しい順に並びます。行を押すと、印刷用の画面と PDF の入口がある詳細へ移ります。"
    columns = ("note_no", "customer", "issued_on", "status")
    search_fields = ("note_no", "customer__name")
    ordering = ("-issued_on", "-id")

    def get_queryset(self):
        return super().get_queryset().select_related("customer")


class DeliveryNoteDetailView(BaseDetailView):
    model = DeliveryNote
    screen_name = "納品書の詳細"
    screen_help = "納品書の項目と、まとめた出荷。印刷用の画面と PDF は新しいタブで開きます。"
    tabs = (("出荷", "shipments"),)
    template_name = "shipping/deliverynote_detail.html"


class DeliveryNoteCreateView(BaseFormView):
    model = DeliveryNote
    form_class = DeliveryNoteForm
    service = staticmethod(services.create_delivery_note)
    screen_name = "納品書を作る"
    screen_help = "同じ得意先の出荷を選んで、1 枚の納品書にまとめます。納品書番号は保存のときに付きます。"


@login_required
def deliverynote_print(request, pk):
    """納品書の印刷用の HTML (ブラウザの印刷で A4 1 枚)。"""
    note = get_object_or_404(DeliveryNote.objects.select_related("customer"), pk=pk)
    return HttpResponse(reports.render_html(DELIVERY_NOTE_TEMPLATE, delivery_note_context(note)))


@login_required
def deliverynote_pdf(request, pk):
    """納品書の PDF。作れなければ、印刷用の HTML の上に知らせを出して返す。"""
    note = get_object_or_404(DeliveryNote.objects.select_related("customer"), pk=pk)
    context = delivery_note_context(note)
    try:
        data = reports.render_pdf(DELIVERY_NOTE_TEMPLATE, context)
    except reports.PdfUnavailable:
        return HttpResponse(reports.render_html(DELIVERY_NOTE_TEMPLATE, {**context, "pdf_notice": PDF_NOTICE}))
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{note.note_no}.pdf"'
    return response
