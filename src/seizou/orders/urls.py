"""orders の URL。名前の約束は <model>_list / _detail / _create / _update / _modal / _select (core の土台が使う)。"""

from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("lines/select/", views.OrderLineSelectView.as_view(), name="orderline_select"),
]
