"""shipping の URL。名前の約束は <model>_list / _detail / _create (core の土台が使う)。"""

from django.urls import path

from . import views

app_name = "shipping"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [
    ("出荷", "shipping:shipment_list"),
]

urlpatterns = [
    path("", views.ShipmentListView.as_view(), name="shipment_list"),
    path("new/", views.ShipmentCreateView.as_view(), name="shipment_create"),
    path("<int:pk>/", views.ShipmentDetailView.as_view(), name="shipment_detail"),
]
