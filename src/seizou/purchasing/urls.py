"""purchasing の URL。名前の約束は <model>_list / _detail / _create (core の土台が使う)。

送り直す (_send) と取り消す (_cancel) と変更 (_change) は発注の詳細のボタンから入る。
発注書の印刷用の HTML (_print) と PDF (_pdf) は関数の view。
"""

from django.urls import path

from . import views
from . import acceptance
from . import self_invoice

app_name = "purchasing"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [("発注", "purchasing:purchaseorder_list"), ("受入", "purchasing:receipt_list")]

urlpatterns = [
    path("orders/", views.PurchaseOrderListView.as_view(), name="purchaseorder_list"),
    path("orders/new/", views.PurchaseOrderCreateView.as_view(), name="purchaseorder_create"),
    path("orders/<int:pk>/", views.PurchaseOrderDetailView.as_view(), name="purchaseorder_detail"),
    path("orders/<int:pk>/modal/", views.PurchaseOrderModalView.as_view(), name="purchaseorder_modal"),
    path("orders/<int:pk>/send/", views.PurchaseOrderSendView.as_view(), name="purchaseorder_send"),
    path("orders/<int:pk>/cancel/", views.PurchaseOrderCancelView.as_view(), name="purchaseorder_cancel"),
    # 送った発注の変更 (相手が出荷し終えるまで)。_update の名前にしない訳: 土台が _update を全部の発注の「編集」の入口に使うが、変更を送れるのは一部の状態だけのため。
    path("orders/<int:pk>/change/", views.PurchaseOrderChangeView.as_view(), name="purchaseorder_change"),
    path("orders/<int:pk>/print/", views.purchaseorder_print, name="purchaseorder_print"),
    path("orders/<int:pk>/pdf/", views.purchaseorder_pdf, name="purchaseorder_pdf"),
    path("receipts/", views.ReceiptListView.as_view(), name="receipt_list"),
    path("receipts/new/", views.ReceiptCreateView.as_view(), name="receipt_create"),
    path("receipts/<int:pk>/", views.ReceiptDetailView.as_view(), name="receipt_detail"),
    path("receipts/<int:pk>/acceptance/resend/", acceptance.resend_acceptance, name="receipt_acceptance_resend"),
    # 仕入明細 (作者の決め)。入口は受入の一覧の上の「仕入明細」と発注の詳細の「仕入明細」のタブ (左のメニューには足さない)。
    path("selfinvoices/", views.SelfInvoiceListView.as_view(), name="selfinvoice_list"),
    path("selfinvoices/new/", views.SelfInvoiceCreateView.as_view(), name="selfinvoice_create"),
    path("selfinvoices/<int:pk>/", views.SelfInvoiceDetailView.as_view(), name="selfinvoice_detail"),
    path("selfinvoices/<int:pk>/resend/", self_invoice.resend_self_invoice, name="selfinvoice_resend"),
    path("selfinvoices/<int:pk>/cancel/", views.SelfInvoiceCancelView.as_view(), name="selfinvoice_cancel"),
    path("selfinvoices/<int:pk>/accept/", views.SelfInvoiceAcceptProposalsView.as_view(), name="selfinvoice_accept"),
    path("lines/select/", views.PurchaseOrderLineSelectView.as_view(), name="purchaseorderline_select"),
    # 明細の完了の印の付け外し (N400b。入口は発注の詳細の明細の ⋮。左のメニューには足さない)
    path("lines/<int:pk>/complete/", views.PurchaseOrderLineCompleteView.as_view(), name="purchaseorderline_complete"),
    path(
        "lines/<int:pk>/complete/clear/",
        views.PurchaseOrderLineCompleteClearView.as_view(),
        name="purchaseorderline_complete_clear",
    ),
    path("items/select/", views.PurchaseItemSelectView.as_view(), name="item_select"),
]
