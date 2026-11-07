"""billing の URL。名前の約束は <model>_list / _detail / _create (core の土台が使う)。

取り消す (_cancel)・印刷用の画面 (_print)・PDF (_pdf) は請求書の詳細のボタンから入る。
作り直す (_reissue) は、請求書の新規登録で同じ締め期間の 2 枚目が止まったときの右上の知らせのボタンから入る (R23)。
入金の記録 (payment_create) は請求書の詳細の「入金」の枠から入り、保存の後は payment_detail (移るだけの口) が請求書の詳細へ戻す。
請求書の外で受け取った入金の記録 (shipment_payment_create) は請求書の新規登録の出荷の行から入り、保存の後は payment_detail が出荷の詳細へ移す。
"""

from django.urls import path

from . import views

app_name = "billing"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [("請求書", "billing:invoice_list")]

urlpatterns = [
    path("invoices/", views.InvoiceListView.as_view(), name="invoice_list"),
    path("invoices/new/", views.InvoiceCreateView.as_view(), name="invoice_create"),
    path("invoices/<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice_detail"),
    path("invoices/<int:pk>/modal/", views.InvoiceModalView.as_view(), name="invoice_modal"),
    path("invoices/<int:pk>/cancel/", views.InvoiceCancelView.as_view(), name="invoice_cancel"),
    path("invoices/<int:pk>/reissue/", views.InvoiceReissueView.as_view(), name="invoice_reissue"),
    path("invoices/<int:pk>/print/", views.invoice_print, name="invoice_print"),
    path("invoices/<int:pk>/pdf/", views.invoice_pdf, name="invoice_pdf"),
    path("invoices/<int:pk>/payments/new/", views.PaymentCreateView.as_view(), name="payment_create"),
    path("shipments/<int:pk>/payments/new/", views.ShipmentPaymentCreateView.as_view(), name="shipment_payment_create"),
    path("payments/<int:pk>/", views.payment_detail, name="payment_detail"),
    path("payments/<int:pk>/cancel/", views.PaymentCancelView.as_view(), name="payment_cancel"),
]
