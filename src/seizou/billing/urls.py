"""billing の URL。名前の約束は <model>_list / _detail / _create (core の土台が使う)。

取り消す (_cancel)・印刷用の画面 (_print)・PDF (_pdf) は請求書の詳細のボタンから入る。
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
    path("invoices/<int:pk>/cancel/", views.InvoiceCancelView.as_view(), name="invoice_cancel"),
    path("invoices/<int:pk>/print/", views.invoice_print, name="invoice_print"),
    path("invoices/<int:pk>/pdf/", views.invoice_pdf, name="invoice_pdf"),
]
