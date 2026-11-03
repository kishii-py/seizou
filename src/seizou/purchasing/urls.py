"""purchasing の URL。名前の約束は <model>_list / _detail / _create (core の土台が使う)。

送り直す (_send) と取り消す (_cancel) は発注の詳細のボタンから入る。
"""

from django.urls import path

from . import views

app_name = "purchasing"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [("発注", "purchasing:purchaseorder_list"), ("受入", "purchasing:receipt_list")]

urlpatterns = [
    path("orders/", views.PurchaseOrderListView.as_view(), name="purchaseorder_list"),
    path("orders/new/", views.PurchaseOrderCreateView.as_view(), name="purchaseorder_create"),
    path("orders/<int:pk>/", views.PurchaseOrderDetailView.as_view(), name="purchaseorder_detail"),
    path("orders/<int:pk>/send/", views.PurchaseOrderSendView.as_view(), name="purchaseorder_send"),
    path("orders/<int:pk>/cancel/", views.PurchaseOrderCancelView.as_view(), name="purchaseorder_cancel"),
    path("receipts/", views.ReceiptListView.as_view(), name="receipt_list"),
    path("receipts/new/", views.ReceiptCreateView.as_view(), name="receipt_create"),
    path("receipts/<int:pk>/", views.ReceiptDetailView.as_view(), name="receipt_detail"),
    path("lines/select/", views.PurchaseOrderLineSelectView.as_view(), name="purchaseorderline_select"),
]
