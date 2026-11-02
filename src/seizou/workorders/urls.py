"""workorders の URL。名前の約束は <model>_list / _detail / _create (core の土台が使う)。"""

from django.urls import path

from . import views

app_name = "workorders"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [("指示書", "workorders:workorder_list"), ("生産計画", "workorders:plan_index")]

urlpatterns = [
    path("", views.WorkOrderListView.as_view(), name="workorder_list"),
    path("new/", views.WorkOrderCreateView.as_view(), name="workorder_create"),
    path("<int:pk>/", views.WorkOrderDetailView.as_view(), name="workorder_detail"),
    path("<int:pk>/modal/", views.WorkOrderModalView.as_view(), name="workorder_modal"),
    path("plans/", views.PlanBoardView.as_view(), name="plan_index"),
    path("plans/place/", views.plan_place, name="plan_place"),
    path("plans/<int:process_pk>/", views.PlanBoardView.as_view(), name="plan_board"),
]
