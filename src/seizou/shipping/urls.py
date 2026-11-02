"""shipping の URL。名前の約束は <model>_list / _detail / _create (core の土台が使う)。

納品書の印刷用の HTML (deliverynote_print) と PDF (deliverynote_pdf) は関数の view。
"""

from django.urls import path

from . import views

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
    path("notes/", views.DeliveryNoteListView.as_view(), name="deliverynote_list"),
    path("notes/new/", views.DeliveryNoteCreateView.as_view(), name="deliverynote_create"),
    path("notes/<int:pk>/", views.DeliveryNoteDetailView.as_view(), name="deliverynote_detail"),
    path("notes/<int:pk>/print/", views.deliverynote_print, name="deliverynote_print"),
    path("notes/<int:pk>/pdf/", views.deliverynote_pdf, name="deliverynote_pdf"),
]
