"""orders の画面 (02 の受注)。全部 core の土台を継承する (R15)。

一覧は 2 つ: 受注の一覧 (Order。受注の新規登録の画面と詳細へ移る入口) と、注文残の一覧 (OrderLine。明細ごとの残)。
出荷済みの数と残の数は列に持たず、services.annotate_remaining の同じ式で出す。
受注の明細の候補の口 (orderline_select) は、出荷の入力が受注の明細を選ぶ欄に使う。
"""

from datetime import date

from django.contrib.auth.decorators import login_required
from django.db.models import F, Prefetch
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView, BaseSelectView, url_or_none

from . import board, services
from .forms import OrderCancelForm, OrderForm
from .models import Order, OrderLine
from .services import annotate_remaining

#: 既定の一覧に出す受注の状態 (出荷の残っている物)
OPEN_STATUSES = (Order.STATUS_OPEN, Order.STATUS_PARTIAL)


def _month(value):
    """GET の month (YYYY-MM) を、その月の 1 日と翌月の 1 日の組にする。読めなければ None。"""
    try:
        year, month = (int(part) for part in str(value or "").split("-"))
        return date(year, month, 1), date(year + month // 12, month % 12 + 1, 1)
    except ValueError:
        return None


def _customer_choices():
    """得意先の絞りの候補。取引先の種類が得意先か両方の物だけ (協力企業だけの取引先は出さない)。並びは取引先の ordering。"""
    partner = Order._meta.get_field("customer").related_model
    rows = partner._default_manager.filter(role__in=(partner.ROLE_CUSTOMER, partner.ROLE_BOTH))
    return [(row.pk, str(row)) for row in rows]


def _chosen(view, name):
    """一覧の絞り name (GET の f_<name>) に読めた値があるか。空と読めない値は、土台の FilterSpec と同じく無いのと同じ。"""
    return any(spec.values(view.request.GET) for spec in view.filter_specs() if spec.name == name)


class OrderListView(BaseListView):
    """受注の一覧。

    使う人: 営業 / いつ: 注文書が届いたときと、受注の状態を見るとき / 何のために: 受けた注文を受注として持ち、直す / 次にすること: 「新規登録」か行から詳細へ。指示書の発行は生産管理が注文残で行う
    """

    model = Order
    screen_name = "受注"
    screen_help = "受けた注文の一覧。既定は出荷の残っている受注 (受注・一部出荷) だけを出します。"
    columns = ("order_no", "customer", "order_date", "customer_order_no", "status", "source")
    search_fields = ("order_no", "customer__name", "customer__code", "customer_order_no")
    # 一覧の絞り (土台の FilterSpec。決め 11-04 15:0x)。得意先は choice に候補の関数を渡す (lookup の候補は取引先の全部で、協力企業も出るため)。
    filters = (
        {"name": "status", "label": "状態", "kind": "choice", "field": "status"},
        {"name": "customer", "label": "得意先", "kind": "choice", "field": "customer", "choices": _customer_choices},
        {"name": "ordered", "label": "受注日", "kind": "date_range", "field": "order_date"},
        {"name": "due", "label": "納期", "kind": "date_range", "field": "lines__due_date"},
    )
    ordering = ("-order_date", "-pk")
    template_name = "orders/order_list.html"

    def get_queryset(self):
        # 土台 (super) が検索と絞り (f_) を当てた後に、受注の一覧の既定を足す。
        orders = super().get_queryset().select_related("customer")
        if _chosen(self, "due"):
            # 納期は明細の項目 (逆向きの FK)。期間に入る明細が 2 行あると同じ受注が 2 回出るので、1 回にする。
            orders = orders.distinct()
        month = _month(self.request.GET.get("month"))
        if month is not None:
            # その月の受注日の、取消を除く全部の状態 (ダッシュボードの今月の受注のカードと同じ数え方)
            return orders.filter(order_date__gte=month[0], order_date__lt=month[1]).exclude(status=Order.STATUS_CANCELLED)
        if self.request.GET.get("all") == "1" or _chosen(self, "status"):
            # 状態を選んだときは、選んだ状態の全部を出す (既定の「出荷の残っている受注だけ」を外す)。
            return orders
        return orders.filter(status__in=OPEN_STATUSES)


class OrderLineListView(BaseListView):
    """注文残の一覧 (注文残の盤)。

    使う人: 生産管理 / いつ: 毎日と、注文が入ったとき /
    何のために: 注文ごとに、在庫で払い出せるか・指示書を出すか・進んでいるか・間に合うかを判断し、その場で手配する / 次にすること: 行の ⋮ か右クリックから出荷・指示書の発行・実績・発注へ
    """

    model = OrderLine
    screen_name = "注文残"
    screen_help = (
        "受注の明細ごとの残と、納期までの日数・指示書・進捗・計画の終了日・判断の印を並べる盤。"
        "既定は残のある明細 (取消を除く) を納期の近い順に出します。"
        "行の ⋮ か右クリックから、出荷・指示書の発行・実績・発注へ移れます。"
        "「納期まで」は、マスターの「休日」の日と定休日の曜日を除いた稼働日の数です。"
    )
    # board__<名前> は OrderLine.board (orders.board.BoardRow) の読むだけの値 (kind。並び替えは sort_key = 同じ順になる表の項目か annotate の列。合計は無い)。
    # shipped_total・remaining_total は annotate の列 (kind と sortable = 土台が右寄せと並び替えを出す)。
    # 合計の行はテンプレートの tag が出す (土台の total は書かない)。
    columns = (
        ("order__order_no", "受注番号"),
        ("order__customer", "得意先"),
        ("item", "部品"),
        ("due_date", "納期"),
        {"name": "board__days_left", "label": "納期まで", "kind": "number", "sort_key": "due_date"},
        ("qty", "数量"),
        {"name": "shipped_total", "label": "出荷済み", "kind": "number", "sortable": True},
        {"name": "board__acceptance_total", "label": "検収", "kind": "number", "sort_key": "acceptance_total"},
        {"name": "remaining_total", "label": "残", "kind": "number", "sortable": True},
        {"name": "board__stock_balance", "label": "在庫", "kind": "number", "sort_key": "stock_balance"},
        ("board__workorder_label", "指示書"),
        ("board__progress_label", "進捗"),
        ("board__plan_end", "計画の終了日"),
        ("board__judgement", "判断"),
        ("order__order_date", "受注日"),
        ("status_label", "状態"),
    )
    search_fields = ("order__customer__name", "item__part_no", "order__order_no", "order__customer_order_no")
    ordering = (F("due_date").asc(nulls_last=True), "order__order_no", "line_no")
    template_name = "orders/orderline_list.html"

    def get_queryset(self):
        # 納期の絞り込みは annotate_remaining の前 (WHERE)。指示書と工程と実績は prefetch で読む (多の join を足さない)。
        lines = super().get_queryset().select_related("order", "order__customer", "item")
        lines = annotate_remaining(board.apply_filters(lines, board.filters(self.request.GET)))
        if self.request.GET.get("all") != "1":
            lines = lines.filter(remaining_total__gt=0).exclude(order__status=Order.STATUS_CANCELLED)
        return board.prefetch_for(lines)


class OrderDetailView(BaseDetailView):
    """受注の詳細。

    使う人: 営業・生産管理 / いつ: 受注の中身と進み具合を確かめるとき / 何のために: 明細ごとの出荷済みと残、指示書・出荷・請求書を見る / 次にすること: 明細の行から出荷か指示書の発行へ。直すなら「編集」
    """

    model = Order
    screen_name = "受注の詳細"
    screen_help = (
        "受注の項目と明細。明細ごとに出荷済みの数と残の数を出し、明細の行から出荷と指示書の発行へ移れます。"
        "指示書・出荷・請求書のタブに、この受注の物が並びます。"
    )
    # 3 つとも Order の読むだけの property (models.py)。部品が入っていない会社では、そのタブを出さない。
    tabs = (
        ("指示書", "workorders"),
        ("出荷", "shipments"),
        ("請求書", "invoices"),
    )
    template_name = "orders/order_detail.html"

    def get_queryset(self):
        # 明細は出荷済みの数と残の数 (annotate_remaining) を付けて、行番号の順に 1 回で読む。
        lines = annotate_remaining(OrderLine.objects.select_related("item")).order_by("line_no")
        return Order.objects.select_related("customer").prefetch_related(Prefetch("lines", queryset=lines))


class OrderCreateView(BaseFormView):
    """受注の新規登録の入力。

    使う人: 営業 / いつ: 注文書が届いたとき / 何のために: 注文を受注として登録する / 次にすること: 保存すると注文残に出る (指示書の発行は生産管理)
    """

    model = Order
    form_class = OrderForm
    service = staticmethod(services.create_order)
    template_name = "orders/order_form.html"
    screen_name = "受注の新規登録"
    screen_help = "得意先と明細の行を入れて、受注を作ります。金額は保存のときに数量と単価から計算します。"


class OrderUpdateView(BaseFormView):
    """受注の編集の入力。

    使う人: 営業 / いつ: 数量や納期が変わったとき / 何のために: 受注の項目と明細を直す / 次にすること: 受注の詳細で確かめる
    """

    model = Order
    form_class = OrderForm
    service = staticmethod(services.update_order)
    template_name = "orders/order_form.html"
    screen_name = "受注の編集"
    screen_help = "受注の項目と明細を編集します。出荷済みより少ない数量にすると、会社の設定に従って止めるか知らせます。"


class OrderCancelView(BaseFormView):
    """受注を取り消す確かめの入力。

    使う人: 営業 / いつ: 注文が無くなったとき / 何のために: 受注を取消にする (出荷も指示書も作れなくなる) / 次にすること: 受注の一覧へ戻る
    """

    model = Order
    form_class = OrderCancelForm
    service = staticmethod(services.cancel_order)
    screen_name = "受注を取り消す"
    screen_help = "受注を取消にします。取消の後は、出荷も指示書も作れません。"


class OrderLineSelectView(BaseSelectView):
    model = OrderLine
    search_fields = ("order__order_no", "item__part_no", "item__name")

    def get_queryset(self):
        # 残が 0 より大きく、受注が取消でない明細だけ。表示の字は OrderLine.__str__ (受注番号-行番号 品番 品名)。
        lines = OrderLine.objects.select_related("order", "item")
        return (
            annotate_remaining(lines)
            .filter(remaining_total__gt=0)
            .exclude(order__status=Order.STATUS_CANCELLED)
            # 集計 (Sum) を含む queryset では model の Meta.ordering が効かないので、並びをここで決める。
            .order_by("order__order_no", "line_no", "pk")
        )


@login_required
def orderline_modal(request, pk):
    """受注の明細の小窓の口。その明細の部品の小窓 (masters:item_modal) へ移す。

    受注の明細を選ぶ欄 (出荷の入力) の「詳細を見る」が開く (core の RemoteSelect は <app>:<model>_modal の口が
    あれば link を出す)。明細そのものの小窓は持たない。部品の部品が入っていない会社では 404。
    """
    line = get_object_or_404(OrderLine.objects.only("item_id"), pk=pk)
    url = url_or_none("masters:item_modal", line.item_id)
    if url is None:
        raise Http404("部品の小窓がありません。")
    return HttpResponseRedirect(url)
