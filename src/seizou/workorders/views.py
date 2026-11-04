"""workorders の画面 (02 の指示書)。全部 core の土台を継承する (R15)。

一覧は行を押すと詳細、部品の名前の字を押すと小窓 (core の master_link)。
発行は検査の層 (受注より多い指示書のポカよけ) を通ってから services.issue_workorder を呼ぶ。
現品票の印刷用の HTML と PDF は画面でない口なので、関数の view にした (shipping の納品書と同じ形)。
PDF を作れない環境 (WeasyPrint か OS の pango が無い) では、現品票の印刷用の HTML に知らせを付けて返す。
現品票の QR を読んだ先 (読み取りの着地) は関数の view で、工程ごとに実績の新規登録へ移る link を並べる。
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from seizou.core import access
from seizou.core import reports
from seizou.core.exceptions import RuleError
from seizou.core.views import BaseDetailView, BaseFormView, BaseListView, BaseModalView, Notice

from . import plans, services, worklist
from .forms import PlanPlaceForm, WorkOrderCancelForm, WorkOrderIssueForm
from .models import WorkOrder, WorkOrderStep
from .reports import WORKORDER_TAG_TEMPLATE, workorder_tag_context

#: PDF を作れないときに現品票の印刷用の HTML の上に出す知らせ (05 の 6 節の文。shipping の納品書と同じ字)
PDF_NOTICE = "PDF の準備がありません。印刷用の画面をお使いください。"


class WorkOrderListView(BaseListView):
    """指示書の一覧。

    使う人: 製造・生産管理 / いつ: 製造は朝と工程の切り替え時、生産管理は進み具合を見るとき /
    何のために: 製造は今日の仕事を知り、生産管理は依頼した物が受けられ進んでいるかを見る / 次にすること: 行から詳細へ (受けた印・現品票・実績)。生産管理は注文残へ戻る
    """

    model = WorkOrder
    screen_name = "指示書"
    screen_help = (
        "発行した指示書の一覧。発行日の新しい順に並びます。工程・未着手・今日の計画で絞り込めます。"
        "行を押すと詳細で「受けた」の印を付けられます。"
    )
    columns = (
        "wo_no",
        {"name": "order_line", "sort_key": "order_line__order__order_no"},
        ("order_line__item", "部品"),
        "qty",
        "issued_on",
        "status",
        ("accepted_label", "受けた"),
    )
    search_fields = ("wo_no",)
    ordering = ("-issued_on", "-id")
    template_name = "workorders/workorder_list.html"

    def get_queryset(self):
        workorders = super().get_queryset().select_related("order_line__order", "order_line__item", "accepted_by")
        return worklist.apply_list_filters(workorders, worklist.list_filters(self.request.GET))


class WorkOrderDetailView(BaseDetailView):
    """指示書の詳細。

    使う人: 製造・生産管理 / いつ: 指示書を受けるときと、工程の進みを見るとき / 何のために: 項目と工程の並びと実績を見て「受けた」の印を付ける / 次にすること: 現品票を印刷し、工程ごとに実績の新規登録へ
    """

    model = WorkOrder
    screen_name = "指示書の詳細"
    screen_help = "指示書の項目と、工程の並び。"
    tabs = (("工程", "steps"), ("進捗", "progress_records"))
    template_name = "workorders/workorder_detail.html"


class WorkOrderCreateView(BaseFormView):
    """指示書を発行する入力。

    使う人: 生産管理 / いつ: 注文残を見て作ると決めたとき / 何のために: 製造部に生産を依頼する (数量と工程の並び) / 次にすること: 製造が指示書を受ける
    """

    model = WorkOrder
    form_class = WorkOrderIssueForm
    service = staticmethod(services.issue_workorder)
    screen_name = "指示書を発行する"
    screen_help = "受注の明細と数量を選んで指示書を発行します。工程を選ばなければ、部品の標準の工程の並びを写します。"


class WorkOrderCancelView(BaseFormView):
    """指示書を取り消す確かめの入力。

    使う人: 生産管理 / いつ: 作らなくなったとき / 何のために: 指示書を取り消す (実績の入った物は取り消せない) / 次にすること: 注文残へ戻る
    """

    model = WorkOrder
    form_class = WorkOrderCancelForm
    service = staticmethod(services.cancel_workorder)
    template_name = "workorders/workorder_cancel.html"
    screen_name = "指示書を取り消す"
    screen_help = "指示書を取り消します。実績の入った指示書は取り消せません。取消の指示書は生産計画と進捗の盤から外れます。"


class PlanBoardView(BaseListView):
    """生産計画の盤 (02 の 4 節)。盤の中身は template tag (workorders_plan) が plans.build_board で作る。

    使う人: 生産管理 / いつ: 週の始めと、指示書を発行した後 / 何のために: 工程ごとに日の計画を置く / 次にすること: 製造が盤を見て順に作る
    """

    model = WorkOrderStep
    template_name = "workorders/plan_board.html"
    screen_name = "生産計画"
    screen_help = "工程ごとの日の計画。右の「未計画」から日へドラッグで置き、棒を押すと計画の詳細を小窓で開きます。"

    def get_queryset(self):
        return plans.active_steps(plans.resolve_process(self.kwargs.get("process_pk")))


class PlanDetailView(BaseModalView):
    """計画の詳細の小窓 (決め 11-04 14:5x)。盤の棒と未計画のカードを押すと開く。中身は template tag (workorders_plan) が plans.plan_detail で作る。

    使う人: 生産管理・製造 / いつ: 生産計画の盤で予定を確かめるとき / 何のために: 指示書の要点・部品・受注の明細・工程の並び・部品の在庫の見込みを、画面を移らずに見る / 次にすること: 盤で日を置き直すか、指示書の詳細へ
    """

    model = WorkOrderStep
    template_name = "workorders/_plan_detail.html"
    screen_name = "計画の詳細"
    screen_help = "生産計画の盤の予定の詳細。指示書の要点と工程の並びと、部品の在庫の見込み (今日から 8 週) を見るだけの小窓です。"

    def get_queryset(self):
        return plans.detail_steps()


class WorkOrderModalView(BaseModalView):
    """指示書の小窓。

    使う人: 製造・生産管理 / いつ: 生産計画の盤や注文残で指示書を確かめたいとき / 何のために: 画面を移らずに要点と工程の並びを見る / 次にすること: 要れば指示書の詳細へ
    """

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
            access.require_write(request.user, "workorders.plan_step")
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


@login_required
@require_POST
def workorder_accept(request, pk):
    """製造部の「受けた」の口 (POST だけ。画面でない口なので関数の view)。

    成功は緑の知らせ、ValueError は赤の知らせ (右下の toast) で、どちらも詳細へ戻る。
    現場の記録なので編集の権限は見ない (02 の 7 節の「権限が要らない」)。
    """
    workorder = get_object_or_404(WorkOrder, pk=pk)
    try:
        services.accept_workorder(workorder, request.user)
    except RuleError as exc:
        messages.error(request, exc.message)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "受けました。")
    return HttpResponseRedirect(reverse("workorders:workorder_detail", args=[pk]))


@login_required
def workorder_tag(request, pk):
    """指示書の現品票の印刷用の HTML (ブラウザの印刷で A4 縦の上半分)。QR の中身は読み取りの着地の URL。"""
    workorder = get_object_or_404(
        WorkOrder.objects.select_related("order_line__order__customer", "order_line__item"), pk=pk
    )
    context = {
        **workorder_tag_context(workorder),
        "qr_text": request.build_absolute_uri(reverse("workorders:workorder_scan", args=[pk])),
    }
    return HttpResponse(reports.render_html(WORKORDER_TAG_TEMPLATE, context))


@login_required
def workorder_tag_pdf(request, pk):
    """指示書の現品票の PDF。作れなければ、印刷用の HTML の上に知らせを出して返す。"""
    workorder = get_object_or_404(
        WorkOrder.objects.select_related("order_line__order__customer", "order_line__item"), pk=pk
    )
    context = {
        **workorder_tag_context(workorder),
        "qr_text": request.build_absolute_uri(reverse("workorders:workorder_scan", args=[pk])),
    }
    try:
        data = reports.render_pdf(WORKORDER_TAG_TEMPLATE, context)
    except reports.PdfUnavailable:
        return HttpResponse(reports.render_html(WORKORDER_TAG_TEMPLATE, {**context, "pdf_notice": PDF_NOTICE}))
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{workorder.wo_no}.pdf"'
    return response


@never_cache
@login_required
def workorder_scan(request, pk):
    """現品票の QR を読んだ先 (読み取りの着地)。工程ごとに、その工程を選んだ実績の新規登録へ移る link を並べる。

    画面の応答は土台の画面と同じくブラウザに保存させない (never_cache)。表に書かない (移るだけの link)。
    見出しと <title> は core/base.html が screen_name を読むので、土台の画面と同じ鍵を context に入れる。
    """
    workorder = get_object_or_404(
        WorkOrder.objects.select_related("order_line__order__customer", "order_line__item"), pk=pk
    )
    # 「完了」の印のために、工程ごとの実績の合計を progress の services で読む (読むだけ。表に書かない)。
    from seizou.progress.services import recorded_qty

    steps = workorder.steps.select_related("process").order_by("seq")
    rows = [{"step": step, "done": recorded_qty(step) >= step.planned_qty} for step in steps]
    return render(
        request,
        "workorders/workorder_scan.html",
        {
            "workorder": workorder,
            "rows": rows,
            "screen_name": "現品票の読み取り",
            "screen_help": (
                "現品票の QR から開く画面です。"
                "工程の「実績を入力」を押すと、その工程を選んだ形で実績の新規登録が開きます。"
            ),
            "screen_app": "workorders",
        },
    )
