"""workorders の画面 (02 の指示書)。全部 core の土台を継承する (R15)。

一覧は行を押すと詳細、部品の名前の字を押すと小窓 (core の master_link)。
発行は検査の層 (受注より多い指示書のポカよけ) を通ってから services.issue_workorder を呼ぶ。
現品票の印刷用の HTML と PDF は画面でない口なので、関数の view にした (shipping の納品書と同じ形)。
PDF を作れない環境 (WeasyPrint か OS の pango が無い) では、現品票の印刷用の HTML に知らせを付けて返す。
現品票の QR を読んだ先 (読み取りの着地) は関数の view で、工程ごとに実績の新規登録へ移る link を並べる。記録の後はこの画面へ戻る (?next=)。
上の帯の「現品票を読む」の小窓 (workorder_reader) は、QR の字か「指示書」の欄から指示書を引き、着地と同じ工程の行を小窓に出す。別の住所で刷った現品票の QR は開かない。
"""

from urllib.parse import urlsplit

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import Resolver404, get_script_prefix, resolve, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from seizou.core import access
from seizou.core import reports
from seizou.core.exceptions import RuleError
from seizou.core.views import REFUSAL_HEAD_SAVE, BaseDetailView, BaseFormView, BaseListView, BaseModalView, BaseSelectView, Notice, add_notice_trigger

from . import plans, services, worklist
from .forms import PlanPlaceForm, WorkOrderCancelForm, WorkOrderFindForm, WorkOrderIssueForm
from .models import WorkOrder, WorkOrderStep, with_recorded_total
from .reports import WORKORDER_TAG_TEMPLATE, workorder_tag_context

#: PDF を作れないときに現品票の印刷用の HTML の上に出す知らせ (帳票の設計の決めの文。shipping の納品書と同じ字)
PDF_NOTICE = "PDF の準備がありません。印刷用の画面をお使いください。"

#: 上の帯の「現品票を読む」の小窓の赤い帯 (段 2 の止まった。小窓の中なので core/_notices.html の帯)。
#: QR の字が、この住所で刷った現品票の QR でない (別の住所・別の画面の QR・URL でない字)。
READER_NOT_TAG = (
    "読み取れません。この QR は、このシステムの現品票の QR ではありません。"
    "現品票の QR を写すか、「指示書」の欄で探してください。"
)
#: 現品票の QR か「指示書」の欄の番号の指示書が無い (消えた・数字でない)。
READER_NOT_FOUND = "読み取れません。その指示書が見つかりません。「指示書」の欄で探し直してください。"
#: カメラを使えないときの赤い帯 (小窓の中に hidden の包みで前もって描き、workorders/qr-reader.js が hidden を外すだけ)。
#: 鍵は data-qr-message の値 (insecure = https でない住所・denied = ブラウザがカメラを許していない・nocamera = カメラを開けない)。
READER_CAMERA_MESSAGES = {
    "insecure": "読み取れません。カメラは https の住所で開いたときだけ使えます。「指示書」の欄で探してください。",
    "denied": (
        "読み取れません。ブラウザがカメラを使うことを許していません。"
        "ブラウザの設定でこのサイトにカメラを許すか、「指示書」の欄で探してください。"
    ),
    "nocamera": "読み取れません。この端末でカメラを開けませんでした。「指示書」の欄で探してください。",
}


#: 一覧の絞りの「状態」の選び (値は表の状態、字は実績から出す 4 つの字。作者の決め)
STATUS_FILTER_CHOICES = [
    (WorkOrder.STATUS_ISSUED, "未着手・着手済・完了"),
    (WorkOrder.STATUS_CANCELLED, "取消"),
]


def _process_choices():
    """工程の絞りの候補。字は工程の名前だけ (工程コードを付けない)。並びは工程の ordering (並び順・工程コード)。"""
    process = WorkOrderStep._meta.get_field("process").related_model
    return [(row.pk, row.name) for row in process._default_manager.all()]


class WorkOrderListView(BaseListView):
    """指示書の一覧。

    使う人: 製造・生産管理 / いつ: 製造は朝と工程の切り替え時、生産管理は進み具合を見るとき /
    何のために: 製造は今日の仕事を知り、生産管理は依頼した物が受けられ進んでいるかを見る / 次にすること: 行から詳細へ (現品票・実績)。生産管理は生産管理ボードへ戻る
    """

    model = WorkOrder
    screen_name = "指示書"
    screen_help = (
        "発行した指示書の一覧。発行日の新しい順に並びます。工程・未着手・今日の計画で絞り込めます。"
        "右の「工程の進み」は工程ごとの実績の合計と予定の数の棒です。行を押すと詳細で工程と実績を見られます。"
    )
    columns = (
        "wo_no",
        {"name": "order_line", "sort_key": "order_line__order__order_no"},
        ("order_line__item", "部品"),
        "qty",
        "issued_on",
        # 状態は実績から出す字 (未着手・着手済・完了・取消。作者の決め)。行は workorders/_row.html が印にする
        ("progress_state_label", "状態"),
        # 工程ごとの進み (工程の名前・棒・実績の合計 / 予定の数を縦に並べる。本人の決め 11-07 18:2x〜18:4x)。値は WorkOrder.step_progress、
        # 行は workorders/_row.html が棒にする。CSV は str の字 (「切断 3/10・曲げ 0/10」)。並び替えは無い (表の項目でない)
        ("step_progress", "工程の進み"),
    )
    search_fields = ("wo_no",)
    filters = (
        # 状態の選びは表の値 (発行・取消) で絞り、字に「発行」を出さない (計算した 4 つの字で絞る種類は土台に無い。作者の決め)
        {"name": "status", "label": "状態", "kind": "choice", "field": "status", "choices": STATUS_FILTER_CHOICES},
        {"name": "process", "label": "工程", "kind": "lookup", "field": "steps__process", "choices": _process_choices},
        {"name": "item", "label": "部品", "kind": "lookup", "field": "order_line__item"},
        {"name": "issued", "label": "発行日", "kind": "date_range", "field": "issued_on"},
        {"name": "unaccepted", "label": "未着手だけ", "kind": "flag", "when": worklist.unaccepted_only},
        {"name": "today", "label": "今日の計画だけ", "kind": "flag", "when": worklist.planned_today},
    )
    ordering = ("-issued_on", "-id")

    def get_queryset(self):
        # 工程 (f_process) と今日の計画 (f_today) は、指示書から見て逆向きの FK (工程の行) で絞る。
        # どちらかが掛かったときは、同じ指示書が重ならないように distinct で外す (flag の when は filter だけ。開発の記録 docs/nights/20261104/N122.md)。
        joined = self.filter_chosen("process") or self.flag_on("today")
        workorders = super().get_queryset().select_related("order_line__order", "order_line__item")
        # 状態の列 (progress_state_label) と工程の進みの列 (step_progress) は工程と実績の合計と工程の名前を読むので、
        # 1 ページ分を 1 回で prefetch する (行ごとに読まない。工程の名前は select_related)
        workorders = workorders.prefetch_related(
            Prefetch("steps", queryset=with_recorded_total(WorkOrderStep.objects.select_related("process").order_by("seq")))
        )
        return workorders.distinct() if joined else workorders


class WorkOrderDetailView(BaseDetailView):
    """指示書の詳細。

    使う人: 製造・生産管理 / いつ: 指示書を受けるときと、工程の進みを見るとき / 何のために: 項目と工程の並びと実績を見て、工程の進みと状態を確かめる / 次にすること: 現品票を印刷し、工程ごとに実績の新規登録へ
    """

    model = WorkOrder
    screen_name = "指示書の詳細"
    screen_help = "指示書の項目と、工程の並び。"
    tabs = (("工程", "steps"), ("進捗", "progress_records"))
    template_name = "workorders/workorder_detail.html"


class WorkOrderCreateView(BaseFormView):
    """指示書を発行する入力。

    使う人: 生産管理 / いつ: 生産管理ボードを見て作ると決めたとき / 何のために: 製造部に生産を依頼する (数量と工程の並び) / 次にすること: 製造が指示書を受ける
    """

    model = WorkOrder
    form_class = WorkOrderIssueForm
    service = staticmethod(services.issue_workorder)
    screen_name = "指示書を発行する"
    screen_help = "受注の明細と数量を選んで指示書を発行します。工程を選ばなければ、部品の標準の工程の並びを写します。"


class WorkOrderCancelView(BaseFormView):
    """指示書を取り消す確かめの入力。

    使う人: 生産管理 / いつ: 作らなくなったとき / 何のために: 指示書を取り消す (実績の入った物は取り消せない) / 次にすること: 生産管理ボードへ戻る
    """

    model = WorkOrder
    form_class = WorkOrderCancelForm
    service = staticmethod(services.cancel_workorder)
    template_name = "workorders/workorder_cancel.html"
    screen_name = "指示書を取り消す"
    screen_help = "指示書を取り消します。実績の入った指示書は取り消せません。取消の指示書は生産計画と進捗の盤から外れます。"


class PlanBoardView(BaseListView):
    """生産計画の盤 (生産計画の盤の設計の決め)。盤の中身は template tag (workorders_plan) が plans.build_board で作る。

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

    使う人: 製造・生産管理 / いつ: 生産計画の盤や生産管理ボードで指示書を確かめたいとき / 何のために: 画面を移らずに要点と工程の並びを見る / 次にすること: 要れば指示書の詳細へ
    """

    model = WorkOrder
    screen_name = "指示書の小窓"
    screen_help = "指示書の要点と工程の並びを見るだけの小窓。詳細へ移れます。"


class WorkOrderSelectView(BaseSelectView):
    """指示書の候補の口。製造の完了を入庫にするの「指示書」の欄 (stock の FinishedForm) と、上の帯の「現品票を読む」の「指示書」の欄 (WorkOrderFindForm) が使う。

    候補は発行の指示書だけ (今の選ぶ欄と同じ)。並びは発行日の新しい順。
    候補の字は 2 段 (上 = 指示書番号、下 = 受注の明細。core/select_options.py の LINES)。
    """

    model = WorkOrder
    search_fields = ("wo_no", "order_line__order__order_no", "order_line__item__part_no", "order_line__item__name")

    def get_queryset(self):
        # select_related は下の段の字 (受注の明細の字) が読む物。1 ページ 30 件で行ごとに読みに行かないため。
        return (
            WorkOrder.objects.filter(status=WorkOrder.STATUS_ISSUED)
            .select_related("order_line__order", "order_line__item")
            .order_by("-issued_on", "-pk")
        )


class WorkOrderStepSelectView(BaseSelectView):
    """指示書の工程の候補の口。実績の新規登録の「指示書の工程」の欄 (progress の ProgressRecordForm) が使う。

    候補は全部の指示書の全部の工程 (今の選ぶ欄と同じ)。字は今の「WO-000010 1 前処理」のまま (WorkOrderStep.__str__)。
    指示書番号と工程の名前で探せる。
    """

    model = WorkOrderStep
    search_fields = ("workorder__wo_no", "process__name")

    def get_queryset(self):
        # 並びは model の ordering のまま (指示書の新しい順・工程の順)
        return WorkOrderStep.objects.select_related("workorder", "process")


#: 盤の置き直しの form の誤りの文の終わり。盤には欄が無い (値はドラッグとボタンが送る) ので、直し方でなく次にすることを書く (決め 11-07)
PLAN_FORM_RETRY = "盤を開き直してから、もう一度置いてください。"


def _plan_form_notice(form):
    """盤の置き直しの form の誤りを、止まった知らせ 1 つにする (前の盤の中の帯の「<欄の名前>: <誤り>」の並びを 1 つの文にした)。

    core.views.invalid_form_message は使わない (その字の「欄の下の赤い字のとおりに直してから」は、欄の無い盤には当たらない)。
    """
    errors = [str(error) for error in form.non_field_errors()]
    errors += [f"{form.fields[name].label}: {error}" for name in form.fields if name in form.errors for error in form.errors[name]]
    return Notice("入力", f"{REFUSAL_HEAD_SAVE}{' '.join(errors)} {PLAN_FORM_RETRY}")


@login_required
@require_POST
def plan_place(request):
    """生産計画の盤で日を置く・動かす・戻す口 (htmx の POST だけ。画面でない口なので関数の view)。

    成功は盤の部分 (workorders/_plan_board.html) を返し、htmx が盤の箱ごと入れ替える。
    失敗 (R05・ValueError・form の誤り・権限) は本文の無い 204 に、見出し HX-Trigger の seizou:notice (core.views.add_notice_trigger) を付けて返す。
    右上の赤い知らせに出し、盤は変わらない (決め 11-07)。
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
    else:
        notice = _plan_form_notice(form)
    return add_notice_trigger(HttpResponse(status=204), notice)


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


def _scan_workorders():
    """読み取りの着地と上の帯の小窓が指示書を引く形 (要点の品番・品名・納期・得意先を 1 回で読む)。"""
    return WorkOrder.objects.select_related("order_line__order__customer", "order_line__item")


def _scan_context(workorder) -> dict:
    """読み取りの着地と上の帯の小窓が同じ工程の行を描く context (workorder と rows)。表に書かない。"""
    # 工程ごとの実績の合計・残・「完了」の印・今の工程のために、工程の行を plans.step_rows で読む (読むだけ。表に書かない)。
    # 残 = 予定の数 - 実績の合計 (進捗の盤の残と同じ引き算。予定を超えるとマイナス)。
    rows = plans.step_rows(workorder)
    for row in rows:
        recorded = row["recorded"]
        row["remaining"] = None if recorded is None else row["step"].planned_qty - recorded
    return {"workorder": workorder, "rows": rows}


@never_cache
@login_required
def workorder_scan(request, pk):
    """現品票の QR を読んだ先 (読み取りの着地)。工程ごとに、その工程を選んだ実績の新規登録へ移る link を並べ、記録の後は ?next= でこの画面へ戻る。

    画面の応答は土台の画面と同じくブラウザに保存させない (never_cache)。表に書かない (移るだけの link)。
    見出しと <title> は core/base.html が screen_name を読むので、土台の画面と同じ鍵を context に入れる。
    """
    workorder = get_object_or_404(_scan_workorders(), pk=pk)
    return render(
        request,
        "workorders/workorder_scan.html",
        {
            **_scan_context(workorder),
            "screen_name": "現品票の読み取り",
            "screen_help": (
                "現品票の QR から開く画面です。"
                "工程の「実績を入力」を押すと、その工程を選んだ形で実績の新規登録が開きます。"
            ),
            "screen_app": "workorders",
        },
    )


def _scan_pk_from_qr(text: str, request) -> int | None:
    """カメラが読んだ QR の字から、読み取りの着地の指示書の pk を出す。この住所で刷った現品票の QR でなければ None。

    通すのは、開いている住所 (名前とポート) と同じ住所の URL で、道が読み取りの着地 (workorders:workorder_scan) の物だけ。
    別の住所・javascript: などの URL・別の画面の道 (出荷ラベルや納品書の QR)・URL でない字は None。
    scheme (http か https か) の違いは見ない。http の置き場の時に刷り、https に移った後に読む札を止めないため。
    道の頭 (script prefix) が「/」でない置き場では、頭を外して「/」から始まる形にしてから引く。
    訳: 協力企業や取引先の seizou で刷った現品票を、この会社の同じ番号の指示書と取り違えて実績を入れないため (別の住所の札は開かず、「指示書」の欄で探す)。出所 = 作者の決め (2026-11-07)。
    変えてよい条件 = 同じ会社が 2 つ以上の住所で開くことになったとき (通す住所の並びを会社の設定に持つ形にする)。
    """
    if not url_has_allowed_host_and_scheme(text, allowed_hosts={request.get_host()}, require_https=False):
        return None
    path = urlsplit(text).path
    prefix = get_script_prefix()
    if prefix != "/" and path.startswith(prefix):
        path = "/" + path[len(prefix):]
    try:
        match = resolve(path)
    except Resolver404:
        return None
    if match.view_name != "workorders:workorder_scan":
        return None
    return match.kwargs["pk"]


@never_cache
@login_required
def workorder_reader(request):
    """上の帯の「現品票を読む」の小窓。QR の字か「指示書」の欄から指示書を引き、読み取りの着地と同じ工程の行を小窓に出す。

    使う人: 製造 / いつ: 工程を終えたとき、どの画面を開いていても / 何のために: 現品票の QR か指示書番号から工程を選び、画面を移らずに実績を入れる / 次にすること: 工程の「実績を入力」
    QR を読む・小窓を開くだけでは何も記録しない (指示書の状態は実績だけから出す決め)。表に書かない GET だけの口。
    小窓の見出し (HX-Target が小窓の箱) の要求にだけ小窓の中身を返し、ほかの要求 (直に開いた・新しいタブ) は指示書の一覧へ移す。
    GET の code (カメラが読んだ QR の字) があれば QR の字で、無くて workorder (「指示書」の欄の pk) があればその番号で引く。
    どちらも無ければ (空の字も) 帯の無い読み取りの小窓を返す。部署は見ない (実績の新規登録が部署の門を持つ)。
    """
    headers = request.headers
    if not (headers.get("HX-Request") and headers.get("HX-Target") == BaseFormView.MODAL_TARGET):
        return redirect("workorders:workorder_list")
    code = request.GET.get("code", "")
    raw = request.GET.get("workorder", "")
    workorder = None
    notice = None
    if code:
        pk = _scan_pk_from_qr(code, request)
        if pk is None:
            notice = Notice("入力", READER_NOT_TAG)
        else:
            workorder = _scan_workorders().filter(pk=pk).first()
    elif raw:
        # isdigit でなく isdecimal で見る (上付きの「²」は isdigit が真で int が落ちる)。
        if raw.isdecimal():
            workorder = _scan_workorders().filter(pk=int(raw)).first()
    if workorder is not None:
        return render(
            request, "workorders/_workorder_scan_modal.html", {**_scan_context(workorder), "in_modal": True}
        )
    if notice is None and (code or raw):
        notice = Notice("入力", READER_NOT_FOUND)
    # warnings は入れない (core/_notices.html は warnings も回すので、hidden のカメラの帯の包みにも描かれるため)。
    camera_notices = {key: Notice("入力", text) for key, text in READER_CAMERA_MESSAGES.items()}
    return render(
        request,
        "workorders/_workorder_reader_modal.html",
        {"form": WorkOrderFindForm(), "notice": notice, "camera_notices": camera_notices, "in_modal": True},
    )
