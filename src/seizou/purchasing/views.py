"""purchasing の画面 (02 の発注と注文を送る)。全部 core の土台を継承する (R15)。

class の中に書くのは、土台が上書きを許す属性だけ。保存は services の関数だけが行い (R13)、
送るのは connect.services.send_order だけ (R17。HTTP は atomic の外)。
"""

from seizou.connect import services as connect_services
from seizou.core.views import BaseDetailView, BaseFormView, BaseListView

from . import services
from .forms import CancelForm, PurchaseOrderForm, SendForm
from .models import PurchaseOrder


class PurchaseOrderListView(BaseListView):
    model = PurchaseOrder
    screen_name = "発注"
    screen_help = "外注先への発注。行を押すと明細と、相手へ送った記録が見られます。"
    columns = ("po_no", "partner", "order_date", "status", "note")
    search_fields = ("po_no", "partner__name", "partner__code")
    ordering = ("-order_date", "-id")


class PurchaseOrderDetailView(BaseDetailView):
    model = PurchaseOrder
    screen_name = "発注の詳細"
    screen_help = "発注の項目と明細、相手へ送った記録。「作成」のときだけ送り直しと取消ができます。"
    tabs = (("送った記録", "outbox_messages"),)
    template_name = "purchasing/purchaseorder_detail.html"


class PurchaseOrderCreateView(BaseFormView):
    model = PurchaseOrder
    form_class = PurchaseOrderForm
    service = staticmethod(services.create_purchase_order)
    template_name = "purchasing/purchaseorder_form.html"
    screen_name = "発注の新規登録"
    screen_help = "外注先と明細を入れて保存すると発注の記録ができ、つながりのある相手へ同時に注文を送ります。"


class PurchaseOrderSendView(BaseFormView):
    model = PurchaseOrder
    form_class = SendForm
    service = staticmethod(connect_services.send_order)
    screen_name = "注文を送り直す"
    screen_help = "「作成」のままの発注を、つながりの相手へもう一度送ります。"


class PurchaseOrderCancelView(BaseFormView):
    model = PurchaseOrder
    form_class = CancelForm
    service = staticmethod(services.cancel_purchase_order)
    screen_name = "発注を取り消す"
    screen_help = "「作成」の発注を取り消します。理由は備考の末尾に残ります。"
