"""workorders の画面 (02 の指示書)。全部 core の土台を継承する (R15)。

一覧は行を押すと詳細、品目の名前の字を押すと小窓 (core の master_link)。
発行は検査の層 (受注より多い指示書のポカよけ) を通ってから services.issue_workorder を呼ぶ。
"""

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView

from . import services
from .forms import WorkOrderIssueForm
from .models import WorkOrder


class WorkOrderListView(BaseListView):
    model = WorkOrder
    screen_name = "指示書"
    screen_help = "発行した指示書の一覧。発行日の新しい順に並びます。"
    columns = ("wo_no", "order_line", ("order_line__item", "品目"), "qty", "issued_on", "status")
    search_fields = ("wo_no",)
    ordering = ("-issued_on", "-id")


class WorkOrderDetailView(BaseDetailView):
    model = WorkOrder
    screen_name = "指示書の詳細"
    screen_help = "指示書の項目と、工程の並び。"
    tabs = (("工程", "steps"),)


class WorkOrderCreateView(BaseFormView):
    model = WorkOrder
    form_class = WorkOrderIssueForm
    service = staticmethod(services.issue_workorder)
    screen_name = "指示書を発行する"
    screen_help = "受注の明細と数量を選んで指示書を発行します。工程を選ばなければ、品目の標準の工程の並びを写します。"
