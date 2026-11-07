"""orders の画面 (02 の受注)。全部 core の土台を継承する (R15)。

一覧は 2 つ: 受注の一覧 (Order。受注の新規登録の画面と詳細へ移る入口) と、注文残の一覧 (OrderLine。明細ごとの残)。
出荷済みの数と残の数は列に持たず、services.annotate_remaining の同じ式で出す。
受注の明細の候補の口 (orderline_select) は、出荷の入力と指示書の発行 (GET の for=workorder) が受注の明細を選ぶ欄に使う。
"""

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import F, Prefetch
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView, BaseModalView, BaseSelectView, FilterSpec, url_or_none

from . import board, services
from .forms import OrderCancelForm, OrderForm, OrderLineNoBillClearForm, OrderLineNoBillForm
from .models import Order, OrderLine
from .services import annotate_remaining

#: 既定の一覧に出す受注の状態 (出荷の残りがある物)
OPEN_STATUSES = (Order.STATUS_OPEN, Order.STATUS_PARTIAL)


def _customer_choices():
    """得意先の絞りの候補。取引先の種類が得意先か両方の物だけ (協力企業だけの取引先は出さない)。並びは取引先の ordering。"""
    partner = Order._meta.get_field("customer").related_model
    rows = partner._default_manager.filter(role__in=(partner.ROLE_CUSTOMER, partner.ROLE_BOTH))
    return [(row.pk, str(row)) for row in rows]


def _open_orders(orders):
    """出荷の残りがある受注だけ (未出荷・一部出荷)。受注の一覧の印の when。"""
    return orders.filter(status__in=OPEN_STATUSES)


def _status_chosen(values):
    """状態の絞りに値があるか (受注の一覧の印の disabled_when。状態を選んだら印を切にして、その状態の全部を出す = N118)。"""
    return values.get("status") is not None


def _overdue_lines(lines):
    """納期が今日より前の明細だけ (暦の日で比べる。盤の判断の「遅れ」と同じ)。"""
    return lines.filter(due_date__lt=timezone.localdate())


def _this_week_due():
    """今週 (月曜から日曜) が納期の明細の組 (盤のタブ)。週の始まりは月曜で、定休日の設定は見ない。"""
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    return {"f_open": "1", "f_due_from": monday.isoformat(), "f_due_to": (monday + timedelta(days=6)).isoformat()}


def _this_month_ordered():
    """今月が受注日の受注の組 (受注の一覧のタブ)。状態は問わない (既定の印を切る)。"""
    today = timezone.localdate()
    first = today.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return {"f_open": "0", "f_ordered_from": first.isoformat(), "f_ordered_to": last.isoformat()}


class OrderListView(BaseListView):
    """受注の一覧。

    使う人: 営業 / いつ: 注文書が届いたときと、受注の状態を見るとき / 何のために: 受けた注文を受注として持ち、直す / 次にすること: 「新規登録」か行から詳細へ。指示書の発行は生産管理が生産管理ボードで行う
    """

    model = Order
    screen_name = "受注"
    screen_help = "受けた注文の一覧。既定は出荷の残りがある受注 (未出荷・一部出荷) だけを出します。"
    columns = ("order_no", "customer", "order_date", "customer_order_no", "status", "source")
    search_fields = ("order_no", "customer__name", "customer__code", "customer_order_no")
    # 一覧の絞り (土台の FilterSpec。決め 11-04 15:0x)。得意先は choice に候補の関数を渡す (lookup の候補は取引先の全部で、協力企業も出るため)。
    filters = (
        {"name": "status", "label": "状態", "kind": "choice", "field": "status"},
        {"name": "customer", "label": "得意先", "kind": "choice", "field": "customer", "choices": _customer_choices},
        {"name": "ordered", "label": "受注日", "kind": "date_range", "field": "order_date"},
        {"name": "due", "label": "納期", "kind": "date_range", "field": "lines__due_date"},
        # 既定で入の flag。状態を選んだら、印を切に描いて掛けない (disabled_when。N118 の「状態を選んだら既定を外す」を土台の形で。N143)。
        {"name": "open", "label": "出荷の残りがある受注だけ", "kind": "flag", "when": _open_orders, "default": True, "disabled_when": _status_chosen},
    )
    # 絞りのタブ (土台の presets。UX の案 3-A)。「すべて」は土台が足す (既定の印を切る)。
    presets = (
        {"label": "出荷の残りがある", "params": {"f_open": "1"}},
        {"label": "今月の受注", "params": _this_month_ordered},
        {"label": "出荷済", "params": {"f_status": Order.STATUS_SHIPPED}},
    )
    ordering = ("-order_date", "-pk")
    template_name = "orders/order_list.html"

    def get_queryset(self):
        # 土台 (super) が検索と絞り (f_。「出荷の残りがある受注だけ」の印も) を当てた後に、得意先を一緒に読む。
        orders = super().get_queryset().select_related("customer")
        if self.filter_chosen("due"):
            # 納期は明細の項目 (逆向きの FK)。期間に入る明細が 2 行あると同じ受注が 2 回出るので、1 回にする。
            orders = orders.distinct()
        return orders


class OrderLineListView(BaseListView):
    """注文残の一覧 (注文残の盤)。

    使う人: 生産管理 / いつ: 毎日と、注文が入ったとき /
    何のために: 注文ごとに、在庫で払い出せるか・指示書を出すか・進んでいるか・間に合うかを判断し、その場で手配する / 次にすること: 行の ⋮ か右クリックから出荷・指示書の発行・実績・発注へ
    """

    model = OrderLine
    screen_name = "生産管理ボード"
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
        # 預かり = 入荷の引当の合計 (receiving。決め 11-06 18:3x。N184)。並び替えは annotate の allocated_total (board.prefetch_for が足す)
        {"name": "board__allocated", "label": "預かり", "kind": "number", "sort_key": "allocated_total"},
        ("board__workorder_label", "指示書"),
        ("board__progress_label", "進捗"),
        ("board__plan_end", "計画の終了日"),
        ("board__judgement", "判断"),
        ("order__order_date", "受注日"),
        ("status_label", "状態"),
    )
    search_fields = ("order__customer__name", "item__part_no", "order__order_no", "order__customer_order_no")
    # 盤の絞り (決め 11-04 17:3x = 絞りは枠の 1 か所)。「出荷の残りがある物だけ」は既定で入の flag で、条件 (残が 0 より大きく、取消でない受注の明細) は
    # get_queryset が annotate_remaining の後に掛ける (土台は絞りを annotate より前に掛けるので、when では残を読めない)。
    filters = (
        {"name": "open", "label": "出荷の残りがある物だけ", "kind": "flag", "when": FilterSpec.as_is, "default": True},
        {"name": "due", "label": "納期", "kind": "date_range", "field": "due_date"},
        {"name": "overdue", "label": "納期が過ぎた物だけ", "kind": "flag", "when": _overdue_lines},
    )
    # 絞りのタブ (土台の presets。UX の案 3-A)。「出荷の残りがある」は既定の印と同じ組 (開いた直後に選ばれた見た目になる)。
    presets = (
        {"label": "出荷の残りがある", "params": {"f_open": "1"}},
        {"label": "納期が過ぎた", "params": {"f_open": "1", "f_overdue": "1"}},
        {"label": "今週が納期", "params": _this_week_due},
    )
    ordering = (F("due_date").asc(nulls_last=True), "order__order_no", "line_no")
    template_name = "orders/orderline_list.html"

    def get_queryset(self):
        # 納期の絞り (f_due・f_overdue) は土台 (super) が annotate_remaining の前 (WHERE) に掛ける。指示書と工程と実績は prefetch で読む (多の join を足さない)。
        lines = super().get_queryset().select_related("order", "order__customer", "item")
        lines = annotate_remaining(lines)
        if self.flag_on("open"):
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
    # 5 つとも Order の読むだけの property (models.py)。部品が入っていない会社では、そのタブを出さない。
    # 「会話」は元の受信した注文の相手の会社との会話 (C104)。中身は connect/_chat.html (order_detail.html の block tab_rows・tab_empty)。
    # 「仕入明細」は、この受注の明細に当てた得意先の仕入明細 (受けた仕入明細。C111)。行の字は番号・版・締め期間・状態。
    tabs = (
        ("指示書", "workorders"),
        ("出荷", "shipments"),
        ("請求書", "invoices"),
        ("会話", "chat_messages"),
        ("仕入明細", "received_self_invoices"),
    )
    template_name = "orders/order_detail.html"

    def get_queryset(self):
        # 明細は出荷済みの数と残の数 (annotate_remaining) を付けて、行番号の順に 1 回で読む。
        lines = annotate_remaining(OrderLine.objects.select_related("item")).order_by("line_no")
        return Order.objects.select_related("customer").prefetch_related(Prefetch("lines", queryset=lines))


class OrderModalView(BaseModalView):
    """受注の小窓。

    使う人: 営業・生産管理 / いつ: 受注の一覧で行の中身を画面を移らずに確かめたいとき / 何のために: 得意先・日付・状態・明細の先頭の数行・合計を見る / 次にすること: 要れば「詳細を見る」で受注の詳細へ

    受注の一覧の行の「要点を見る」(目の印。core/_row_summary_link.html) が開く (受け渡しの seizou-部品の6案-20261107/指示書.md の 6。
    本人の決め 11-07 18:2x〜18:4x「5はmodalでいいかな」= 右の覗き窓でなく今の小窓)。中身は orders/_modal.html (土台の項目に明細の先頭の数行と合計を足した物)。読むだけ。
    """

    model = Order
    screen_name = "受注の小窓"
    screen_help = "受注の要点 (得意先・日付・状態・明細・合計) を見るだけの小窓。受注の一覧の行の目の印から開き、詳細へ移れます。"

    def get_queryset(self):
        # 詳細 (OrderDetailView.get_queryset) と同じ読み方: 明細は出荷済みの数と残の数 (annotate_remaining) を付けて、行番号の順に 1 回で読む。
        lines = annotate_remaining(OrderLine.objects.select_related("item")).order_by("line_no")
        return Order.objects.select_related("customer").prefetch_related(Prefetch("lines", queryset=lines))


class OrderCreateView(BaseFormView):
    """受注の新規登録の入力。

    使う人: 営業 / いつ: 注文書が届いたとき / 何のために: 注文を受注として登録する / 次にすること: 保存すると生産管理ボードに出る (指示書の発行は生産管理)
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
    template_name = "orders/order_cancel.html"
    screen_name = "受注を取り消す"
    screen_help = "受注を取消にします。取消の後は、出荷も指示書も作れません。"


class OrderLineNoBillView(BaseFormView):
    """受注の明細を請求しないにする確かめの入力。

    使う人: 経理 / いつ: 出し切らずに終えた一式を請求しないと決めたとき / 何のために: その一式の明細を請求書の新規登録の一式の表と請求の候補から外す / 次にすること: 請求書の新規登録へ戻る

    入口は請求書の新規登録の一式の表の「請求しない」だけ (小窓。JS の無いときは画面の全体と ?next=)。テンプレートは土台の入力の画面と小窓のまま。
    """

    model = OrderLine
    form_class = OrderLineNoBillForm
    service = staticmethod(services.mark_no_bill)
    screen_name = "請求しないにする"
    screen_help = "出し切らずに終えた一式の明細を、請求書の新規登録の一式の表と請求の候補から外します。出荷の数と金額は今のまま数え、発行した請求書は変わりません。"


class OrderLineNoBillClearView(BaseFormView):
    """受注の明細の「請求しない」の印を外す確かめの入力。

    使う人: 経理 / いつ: 「請求しない」の印を間違えて付けたとき / 何のために: 印を外して、その明細を請求書の新規登録に戻す / 次にすること: 請求書の新規登録で金額を決めて載せる

    入口は受注の詳細の明細の ⋮ の「請求しないを外す」だけ (印のある明細・金額を見られる人で受注の書く権限の人だけ。小窓。JS の無いときは画面の全体と ?next=)。
    テンプレートは土台の入力の画面と小窓のまま。
    """

    model = OrderLine
    form_class = OrderLineNoBillClearForm
    service = staticmethod(services.clear_no_bill)
    screen_name = "請求しないを外す"
    screen_help = "受注の明細の「請求しない」の印を外します。出し切らずに終えた一式なら、請求書の新規登録の一式の表にまた並び、まだ請求書に入っていない出荷も請求書の新規登録に並びます。"


class OrderLineSelectView(BaseSelectView):
    model = OrderLine
    # 得意先の名前・取引先コード・得意先の注文番号でも探せる (相手の注文書を見ながら選ぶ。初見テスト R4 2-2)。
    search_fields = (
        "order__order_no",
        "item__part_no",
        "item__name",
        "order__customer__name",
        "order__customer__code",
        "order__customer_order_no",
    )

    def get_queryset(self):
        # GET の for が workorder なら指示書の発行の欄 (workorders/forms.py の order_line)。今の選ぶ欄と同じく取消でない受注の明細の全部を、新しい受注から返す。
        # 出荷の残りは足さない (字は「受注番号-行番号 品番 品名」。発行の画面に出荷の残りの数を出さない)。
        if self.request.GET.get("for") == "workorder":
            return OrderLine.objects.select_related("order", "item").exclude(order__status=Order.STATUS_CANCELLED)
        # 残が 0 より大きく、受注が取消でない明細だけ。表示の字は OrderLine.__str__
        # (受注番号-行番号 品番 品名 / 得意先 (注文番号 …) 残り n)。得意先は字に使うので一緒に読む。
        lines = OrderLine.objects.select_related("order", "order__customer", "item")
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
