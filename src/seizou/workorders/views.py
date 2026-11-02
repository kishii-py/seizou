"""workorders の画面 (02 の指示書)。全部 core の土台を継承する (R15)。

一覧は行を押すと詳細、部品の名前の字を押すと小窓 (core の master_link)。
発行は検査の層 (受注より多い指示書のポカよけ) を通ってから services.issue_workorder を呼ぶ。
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_POST

from seizou.core.exceptions import RuleError
from seizou.core.views import BaseDetailView, BaseFormView, BaseListView, BaseModalView, Notice

from . import plans, services
from .forms import PlanPlaceForm, WorkOrderCancelForm, WorkOrderIssueForm
from .models import WorkOrder, WorkOrderStep


class WorkOrderListView(BaseListView):
    model = WorkOrder
    screen_name = "指示書"
    screen_help = "発行した指示書の一覧。発行日の新しい順に並びます。"
    columns = ("wo_no", "order_line", ("order_line__item", "部品"), "qty", "issued_on", "status")
    search_fields = ("wo_no",)
    ordering = ("-issued_on", "-id")


class WorkOrderDetailView(BaseDetailView):
    model = WorkOrder
    screen_name = "指示書の詳細"
    screen_help = "指示書の項目と、工程の並び。"
    tabs = (("工程", "steps"), ("進捗", "progress_records"))
    template_name = "workorders/workorder_detail.html"


class WorkOrderCreateView(BaseFormView):
    model = WorkOrder
    form_class = WorkOrderIssueForm
    service = staticmethod(services.issue_workorder)
    screen_name = "指示書を発行する"
    screen_help = "受注の明細と数量を選んで指示書を発行します。工程を選ばなければ、部品の標準の工程の並びを写します。"


class WorkOrderCancelView(BaseFormView):
    model = WorkOrder
    form_class = WorkOrderCancelForm
    service = staticmethod(services.cancel_workorder)
    template_name = "workorders/workorder_cancel.html"
    screen_name = "指示書を取り消す"
    screen_help = "指示書を取り消します。実績の入った指示書は取り消せません。取消の指示書は生産計画と進捗の盤から外れます。"


class PlanBoardView(BaseListView):
    """生産計画の盤 (02 の 4 節)。盤の中身は template tag (workorders_plan) が plans.build_board で作る。"""

    model = WorkOrderStep
    template_name = "workorders/plan_board.html"
    screen_name = "生産計画"
    screen_help = "工程ごとの日の計画。右の「未計画」から日へドラッグで置き、棒を押すと指示書を小窓で開きます。"

    def get_queryset(self):
        return plans.active_steps(plans.resolve_process(self.kwargs.get("process_pk")))


class WorkOrderModalView(BaseModalView):
    model = WorkOrder
    screen_name = "指示書の小窓"
    screen_help = "指示書の要点と工程の並びを見るだけの小窓。詳細へ移れます。"


@login_required
@require_POST
def plan_place(request):
    """生産計画の盤で日を置く・動かす・戻す口 (htmx の POST だけ。画面でない口なので関数の view)。

    成功は盤の部分 (workorders/_plan_board.html) を返し、htmx が盤の箱ごと入れ替える。
    失敗 (R05・ValueError・form の誤り) は知らせ (workorders/_plan_notice.html) を 200 で返し、
    返事の見出し HX-Retarget・HX-Reswap で盤の中の知らせの置き場に入れる (盤は変わらない)。
    """
    if not request.headers.get("HX-Request"):
        return HttpResponseBadRequest("生産計画の盤からの要求だけを受けます。")
    process = plans.resolve_process(request.POST.get("process", ""))
    form = PlanPlaceForm(request.POST)
    notice = None
    if form.is_valid():
        data = form.cleaned_data
        try:
            services.plan_step(data["step"], data["start"], data["end"], request.user)
        except RuleError as exc:
            notice = exc
        except ValueError as exc:
            notice = Notice("入力", str(exc))
        else:
            board = plans.build_board(
                process,
                plans.parse_start(request.POST.get("board_start")),
                plans.parse_weeks(request.POST.get("weeks")),
            )
            return render(request, "workorders/_plan_board.html", {"board": board})
    response = render(request, "workorders/_plan_notice.html", {"notice": notice, "form": form})
    response["HX-Retarget"] = "#plan-notice"
    response["HX-Reswap"] = "innerHTML"
    return response
