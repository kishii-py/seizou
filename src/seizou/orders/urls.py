"""orders の URL。名前の約束は <model>_list / _detail / _create / _update / _modal / _select (core の土台が使う)。"""

from django.urls import path

from . import views

app_name = "orders"

#: 左のメニューに並べる入口 (core の context processor が読む)。受注管理の先頭に注文残の盤 (決め 11-04 17:4x)。
MENU = [
    ("生産管理ボード", "orders:orderline_list"),
    ("受注", "orders:order_list"),
]

urlpatterns = [
    path("", views.OrderListView.as_view(), name="order_list"),
    path("new/", views.OrderCreateView.as_view(), name="order_create"),
    path("<int:pk>/", views.OrderDetailView.as_view(), name="order_detail"),
    path("<int:pk>/edit/", views.OrderUpdateView.as_view(), name="order_update"),
    path("<int:pk>/cancel/", views.OrderCancelView.as_view(), name="order_cancel"),
    path("lines/", views.OrderLineListView.as_view(), name="orderline_list"),
    path("lines/select/", views.OrderLineSelectView.as_view(), name="orderline_select"),
    path("lines/<int:pk>/modal/", views.orderline_modal, name="orderline_modal"),
]
