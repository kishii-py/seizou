"""receiving の URL。名前の約束は <model>_list / _detail / _create (core の土台が使う)。

入荷の一覧は土台が「新規登録」のボタンを出す (receiving_create)。受注の明細の候補の口は新規登録の form の欄が使う。
"""

from django.urls import path

from . import views

app_name = "receiving"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [("入荷", "receiving:receiving_list")]

urlpatterns = [
    path("", views.ReceivingListView.as_view(), name="receiving_list"),
    path("<int:pk>/", views.ReceivingDetailView.as_view(), name="receiving_detail"),
    path("new/", views.ReceivingCreateView.as_view(), name="receiving_create"),
    path("lines/select/", views.OrderLineSelectView.as_view(), name="orderline_select"),
]
