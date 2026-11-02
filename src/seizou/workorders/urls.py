"""workorders の URL。名前の約束は <model>_list / _detail / _create (core の土台が使う)。"""

from django.urls import path

from . import views

app_name = "workorders"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [("指示書", "workorders:workorder_list")]

urlpatterns = [
    path("", views.WorkOrderListView.as_view(), name="workorder_list"),
    path("new/", views.WorkOrderCreateView.as_view(), name="workorder_create"),
    path("<int:pk>/", views.WorkOrderDetailView.as_view(), name="workorder_detail"),
]
