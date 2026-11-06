"""workorders の URL。名前の約束は <model>_list / _detail / _create / _update / _modal / _select (core の土台が使う)。"""

from django.urls import path

from seizou.core.conf import setting

from . import views

app_name = "workorders"


class _Menu:
    """左のメニューの入口。core の context processor が要求ごとに回すたびに、会社の設定を読む。

    「生産計画」は設定 production_plan が真のときだけ出す (偽の会社では盤の URL も 404)。
    """

    def __iter__(self):
        yield ("指示書", "workorders:workorder_list")
        if setting("production_plan"):
            yield ("生産計画", "workorders:plan_index")


#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = _Menu()

urlpatterns = [
    path("", views.WorkOrderListView.as_view(), name="workorder_list"),
    path("new/", views.WorkOrderCreateView.as_view(), name="workorder_create"),
    path("<int:pk>/", views.WorkOrderDetailView.as_view(), name="workorder_detail"),
    path("<int:pk>/modal/", views.WorkOrderModalView.as_view(), name="workorder_modal"),
    path("select/", views.WorkOrderSelectView.as_view(), name="workorder_select"),
    path("steps/select/", views.WorkOrderStepSelectView.as_view(), name="workorderstep_select"),
    path("<int:pk>/cancel/", views.WorkOrderCancelView.as_view(), name="workorder_cancel"),
    path("<int:pk>/accept/", views.workorder_accept, name="workorder_accept"),
    path("<int:pk>/tag/", views.workorder_tag, name="workorder_tag"),
    path("<int:pk>/tag/pdf/", views.workorder_tag_pdf, name="workorder_tag_pdf"),
    path("<int:pk>/scan/", views.workorder_scan, name="workorder_scan"),
    path("plans/", views.PlanBoardView.as_view(), name="plan_index"),
    path("plans/place/", views.plan_place, name="plan_place"),
    path("plans/steps/<int:pk>/", views.PlanDetailView.as_view(), name="plan_detail"),
    path("plans/<int:process_pk>/", views.PlanBoardView.as_view(), name="plan_board"),
]
