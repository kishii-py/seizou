"""shipping の URL。名前の約束は <model>_list / _detail / _create (core の土台が使う)。

納品書の印刷用の HTML (deliverynote_print) と PDF (deliverynote_pdf) は関数の view。
出荷の編集 (shipment_update) は土台が出荷の詳細に「編集」を出し、納品書を取り消す (deliverynote_cancel) は納品書の詳細のボタンから入る。
"""

from django.urls import path

from . import views
from . import shipping_notice

app_name = "shipping"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [
    ("出荷", "shipping:shipment_list"),
    ("納品書", "shipping:deliverynote_list"),
]

urlpatterns = [
    path("", views.ShipmentListView.as_view(), name="shipment_list"),
    path("new/", views.ShipmentCreateView.as_view(), name="shipment_create"),
    path("<int:pk>/", views.ShipmentDetailView.as_view(), name="shipment_detail"),
    path("<int:pk>/edit/", views.ShipmentUpdateView.as_view(), name="shipment_update"),
    path("<int:pk>/label/", views.shipment_label, name="shipment_label"),
    path("<int:pk>/label/pdf/", views.shipment_label_pdf, name="shipment_label_pdf"),
    path("<int:pk>/notice/resend/", shipping_notice.resend_shipping_notice, name="shipment_notice_resend"),
    path("notes/", views.DeliveryNoteListView.as_view(), name="deliverynote_list"),
    path("notes/new/", views.DeliveryNoteCreateView.as_view(), name="deliverynote_create"),
    path("notes/<int:pk>/", views.DeliveryNoteDetailView.as_view(), name="deliverynote_detail"),
    path("notes/<int:pk>/print/", views.deliverynote_print, name="deliverynote_print"),
    path("notes/<int:pk>/pdf/", views.deliverynote_pdf, name="deliverynote_pdf"),
    path("notes/<int:pk>/cancel/", views.DeliveryNoteCancelView.as_view(), name="deliverynote_cancel"),
]
