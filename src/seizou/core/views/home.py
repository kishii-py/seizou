"""最初の画面 (HomeView) と数字のカードの集計 (dashboard_stats) (core.views の package)。"""

from __future__ import annotations

from datetime import date

from django.db import DatabaseError
from django.db.models import Count, Sum
from django.utils import timezone
from django.views.generic import TemplateView

from seizou.core import access
from seizou.core import dashboard

from .base import ScreenMixin, load_model, model_url, screen_name_of, url_or_none


def count_or_none(queryset) -> int | None:
    try:
        return queryset.count()
    except DatabaseError:
        return None


#: 「今月の◯◯」のカードの (i) の 1 文に足す、先月との比べ方と細い線の説明 (dashboard_stats。カードの札と線は core/home.html)
COMPARE_HELP = "「先月より」は、今月の 1 日から今日までの数と、先月の 1 日から同じ日までの数との差です。細い線は今月の 1 日から今日までの日ごとの数です。"


def dashboard_stats() -> list[dict]:
    """最初の画面の数字のカード。部品が無い物は出さず、数は本物の集計だけ (DB が読めない物も出さない)。

    1 枚 = {"label", "value", "unit", "note", "help", "icon", "url", "url_name"} (と、数の下の 1 行 "detail" のあるカード)。"url_name" は url を作った URL の名前で、
    行き先のあるカードだけに付く (HomeView が、部署で開けない画面へ行くカードの url を外すのに使う)。金額のカード (支払の見込みと入金) は "money" が真で、
    HomeView が金額を見られない人には並びから外す (カードごと出さない)。
    "help" は数え方の 1 文 (何をどう数えたかと期間)。画面はカードの題の横の (i) の小窓に出す (作者の決め)。
    文は下の数え方の式と合わせる (式を変えたら文も直す)。
    「今月の◯◯」のカード (今月の受注・今月の支払の見込み・今月の入金) は "compare" に、先月の同じ日までとの差と今月の日ごとの数の細い線
    (core.dashboard の *_month_compare。読めなければ None) を持ち、help に比べ方の 1 文 (COMPARE_HELP) を足す。画面は札 (core/_stat_delta.html) と
    線 (svg) を数の横と下に出す (本人の決め 11-07 18:2x〜18:4x「2もいい」。受け渡しの seizou-部品の6案-20261107/指示書.md の 3)。
    今月でないカード (部品・取引先など) と、題に「今月」の無い 8 枚目 (手入力せずに取り込んだ明細) には付けない (指示書の「迷ったら出さない」)。
    """
    today = timezone.localdate()
    stats: list[dict] = []
    item_model = load_model("seizou.masters.models.Item")
    if item_model is not None:
        stats.append(
            {
                "label": "部品",
                "value": count_or_none(item_model._default_manager.all()),
                "unit": "件",
                "note": "部品マスターにある部品の数",
                "help": "部品マスターにある部品の数です。使用を止めた部品も数えます。",
                "icon": "box",
                "url": url_or_none("masters:item_list"),
                "url_name": "masters:item_list",
            }
        )
    partner_model = load_model("seizou.masters.models.Partner")
    if partner_model is not None:
        stats.append(
            {
                "label": "取引先",
                "value": count_or_none(partner_model._default_manager.all()),
                "unit": "社",
                "note": "得意先と協力企業の数",
                "help": "登録した取引先 (得意先・協力企業・両方) の数です。使用を止めた取引先も数えます。",
                "icon": "users",
                "url": url_or_none("masters:partner_list"),
                "url_name": "masters:partner_list",
            }
        )
    order_model = load_model("seizou.orders.models.Order")
    if order_model is not None:
        this_month = order_model._default_manager.filter(
            order_date__year=today.year, order_date__month=today.month
        ).exclude(status=order_model.STATUS_CANCELLED)
        order_list_url = url_or_none("orders:order_list")
        month_end = date.fromordinal(date(today.year + today.month // 12, today.month % 12 + 1, 1).toordinal() - 1)
        compare = dashboard.orders_month_compare(today)
        stats.append(
            {
                "label": "今月の受注",
                "value": count_or_none(this_month),
                "unit": "件",
                "note": f"{today.month} 月に受けた受注の件数 (取消を除く)",
                "help": "受注日が今月の受注の件数です。取消は数えません。" + (COMPARE_HELP if compare is not None else ""),
                "icon": "clipboard",
                "url": f"{order_list_url}?f_open=0&f_ordered_from={today:%Y-%m}-01&f_ordered_to={month_end}" if order_list_url else None,
                "url_name": "orders:order_list",
                "compare": compare,
            }
        )
    lines = dashboard.lines_to_ship()
    if lines is not None:
        lines_stat = {
            "label": "出荷の残りがある明細",
            "value": count_or_none(lines),
            "unit": "行",
            "note": "出荷が終わっていない受注の明細の数",
            "help": "受注の数量から出荷した数を引いた残りがある明細の数です。取消の受注は数えません。",
            "icon": "activity",
            "url": url_or_none("orders:orderline_list"),
            "url_name": "orders:orderline_list",
        }
        # 数の下の 1 行 = 受注の件数 (受注の一覧のタブ「出荷の残りがある」と今日やることの出荷の行と同じ数え方。作者の決めで、
        # 明細の行の数を受注の件数と読み違えそうと書かれたため)。数えられないときは 1 行ごと出さない (偽の 0 を置かない)。
        open_orders = dashboard.orders_to_ship()
        open_count = count_or_none(open_orders) if open_orders is not None else None
        if open_count is not None:
            lines_stat["detail"] = f"受注 {open_count:,} 件"
        stats.append(lines_stat)
    inbox_model = load_model("seizou.connect.models.Inbox")
    if inbox_model is not None:
        waiting = inbox_model._default_manager.filter(status=inbox_model.STATUS_RECEIVED)
        stats.append(
            {
                "label": "受信した注文",
                "value": count_or_none(waiting),
                "unit": "件",
                "note": "人が受け入れるのを待つ注文の数",
                "help": "受信した注文 (取引先から直接届いた物と、読み取った注文書から入った物) のうち、まだ受け入れも断りもしていない物の数です。",
                "icon": "inbox",
                "url": url_or_none("connect:inbox_list"),
                "url_name": "connect:inbox_list",
            }
        )
    # 6 枚目: 今月の支払の見込み (受入の部品があるとき。受け入れた数 × 発注の単価の合計 = 開発の記録 docs/nights/20261103/N42.md の要望 4)
    payable = dashboard.payable_this_month(today)
    if payable is not None:
        compare = dashboard.payable_month_compare(today)
        stats.append(
            {
                "label": "今月の支払の見込み",
                "value": payable,
                "unit": "",
                "note": f"{today.month} 月に受け入れた分の支払の見込み",
                "help": "受入日が今月の受入の金額 (受け入れた数量 × 発注の単価。相手が仕入明細で承諾した明細は、仕入明細の金額) の合計です。"
                + (COMPARE_HELP if compare is not None else ""),
                "icon": "yen",
                "url": url_or_none("purchasing:receipt_list"),
                "url_name": "purchasing:receipt_list",
                "money": True,
                "compare": compare,
            }
        )
    # 7 枚目: 入金 (入金の表 = billing の Payment があるとき。入金日が今月の、取消でない入金の金額の合計 = 開発の記録 docs/nights/20261103/N56.md の要望)
    payments = dashboard.payments_this_month(today)
    if payments is not None:
        compare = dashboard.payments_month_compare(today)
        stats.append(
            {
                "label": "今月の入金",
                "value": payments,
                "unit": "",
                "note": f"{today.month} 月に入金日のある入金の金額の合計 (取消を除く)",
                "help": "入金日が今月の入金の金額の合計です。取消は数えません。" + (COMPARE_HELP if compare is not None else ""),
                "icon": "yen",
                "url": url_or_none("billing:invoice_list"),
                "url_name": "billing:invoice_list",
                "money": True,
                "compare": compare,
            }
        )
    # 8 枚目: 手入力せずに取り込んだ明細 (受注の部品があるとき。読み取りと受信した注文から受注にした今月の明細の行数と、入力を省けた時間 = 開発の記録 docs/nights/20261104/N83.md)
    hands_free = dashboard.hands_free_lines(today)
    if hands_free is not None:
        minutes = f"{dashboard.minutes_per_line().normalize():f}"
        note = f"{today.month} 月に、読み取った注文書と受信した注文から受注にした明細の数 (1 行 {minutes} 分で数える)"
        accept = dashboard.accept_minutes_this_month(today)
        if accept is not None:
            note += f"。受付の平均 {dashboard.transcription_text(accept)}"
        stats.append(
            {
                "label": "手入力せずに取り込んだ明細",
                "value": hands_free,
                "unit": "行",
                "detail": f"入力を省けた時間 ≒ {dashboard.transcription_text(dashboard.saved_minutes(hands_free))}",
                "note": note,
                # 分の数は会社の設定 transcription_minutes_per_line (dashboard.minutes_per_line。既定は conf.DEFAULTS)
                "help": (
                    "受注日が今月の受注のうち、読み取った注文書か受信した注文から作った受注の明細の行の数です。取消は数えません。"
                    f"入力を省けた時間は、1 行を手入力する時間を {minutes} 分と置いた目安です。"
                ),
                "icon": "check",
                "url": None,
            }
        )
    return [stat for stat in stats if stat["value"] is not None]


def recent_orders(limit: int = 5) -> list[dict] | None:
    """最近の受注 (直近 limit 件)。受注の部品が無いか DB が読めなければ None。

    1 件 = {"obj", "url", "total", "line_count", "status"}。total は明細の金額の合計 (読むだけ)、
    status は状態の値 (open / partial / shipped / cancelled。印の色を選ぶのに使う)。
    """
    order_model = load_model("seizou.orders.models.Order")
    if order_model is None:
        return None
    try:
        rows = list(
            order_model._default_manager.select_related("customer")
            .annotate(total=Sum("lines__amount"), line_count=Count("lines"))
            .order_by("-order_date", "-pk")[:limit]
        )
    except DatabaseError:
        return None
    return [
        {
            "obj": row,
            "url": model_url(order_model, "detail", row.pk),
            "total": row.total,
            "line_count": row.line_count,
            "status": row.status,
        }
        for row in rows
    ]


def _package_dashboard_stats() -> list[dict]:
    """数字のカード。呼ぶ時に package (seizou.core.views) の dashboard_stats を読む。

    試験は seizou.core.views.dashboard_stats を差し替えて最初の画面を描く。この module の dashboard_stats を直に呼ぶと、
    package の名前の差し替えが届かないため (11-08 に views.py を package に分けた時の約束)。
    """
    from seizou.core import views

    return views.dashboard_stats()


class HomeView(ScreenMixin, TemplateView):
    """最初の画面 (ログインの後の移動先。全員が意識する KPI の置き場)。上から 数字のカード → 今日やること (手を動かす物のある段と移る先) と納期アラート → 出荷と生産の図 → 最近の受注と注文の入口 → よく使う画面 (☆ で登録した画面)。

    使う人: 全員 / いつ: ログイン直後と朝 / 何のために: 今日の数字と、出荷と生産の図、納期の遅れを見る / 次にすること: 今日やることのボタンから、その仕事の一覧へ。遅れの行から生産管理ボードへ。☆ で登録した画面へ
    """

    screen_name = "ダッシュボード"
    screen_help = "今日の数字、今日やること (手を動かす物の数と移る先)、出荷と生産の図、最近の受注と納期の遅れ。上の帯の ☆ で登録した画面が「よく使う画面」に並びます。"
    template_name = "core/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        # 部署で開けない画面へ行くカードは、url を外した写しにする (数は出し、押せない div で描く。作者の決め。
        # 作者の決め)。判定は今日やることの行を外すのと同じ引き方 (URL の名前の : の前の部品を can_open。記録は書かない)。
        # 並びの中の dict は書き換えず、新しい並びを作る (試験が同じ dict を返す関数に差し替えるため)。写しは下の画面の名前の枝に入らない。
        context["stats"] = [
            {**stat, "url": None}
            if stat.get("url_name") and not access.can_open(user, stat["url_name"].split(":")[0], record=False)
            else stat
            for stat in _package_dashboard_stats()
        ]
        for stat in context["stats"]:
            if stat.get("url"):
                # カードの行き先の link の字「<画面の名前>を見る」の名前。「?」の後ろ (絞りの GET) を付けたままでは引けないので外す。
                # 引けなければ空で、画面は「一覧を見る」を出す (詳細のパンくずの「一覧に戻る」と同じ考え)。作者の決め
                stat["screen"] = screen_name_of(stat["url"].split("?", 1)[0])
        context["recent_orders"] = recent_orders()
        context["orders_url"] = url_or_none("orders:order_list")
        context["alerts"] = dashboard.due_alerts()
        context["shipping"] = dashboard.shipping_trend()
        context["production"] = dashboard.production_trend()
        if not access.can_open(user, "orders", record=False):
            # 受注の部品を部署で開けない人には、納期アラートの「生産管理ボードを見る」と最近の受注の「受注の一覧を見る」を出さない
            # (テンプレートは値が無ければ link ごと出さない。行の受注番号の link は受注の詳細へ移り、詳細は部署を見ないので残す)。
            # due_alerts の値は書き換えず、写しの list_url だけを外す (作者の決め)。
            context["orders_url"] = None
            if context["alerts"] is not None:
                context["alerts"] = {**context["alerts"], "list_url": None}
        if not access.can_see_money(self.request.user):
            context["recent_orders"] = access.without_totals(context["recent_orders"])
            context["shipping"] = dashboard.without_amounts(context["shipping"])
            # 金額のカードは並びから外す (カードごと出さない。決め 11-05 09:3x)。並びの中の dict は書き換えず、新しい並びを作る。
            context["stats"] = [stat for stat in context["stats"] if not stat.get("money")]
        return context
