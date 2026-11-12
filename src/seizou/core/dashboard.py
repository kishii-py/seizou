"""ダッシュボードの納期アラートと月ごとの推移を読む関数 (第 3 波)。

- due_alerts: 納期を過ぎても出荷が終わっていない明細と、近く納期が来る明細を、受注の詳細への URL つきで返す。
- monthly_trend: 直近の月ごとの受注と出荷の件数と金額と、図 (inline SVG) に置く座標の字を返す。
  画面からは外れたが、ほかの所 (MCP の道具など) が使えるように関数と試験は残す。
- shipping_trend: 直近の月ごとの出荷の件数と数量と金額と、数量の図と金額の図の座標の字を返す。
- production_trend: 直近の月ごとの指示書の発行の件数と数量と、完成の数 (最後の工程の実績) と、図の座標の字を返す。
- without_amounts: shipping_trend の結果から金額を抜いた写しを返す (金額を見られない人の画面に使う)。
- payable_this_month: 今月の支払の見込み (受入日が今月の受入の支払の見込みの金額 (purchasing.services.annotate_payable。相手が仕入明細で承諾した明細は仕入明細の金額) の合計)。金額の閲覧の判定は呼ぶ側 (purchasing の tag)。
- payments_this_month: 今月の入金 (入金日が今月の、取消でない入金の金額の合計)。金額の閲覧の判定は呼ぶ側 (core の views の dashboard_stats と HomeView)。
- orders_month_compare・payable_month_compare・payments_month_compare: 「今月の◯◯」のカードの、先月の同じ日までとの差と、今月の日ごとの数と細い線 (sparkline) の座標
  (本人の決め 11-07 18:2x〜18:4x「2もいい」。受け渡しの seizou-部品の6案-20261107/指示書.md の 3)。同じ日の読み方は same_day_last_month。
- lines_to_ship: 出荷の残りがある明細 (注文残の一覧の既定と同じ数え方)。
- orders_to_ship: 出荷の残りがある受注 (受注の一覧のタブ「出荷の残りがある」と同じ数え方。カードの受注の件数と今日やることの出荷の行)。
- minutes_per_line・transcription_text: 1 行の手入力の分の設定と、分の字 (入力を省けた時間と受付の平均)。
- hands_free_lines: 今月、手入力せずに (読み取りと受信した注文から) 取り込んだ明細の行数。
- accept_minutes_this_month: 今月の受付 (受信した注文が届いてから承認まで) の平均の分。承認の日時の列が無ければ None。
- annotate_order_inlet: 取引先の queryset に、注文の入口 (seizou で直接・注文書の読み取り・手入力) の字の列 order_inlet を足す。
- order_inlets: 得意先ごとの注文の入口と今月の明細の数を、1 か所の受注に集まる図 (inline SVG) の座標つきで返す。
- flow_guide: 業務の流れ (受注から入金まで・発注から検収まで) の段ごとの画面の URL と、手を動かす物の件数と、流れごとの次にすること。
  発注から検収までの流れには、「発注」の前に「協力企業に出す」(協力企業に出していない工程)、「受入」の前に「渡す」(協力企業に渡していない明細) の段がある (見比べのまとめの M01 の 3 段目)。
- today_tasks: 「今日やること」の枠 (flow_guide の段のうち件数のある物を、移る先の絞りの GET つきで 1 行ずつ。部署で開けない行と段と、
  その人が進められない行 (行の書く action を編集の権限・金額の閲覧・部署で行えない。作者の決め) は外し、何も残らなければ None)。
  協力企業に出していない工程の行は指示書の一覧を絞り「協力企業に出していない工程があるだけ」で、協力企業に渡していない明細の行は発注の一覧を
  絞り「渡していない明細があるだけ」で開く (数え方と絞りは purchasing.services の同じ関数。数は行の数、一覧は指示書か発注の件数)。
  流れごとの段の行の後に、相手に届いていない注文回答・出荷の知らせ・検収の行 (_UNDELIVERED_TASKS。件数が 1 以上の物だけ) を足し、受信した注文・出荷・受入の素の一覧へ移す。
- flow_neighbors: 画面の URL の名前から、流れの中の前後の段の画面 (「案内」の小窓の link)。
- reading_match: 読み取りの一致率 (読んだ明細のうち、そのまま受注に残った行と人が直した行の数。intake の outcome が数える)。見張りの道 (受信した注文を経て受注にした読み取り) も数える。
- どれも読むだけで、表に書かない。金額の掛け算と丸めと合計は Python で行う (R03。DB で計算しない)。
- 読む部品が無いか DB が読めなければ None を返す (画面は節ごと出さない。偽の 0 を置かない)。

ほかの部品の model は apps.get_model で名前から引き、ほかの部品の services は model が引けた後に関数の中で
import する (部品の入っていない会社で落ちないため)。core の画面の module は import しない (画面の module が
この module を import するので輪になる)。色と形の class はテンプレートに固定の字で書き、ここは数と字だけを返す。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal
from decimal import ROUND_HALF_UP, InvalidOperation

from django.apps import apps
from django.db import DatabaseError
from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models import Case, CharField, Count, Exists, OuterRef, When
from django.db.models.functions import Coalesce
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.http import urlencode

from seizou.core import access, conf

# 図の枠 (viewBox の大きさ) と描く所。y は下へ増え、_BASELINE が値 0 の基線。目盛りの字 (右の端 = _TICK_LABEL_X) と
# 月の字 (_LABEL_Y の辺り) は svg の外の HTML の字で、svg の箱に対する割合の位置に置く (作者の決め。図と一緒に縮まない)。
_WIDTH = 640
_HEIGHT = 240
_PLOT_LEFT = 56
_PLOT_RIGHT = 632
_PLOT_TOP = 16
_BASELINE = 200
_LABEL_Y = 224
_TICK_LABEL_X = 48
# 棒の太さ・同じ月の 2 本の間の隙間・上の角の丸み。
_BAR_WIDTH = 20
_BAR_GAP = 2
_BAR_RADIUS = 4
# 目盛りの間の数 (0 を含めて 4〜5 本)。刻みは 1・2・5 × 10 の累乗。
_MIN_INTERVALS = 3
_MAX_INTERVALS = 4
_NICE_STEPS = (1, 2, 5)
_MAN = Decimal(10000)
_ZERO = Decimal(0)


def _model(app_label, name):
    """部品の model を名前で引く。部品が入っていなければ None。"""
    try:
        return apps.get_model(app_label, name)
    except LookupError:
        return None


def _url(name, *args):
    """URL の名前から URL を返す。無ければ None (画面の url_or_none と同じ形)。"""
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return None


def due_alerts(today=None, *, days=7, limit=10) -> dict | None:
    """納期アラート。受注の部品が無いか DB が読めなければ None。

    対象は、取消でない受注の明細のうち、納期が空でなく、残 (受注の数 − 出荷済みの数) が 0 より大きい物。
    遅れ = 納期が today より前 (days は today − 納期の日数)。近い = 納期が today から today + days まで
    (両端を含む。days は納期 − today の日数で、今日は 0)。それぞれ件数と、納期の古い順
    (同じ納期は受注番号・行番号の順) の上位 limit 行。

    返す物 = {"today", "days", "limit", "overdue": {"count", "rows"}, "soon": {"count", "rows"}, "list_url"}。
    1 行 = {"order_no", "order_url", "customer", "item", "qty", "remaining", "due_date", "days"}。
    得意先と部品は物のまま渡す (テンプレートが小窓の link を作るため)。
    """
    order_model = _model("orders", "Order")
    line_model = _model("orders", "OrderLine")
    if order_model is None or line_model is None:
        return None
    from seizou.orders.services import annotate_remaining

    if today is None:
        today = timezone.localdate()
    horizon = today + timedelta(days=days)

    def open_lines(**due):
        # 日付と取消の絞り込みは残を足す前に置く。残を足した後に多の join を足さない (出荷の数が重なるため)。
        queryset = line_model._default_manager.filter(due_date__isnull=False, **due).exclude(
            order__status=order_model.STATUS_CANCELLED
        )
        return (
            annotate_remaining(queryset)
            .filter(remaining_total__gt=0)
            .select_related("order__customer", "item")
            .order_by("due_date", "order__order_no", "line_no")
        )

    overdue = open_lines(due_date__lt=today)
    soon = open_lines(due_date__gte=today, due_date__lte=horizon)
    try:
        overdue_part = {
            "count": overdue.count(),
            "rows": [_alert_row(line, (today - line.due_date).days) for line in overdue[:limit]],
        }
        soon_part = {
            "count": soon.count(),
            "rows": [_alert_row(line, (line.due_date - today).days) for line in soon[:limit]],
        }
    except DatabaseError:
        return None
    return {
        "today": today,
        "days": days,
        "limit": limit,
        "overdue": overdue_part,
        "soon": soon_part,
        "list_url": _url("orders:orderline_list"),
    }


def _alert_row(line, days):
    order = line.order
    return {
        "order_no": order.order_no,
        "order_url": _url("orders:order_detail", order.pk),
        "customer": order.customer,
        "item": line.item,
        "qty": line.qty,
        "remaining": line.remaining_total,
        "due_date": line.due_date,
        "days": days,
    }


def _overdue_lines():
    """納期が過ぎた明細 (左のメニューの生産管理ボードの数。due_alerts の遅れと同じ条件)。受注の部品が無ければ None。

    取消でない受注の明細のうち、納期が今日より前で、残 (受注の数 − 出荷済みの数) が 0 より大きい物。
    """
    order_model = _model("orders", "Order")
    line_model = _model("orders", "OrderLine")
    if order_model is None or line_model is None:
        return None
    from seizou.orders.services import annotate_remaining

    # 日付と取消の絞り込みは残を足す前に置く (due_alerts と同じ。残を足した後に多の join を足さない)。
    queryset = line_model._default_manager.filter(due_date__isnull=False, due_date__lt=timezone.localdate()).exclude(
        order__status=order_model.STATUS_CANCELLED
    )
    return annotate_remaining(queryset).filter(remaining_total__gt=0)


def monthly_trend(today=None, *, months=6) -> dict | None:
    """月次の推移。today の月を含む直近 months か月を古い順に。受注の部品が無いか DB が読めなければ None。

    受注 = 取消でない受注の件数 (受注日の月) と、その明細の金額 (R03 で丸めた値) の合計。
    出荷 = 出荷の件数 (出荷日の月) と、1 件ずつ shipping.services.amount_of_shipment (部品の単位は round_amount(得意先, 出荷の数量 × 明細の単価)。
    一式の明細は明細を出し終えた出荷で一式の全額、ほかの出荷は 0 = 作者の決め) の合計 (請求書の明細と同じ式)。後で受注が取消になった出荷も数える (出荷は起きた事 = 現物優先)。
    出荷の部品が無ければ、出荷の件数と金額は None。

    返す物 = {"months", "has_shipping", "is_empty", "max_amount", "chart"}。
    1 月 = {"key" (YYYY-MM), "label" (M 月), "order_count", "order_amount", "shipment_count", "shipment_amount"}。
    chart は図に置く数で、座標は小数 1 桁の字 (テンプレートの数の書式に左右されないため)。
    """
    order_model = _model("orders", "Order")
    line_model = _model("orders", "OrderLine")
    if order_model is None or line_model is None:
        return None
    shipment_model = _model("shipping", "Shipment")
    has_shipping = shipment_model is not None

    if today is None:
        today = timezone.localdate()
    periods = _periods(today, months)
    start = date(periods[0][0], periods[0][1], 1)
    end = _first_of_next_month(*periods[-1])  # この日は含まない
    by_period = {
        period: {
            "key": f"{period[0]:04d}-{period[1]:02d}",
            "label": f"{period[1]} 月",
            "order_count": 0,
            "order_amount": _ZERO,
            "shipment_count": 0 if has_shipping else None,
            "shipment_amount": _ZERO if has_shipping else None,
        }
        for period in periods
    }
    cancelled = order_model.STATUS_CANCELLED
    try:
        orders = order_model._default_manager.filter(order_date__gte=start, order_date__lt=end).exclude(
            status=cancelled
        )
        for order_date in orders.values_list("order_date", flat=True):
            by_period[(order_date.year, order_date.month)]["order_count"] += 1
        lines = line_model._default_manager.filter(
            order__order_date__gte=start, order__order_date__lt=end
        ).exclude(order__status=cancelled)
        for order_date, amount in lines.values_list("order__order_date", "amount"):
            by_period[(order_date.year, order_date.month)]["order_amount"] += amount
        if has_shipping:
            from seizou.masters.services import company_rounding
            from seizou.shipping.services import amount_of_shipment

            rounding = company_rounding()  # 会社の端数の方法は輪の前に 1 回だけ読む (行ごとに会社の行を読まない)
            shipments = shipment_model._default_manager.filter(
                shipped_at__gte=start, shipped_at__lt=end
            ).select_related("order_line__order__customer")
            for shipment in shipments:
                row = by_period[(shipment.shipped_at.year, shipment.shipped_at.month)]
                row["shipment_count"] += 1
                row["shipment_amount"] += amount_of_shipment(shipment, rounding=rounding)
    except DatabaseError:
        return None

    rows = [by_period[period] for period in periods]
    amounts = [row["order_amount"] for row in rows] + [
        row["shipment_amount"] for row in rows if row["shipment_amount"] is not None
    ]
    max_amount = max(amounts, default=_ZERO)
    return {
        "months": rows,
        "has_shipping": has_shipping,
        "is_empty": all(row["order_count"] == 0 and not row["shipment_count"] for row in rows),
        "max_amount": max_amount,
        "chart": _chart(rows, max_amount, (("order_amount", "order_bars"), ("shipment_amount", "shipment_bars"))),
    }


def shipping_trend(today=None, *, months=6) -> dict | None:
    """出荷の推移。today の月を含む直近 months か月を古い順に。出荷の部品が無いか DB が読めなければ None。

    出荷の件数と数量の合計と金額の合計を、出荷日の月で数える。金額は 1 件ずつ
    shipping.services.amount_of_shipment (部品の単位は round_amount(受注の得意先, 出荷の数量 × 明細の単価)。一式の明細は明細を出し終えた出荷で
    一式の全額、ほかの出荷は 0 = 作者の決め) の合計 (月次の推移の出荷の側と同じ式)。
    後で受注が取消になった出荷も数える (出荷は起きた事 = 現物優先)。

    返す物 = {"months", "is_empty", "max_qty", "max_amount", "qty_chart", "amount_chart"}。
    1 月 = {"key" (YYYY-MM), "label" (M 月), "shipment_count", "shipment_qty", "shipment_amount"}。
    数量と金額は単位が違うので 1 つの軸に置かず、図を 2 つにする (qty_chart の棒は qty_bars、amount_chart は amount_bars)。
    """
    shipment_model = _model("shipping", "Shipment")
    if shipment_model is None:
        return None
    from seizou.masters.services import company_rounding
    from seizou.shipping.services import amount_of_shipment

    if today is None:
        today = timezone.localdate()
    periods = _periods(today, months)
    start = date(periods[0][0], periods[0][1], 1)
    end = _first_of_next_month(*periods[-1])  # この日は含まない
    by_period = {
        period: {
            "key": f"{period[0]:04d}-{period[1]:02d}",
            "label": f"{period[1]} 月",
            "shipment_count": 0,
            "shipment_qty": _ZERO,
            "shipment_amount": _ZERO,
        }
        for period in periods
    }
    try:
        rounding = company_rounding()  # 会社の端数の方法は輪の前に 1 回だけ読む (行ごとに会社の行を読まない)
        shipments = shipment_model._default_manager.filter(
            shipped_at__gte=start, shipped_at__lt=end
        ).select_related("order_line__order__customer")
        for shipment in shipments:
            row = by_period[(shipment.shipped_at.year, shipment.shipped_at.month)]
            row["shipment_count"] += 1
            row["shipment_qty"] += shipment.qty
            row["shipment_amount"] += amount_of_shipment(shipment, rounding=rounding)
    except DatabaseError:
        return None

    rows = [by_period[period] for period in periods]
    max_qty = max((row["shipment_qty"] for row in rows), default=_ZERO)
    max_amount = max((row["shipment_amount"] for row in rows), default=_ZERO)
    return {
        "months": rows,
        "is_empty": all(row["shipment_count"] == 0 for row in rows),
        "max_qty": max_qty,
        "max_amount": max_amount,
        "qty_chart": _chart(rows, max_qty, (("shipment_qty", "qty_bars"),)),
        "amount_chart": _chart(rows, max_amount, (("shipment_amount", "amount_bars"),)),
    }


def production_trend(today=None, *, months=6) -> dict | None:
    """生産の推移。today の月を含む直近 months か月を古い順に。指示書の部品が無いか DB が読めなければ None。

    発行 = 取消でない指示書の件数と数量の合計 (発行日の月)。
    完成 = 実績のうち、その指示書の最後の工程 (順が一番大きい工程) に入れた物の数の合計 (実績の日時を
    地域の時刻にした月)。指示書の数量で切らない (現物優先)。取消の指示書の実績は数えない。
    最後の工程は 2 回目の問い合わせで工程の順を読み、Python で決める (DB の join で実績の数を重ねないため)。
    進捗の部品が無ければ has_progress が偽で、完成の数は None。

    返す物 = {"months", "has_progress", "is_empty", "max_qty", "chart"}。
    1 月 = {"key" (YYYY-MM), "label" (M 月), "issued_count", "issued_qty", "completed_qty"}。
    chart の棒は issued_bars (左) と completed_bars (右)。どちらも数なので 1 つの軸に置く。
    """
    workorder_model = _model("workorders", "WorkOrder")
    step_model = _model("workorders", "WorkOrderStep")
    if workorder_model is None or step_model is None:
        return None
    progress_model = _model("progress", "Progress")
    has_progress = progress_model is not None

    if today is None:
        today = timezone.localdate()
    periods = _periods(today, months)
    start = date(periods[0][0], periods[0][1], 1)
    end = _first_of_next_month(*periods[-1])  # この日は含まない
    by_period = {
        period: {
            "key": f"{period[0]:04d}-{period[1]:02d}",
            "label": f"{period[1]} 月",
            "issued_count": 0,
            "issued_qty": _ZERO,
            "completed_qty": _ZERO if has_progress else None,
        }
        for period in periods
    }
    cancelled = workorder_model.STATUS_CANCELLED
    try:
        workorders = workorder_model._default_manager.filter(issued_on__gte=start, issued_on__lt=end).exclude(
            status=cancelled
        )
        for issued_on, qty in workorders.values_list("issued_on", "qty"):
            row = by_period[(issued_on.year, issued_on.month)]
            row["issued_count"] += 1
            row["issued_qty"] += qty
        if has_progress:
            # 期間の境は地域の時刻の 0 時 (naive な日時を渡すと境がずれる)。
            records = list(
                progress_model._default_manager.filter(
                    recorded_at__gte=timezone.make_aware(datetime.combine(start, time.min)),
                    recorded_at__lt=timezone.make_aware(datetime.combine(end, time.min)),
                )
                .exclude(workorder_step__workorder__status=cancelled)
                .values_list("qty", "recorded_at", "workorder_step_id", "workorder_step__workorder_id")
            )
            last_steps = {}  # 指示書の pk → (一番大きい順, その工程の pk)
            workorder_ids = {workorder_id for _qty, _at, _step_id, workorder_id in records}
            if workorder_ids:
                steps = step_model._default_manager.filter(workorder_id__in=workorder_ids)
                for workorder_id, seq, step_id in steps.values_list("workorder_id", "seq", "pk"):
                    if workorder_id not in last_steps or seq > last_steps[workorder_id][0]:
                        last_steps[workorder_id] = (seq, step_id)
            last_step_ids = {step_id for _seq, step_id in last_steps.values()}
            for qty, recorded_at, step_id, _workorder_id in records:
                if step_id not in last_step_ids:
                    continue
                local = timezone.localtime(recorded_at)
                row = by_period.get((local.year, local.month))
                if row is not None:
                    row["completed_qty"] += qty
    except DatabaseError:
        return None

    rows = [by_period[period] for period in periods]
    quantities = [row["issued_qty"] for row in rows] + [
        row["completed_qty"] for row in rows if row["completed_qty"] is not None
    ]
    max_qty = max(quantities, default=_ZERO)
    return {
        "months": rows,
        "has_progress": has_progress,
        "is_empty": all(row["issued_count"] == 0 and not row["completed_qty"] for row in rows),
        "max_qty": max_qty,
        "chart": _chart(rows, max_qty, (("issued_qty", "issued_bars"), ("completed_qty", "completed_bars"))),
    }


def without_amounts(shipping) -> dict | None:
    """出荷の推移 (shipping_trend の結果) から金額を抜いた写しを返す。None はそのまま。

    金額の図 (amount_chart) と一番大きい金額 (max_amount) と各月の金額を None にし、数量の側は残す。
    access の without_totals と同じ考えで、値を None にし、画面は金額の列を出さない。元の辞書は変えない。
    """
    if shipping is None:
        return None
    return {
        **shipping,
        "max_amount": None,
        "amount_chart": None,
        "months": [{**month, "shipment_amount": None} for month in shipping["months"]],
    }


def payable_this_month(today=None) -> Decimal | None:
    """今月の支払の見込み = 受入日が今月 (1 日から月末まで) の受入の支払の見込みの金額 (purchasing.services.annotate_payable。
    相手が仕入明細で承諾した明細は仕入明細の金額) の合計。受入が無ければ 0。受入の一覧の上の表 (payable_by_partner) と同じ数え方。

    保存した金額 (受け入れた数 × 発注の単価を purchasing.services が丸めた物と、仕入明細の明細の金額) を選んで足すだけで、DB で掛け算をしない。
    受入の表 (purchasing) が無いか DB が読めなければ None (画面はカードを出さない。偽の 0 を置かない)。
    """
    receipt_model = _model("purchasing", "Receipt")
    if receipt_model is None:
        return None
    if today is None:
        today = timezone.localdate()
    start = date(today.year, today.month, 1)
    end = _first_of_next_month(today.year, today.month)  # この日は含まない
    from seizou.purchasing.services import annotate_payable

    try:
        total = annotate_payable(receipt_model._default_manager.filter(received_on__gte=start, received_on__lt=end)).aggregate(
            total=Sum("payable_amount")
        )["total"]
    except DatabaseError:
        return None
    return _ZERO if total is None else Decimal(str(total))


def payments_this_month(today=None) -> Decimal | None:
    """今月の入金 = 入金日が今月 (1 日から月末まで) の、取消でない入金の金額の合計。入金が無ければ 0。

    保存した金額 (billing.services が記録した物) を足すだけで、DB で掛け算をしない。
    入金の表 (billing の Payment) が無いか DB が読めなければ None (画面はカードを出さない。偽の 0 を置かない)。
    """
    payment_model = _model("billing", "Payment")
    if payment_model is None:
        return None
    if today is None:
        today = timezone.localdate()
    start = date(today.year, today.month, 1)
    end = _first_of_next_month(today.year, today.month)  # この日は含まない
    try:
        total = payment_model._default_manager.filter(
            status=payment_model.STATUS_RECORDED, received_on__gte=start, received_on__lt=end
        ).aggregate(total=Sum("amount"))["total"]
    except DatabaseError:
        return None
    return _ZERO if total is None else Decimal(str(total))


# ---- 「今月」のカードの、先月の同じ日までとの差と、日ごとの細い線 (本人の決め 11-07 18:2x〜18:4x「2もいい」。受け渡しの seizou-部品の6案-20261107/指示書.md の 3) ----

#: 細い線 (sparkline) の枠 (viewBox) と内側の余白。線は箱の幅に伸び縮みする (preserveAspectRatio="none") ので、縦横の比は持たない
_SPARK_WIDTH = 100
_SPARK_HEIGHT = 24
_SPARK_PAD = 2


def same_day_last_month(today) -> date:
    """先月の、今日と同じ日。先月にその日が無ければ先月の末日 (3/31 → 2/28 か 2/29、5/31 → 4/30)。

    訳: 「今月の◯◯」のカードの差は、月の初めに今月の数が小さく見えないよう、先月の 1 日から同じ日までと比べる (指示書の 3)。
    """
    last_month_end = date(today.year, today.month, 1) - timedelta(days=1)
    return date(last_month_end.year, last_month_end.month, min(today.day, last_month_end.day))


def _per_day(queryset, date_field, start, end, amount_field=None) -> list:
    """start から end (含まない) までの日ごとの数の並び。amount_field が無ければ件数、あれば保存した金額の合計 (Python で足す = R03。DB で掛け算をしない)。

    問い合わせは 1 回 (values_list)。記録の無い日は 0。
    """
    rows = queryset.filter(**{f"{date_field}__gte": start, f"{date_field}__lt": end})
    totals = {}
    if amount_field is None:
        zero = 0
        for day in rows.values_list(date_field, flat=True):
            totals[day] = totals.get(day, 0) + 1
    else:
        zero = _ZERO
        for day, amount in rows.values_list(date_field, amount_field):
            totals[day] = totals.get(day, _ZERO) + Decimal(str(amount))
    return [totals.get(start + timedelta(days=offset), zero) for offset in range((end - start).days)]


def sparkline(values) -> dict:
    """細い線の座標。{"width", "height", "points" (polyline の points の字 "x,y x,y …")}。

    値を枠の高さに収めて左から右へ等間隔に並べる (一番大きい値が上の端)。値が全部 0 なら下の端の平らな線。
    日が 1 つ (月の 1 日) なら、その値の高さで左から右への平らな線。座標は小数 1 桁の字 (_num)。
    """
    top = max(values, default=0)
    inner_width = _SPARK_WIDTH - 2 * _SPARK_PAD
    inner_height = _SPARK_HEIGHT - 2 * _SPARK_PAD

    def y_of(value):
        ratio = float(value) / float(top) if top else 0.0
        return _SPARK_PAD + inner_height * (1 - ratio)

    if len(values) <= 1:
        y = _num(y_of(values[0]) if values else _SPARK_PAD + inner_height)
        points = [(_num(_SPARK_PAD), y), (_num(_SPARK_PAD + inner_width), y)]
    else:
        step = inner_width / (len(values) - 1)
        points = [(_num(_SPARK_PAD + step * index), _num(y_of(value))) for index, value in enumerate(values)]
    return {"width": _SPARK_WIDTH, "height": _SPARK_HEIGHT, "points": " ".join(f"{x},{y}" for x, y in points)}


def _month_compare(queryset, date_field, today, amount_field=None) -> dict:
    """今月の 1 日から今日までの日ごとの数と合計、先月の 1 日から同じ日までの合計、その差。読むだけ (問い合わせは 2 回)。

    返す物 = {"this": 今月 (1 日〜今日) の合計, "last": 先月 (1 日〜同じ日) の合計, "delta": this − last, "delta_abs": 差の大きさ,
    "direction": "up" (増えた) / "down" (減った) / "same", "until": 先月の比べる最後の日, "days": 今月の日ごとの数の並び (1 日〜今日), "spark": sparkline(days)}。
    今月の側は今日までで数える (今日より後の日付の記録はカードの数には入るが差には入らない = 比べる期間を同じ長さにするため)。
    """
    this_start = date(today.year, today.month, 1)
    days = _per_day(queryset, date_field, this_start, today + timedelta(days=1), amount_field)
    until = same_day_last_month(today)
    last_days = _per_day(queryset, date_field, date(until.year, until.month, 1), until + timedelta(days=1), amount_field)
    zero = _ZERO if amount_field else 0
    this_total = sum(days, zero)
    last_total = sum(last_days, zero)
    delta = this_total - last_total
    return {
        "this": this_total,
        "last": last_total,
        "delta": delta,
        "delta_abs": abs(delta),
        "direction": "up" if delta > 0 else "down" if delta < 0 else "same",
        "until": until,
        "days": days,
        "spark": sparkline(days),
    }


def orders_month_compare(today=None) -> dict | None:
    """「今月の受注」の、先月の同じ日までとの差と今月の日ごとの件数 (_month_compare。受注日で数え、取消を除く = カードと同じ数え方)。

    受注の部品が無いか DB が読めなければ None (カードは差と線を出さない)。
    """
    order_model = _model("orders", "Order")
    if order_model is None:
        return None
    if today is None:
        today = timezone.localdate()
    try:
        return _month_compare(order_model._default_manager.exclude(status=order_model.STATUS_CANCELLED), "order_date", today)
    except DatabaseError:
        return None


def payable_month_compare(today=None) -> dict | None:
    """「今月の支払の見込み」の、先月の同じ日までとの差と今月の日ごとの金額 (受入日。保存した受入の金額を足す = payable_this_month と同じ数え方)。

    受入の表 (purchasing) が無いか DB が読めなければ None。
    """
    receipt_model = _model("purchasing", "Receipt")
    if receipt_model is None:
        return None
    if today is None:
        today = timezone.localdate()
    from seizou.purchasing.services import annotate_payable

    try:
        return _month_compare(annotate_payable(receipt_model._default_manager.all()), "received_on", today, amount_field="payable_amount")
    except DatabaseError:
        return None


def payments_month_compare(today=None) -> dict | None:
    """「今月の入金」の、先月の同じ日までとの差と今月の日ごとの金額 (入金日。取消でない入金 = payments_this_month と同じ数え方)。

    入金の表 (billing の Payment) が無いか DB が読めなければ None。
    """
    payment_model = _model("billing", "Payment")
    if payment_model is None:
        return None
    if today is None:
        today = timezone.localdate()
    try:
        return _month_compare(
            payment_model._default_manager.filter(status=payment_model.STATUS_RECORDED), "received_on", today, amount_field="amount"
        )
    except DatabaseError:
        return None


MAX_MINUTES_PER_LINE = Decimal(60)  # 1 行の手入力の分の上限。これを超える値と 0 以下は既定に戻す


def minutes_per_line() -> Decimal:
    """会社の設定 transcription_minutes_per_line (1 行の手入力にかかる分) を Decimal で返す。真偽・数でない字・有限でない数・0 以下・MAX_MINUTES_PER_LINE を超える数は既定 (conf.DEFAULTS) に戻す (画面を 500 にしないよう、例外を投げない。core.calendar の定休日と同じ考え)。"""
    default = Decimal(conf.DEFAULTS["transcription_minutes_per_line"])
    value = conf.setting("transcription_minutes_per_line")
    if isinstance(value, bool):
        return default
    try:
        minutes = Decimal(str(value))
    except InvalidOperation:
        return default
    if not minutes.is_finite() or minutes <= 0 or minutes > MAX_MINUTES_PER_LINE:
        return default
    return minutes


def transcription_text(minutes: int) -> str:
    """分の字 (入力を省けた時間と受付の平均)。60 分より少なければ「m 分」、60 分からは「h 時間 m 分」(m が 0 なら「h 時間」)。"""
    hours, rest = divmod(minutes, 60)
    if hours == 0:
        return f"{rest} 分"
    if rest == 0:
        return f"{hours:,} 時間"
    return f"{hours:,} 時間 {rest} 分"


def saved_minutes(lines: int) -> int:
    """lines × 1 行の手入力の分 (minutes_per_line) を四捨五入した分の整数 (ROUND_HALF_UP)。"""
    return int((lines * minutes_per_line()).to_integral_value(rounding=ROUND_HALF_UP))


def hands_free_lines(today=None) -> int | None:
    """今月 (受注日が today の月) の、取消でない受注の明細のうち、受注の入口が読み取りか受信した注文の行数。受注の部品が無いか DB が読めなければ None。問い合わせは 1 回 (count)。

    入口は受注の列 (Order.source) で、明細の列ではない。
    """
    order_model = _model("orders", "Order")
    line_model = _model("orders", "OrderLine")
    if order_model is None or line_model is None:
        return None
    if today is None:
        today = timezone.localdate()
    start = date(today.year, today.month, 1)
    end = _first_of_next_month(today.year, today.month)  # この日は含まない
    try:
        return (
            line_model._default_manager.filter(
                order__order_date__gte=start,
                order__order_date__lt=end,
                order__source__in=(order_model.SOURCE_INTAKE, order_model.SOURCE_INBOX),
            )
            .exclude(order__status=order_model.STATUS_CANCELLED)
            .count()
        )
    except DatabaseError:
        return None


def _average_minutes(pairs) -> int | None:
    """(届いた日時, 承認の日時) の組の並びから、かかった分の平均を四捨五入した整数。どちらかが None の組と、承認が届いたより前の組は飛ばす。組が 1 つも無ければ None。"""
    spans = [
        Decimal(str((accepted - received).total_seconds())) / 60
        for received, accepted in pairs
        if received is not None and accepted is not None and accepted >= received
    ]
    if not spans:
        return None
    return int((sum(spans) / len(spans)).to_integral_value(rounding=ROUND_HALF_UP))


def accept_minutes_this_month(today=None) -> int | None:
    """今月 (承認の日時が today の月。地域の時刻) に承認した受信した注文の、届いてから承認までの分の平均。承認の日時の列 (Inbox.accepted_at。開発の記録 docs/nights/20261104/N82.md が足す) が無い・受信箱の部品が無い・今月の承認が無い・DB が読めなければ None。列があれば問い合わせは 1 回。"""
    from django.core.exceptions import FieldDoesNotExist

    inbox_model = _model("connect", "Inbox")
    if inbox_model is None:
        return None
    try:
        inbox_model._meta.get_field("accepted_at")
    except FieldDoesNotExist:
        return None
    if today is None:
        today = timezone.localdate()
    start = date(today.year, today.month, 1)
    end = _first_of_next_month(today.year, today.month)  # この日は含まない
    try:
        pairs = list(
            inbox_model._default_manager.filter(
                status=inbox_model.STATUS_ACCEPTED,
                # 期間の境は地域の時刻の 0 時 (production_trend と同じ)。
                accepted_at__gte=timezone.make_aware(datetime.combine(start, time.min)),
                accepted_at__lt=timezone.make_aware(datetime.combine(end, time.min)),
            ).values_list("received_at", "accepted_at")
        )
    except DatabaseError:
        return None
    return _average_minutes(pairs)


def lines_to_ship():
    """出荷の残りがある明細。注文残の一覧の既定 (orders の OrderLineListView = 残が 0 より大きく、取消でない受注の明細) と
    同じ数え方で、カードの数と押した先の一覧の件数を合わせる。部品が無ければ None。"""
    order_model = _model("orders", "Order")
    line_model = _model("orders", "OrderLine")
    if order_model is None or line_model is None:
        return None
    from seizou.orders.services import annotate_remaining

    return (
        annotate_remaining(line_model._default_manager.all())
        .filter(remaining_total__gt=0)
        .exclude(order__status=order_model.STATUS_CANCELLED)
    )


def _periods(today, months):
    """today の月を含む直近 months か月の (年, 月) を古い順に返す。月は年と月の組で数える (日の引き算にしない)。"""
    last = today.year * 12 + today.month - 1
    return [(index // 12, index % 12 + 1) for index in range(last - months + 1, last + 1)]


def _first_of_next_month(year, month):
    return date(year + month // 12, month % 12 + 1, 1)


def _num(value):
    """座標を小数 1 桁の字にする。"""
    return f"{value:.1f}"


def _scale(max_value):
    """目盛りの刻みと間の数を返す。

    刻みは 1・2・5 × 10 の累乗のうち、間が 4 以下で一番上が max_value 以上になる一番小さい物。
    間は 3 か 4 (0 を含めて目盛りは 4〜5 本)。max_value が 1 より小さい (0 を含む) ときは 1 として数え、0 で割らない。
    """
    target = max(max_value, Decimal(1))
    power = Decimal(1)
    while True:
        for base in _NICE_STEPS:
            step = base * power
            intervals = int((target / step).to_integral_value(rounding=ROUND_CEILING))
            if intervals <= _MAX_INTERVALS:
                return step, max(intervals, _MIN_INTERVALS)
        power *= 10


def _tick_label(value, top):
    """目盛りの字。一番上が 1 万未満なら 3 桁区切りの全部、1 万以上なら万の単位 (小数は 1 桁まで)。1 本の軸で混ぜない。"""
    if top < _MAN:
        return f"{int(value):,}"
    man = value / _MAN
    if man == man.to_integral_value():
        return f"{int(man):,} 万"
    return f"{man:,.1f} 万"


def _bar_path(left, top_y):
    """棒の形 (path の d)。上の 2 つの角だけ丸み (高さが丸みより低ければ高さまで)、基線の側は角のまま。"""
    radius = min(_BAR_RADIUS, _BASELINE - top_y)
    right = left + _BAR_WIDTH
    return (
        f"M{_num(left)},{_num(_BASELINE)}"
        f" L{_num(left)},{_num(top_y + radius)}"
        f" Q{_num(left)},{_num(top_y)} {_num(left + radius)},{_num(top_y)}"
        f" L{_num(right - radius)},{_num(top_y)}"
        f" Q{_num(right)},{_num(top_y)} {_num(right)},{_num(top_y + radius)}"
        f" L{_num(right)},{_num(_BASELINE)} Z"
    )


def _chart(rows, max_value, series):
    """図に置く数。series = ((行の鍵, 棒の並びの名前), ...) の順に、月ごとの棒を左から並べる。

    月の帯の中央に、系列の数だけ棒をまとめて置く (全体の幅 = 本数 × 太さ + (本数 − 1) × 隙間。
    2 本なら中央の左右に隙間の半分ずつ空け、1 本なら中央に 1 本)。値が 0 か None の棒は入れない。
    返す物は枠と目盛りと月の字の数と、棒の並びの名前ごとの棒の並び (1 本 = {"key", "value", "d"})。
    目盛りと月の字は svg の外の HTML の字なので、svg の箱に対する割合 (0〜100 の字) も返す: 目盛り 1 本の "top" (箱の上の端から y まで)・
    月 1 つの "left" (箱の左の端から帯の中央まで)・"tick_label_right" (箱の右の端から目盛りの字の右の端 _TICK_LABEL_X まで)。
    """
    step, intervals = _scale(max_value)
    top = step * intervals
    plot_height = _BASELINE - _PLOT_TOP
    band = (_PLOT_RIGHT - _PLOT_LEFT) / len(rows)
    group_width = len(series) * _BAR_WIDTH + (len(series) - 1) * _BAR_GAP

    def y_of(value):
        return _BASELINE - float(value / top) * plot_height

    ticks = [
        {
            "y": _num(y_of(step * index)),
            "top": _num(y_of(step * index) * 100 / _HEIGHT),
            "label": _tick_label(step * index, top),
        }
        for index in range(intervals + 1)
    ]
    bars_by_name = {name: [] for _key, name in series}
    labels = []
    for index, row in enumerate(rows):
        center = _PLOT_LEFT + band * (index + 0.5)
        labels.append({"x": _num(center), "left": _num(center * 100 / _WIDTH), "label": row["label"]})
        first_left = center - group_width / 2
        for position, (key, name) in enumerate(series):
            value = row[key]
            if value:
                left = first_left + position * (_BAR_WIDTH + _BAR_GAP)
                bars_by_name[name].append({"key": row["key"], "value": value, "d": _bar_path(left, y_of(value))})
    return {
        "width": _WIDTH,
        "height": _HEIGHT,
        "left": _num(_PLOT_LEFT),
        "right": _num(_PLOT_RIGHT),
        "tick_label_x": _num(_TICK_LABEL_X),
        "tick_label_right": _num((_WIDTH - _TICK_LABEL_X) * 100 / _WIDTH),
        "label_y": _num(_LABEL_Y),
        "top": top,
        "ticks": ticks,
        **bars_by_name,
        "labels": labels,
    }


INLETS = (("direct", "seizou で直接"), ("intake", "注文書の読み取り"), ("manual", "手入力"))  # 注文の入口の鍵と字 (取引先の一覧の列と、注文の入口の図の色の順)


def annotate_order_inlet(partners, today=None):
    """取引先の queryset に、注文の入口の字の列 order_inlet を足す (読むだけ。問い合わせを足さない = 副問い合わせ)。得意先 (両方を含む) のうち、受ける向きのつながり (connect の Connection) がある = seizou で直接、無くて今月を含む直近 3 か月 (_periods(today, 3) の最初の月の 1 日から) に読み取りの行 (intake の Extraction) がある = 注文書の読み取り、それ以外 = 手入力。協力企業だけの取引先は None。connect と intake の部品が無ければ、その枝を飛ばす。"""
    if today is None:
        today = timezone.localdate()
    labels = dict(INLETS)
    whens = [When(role=partners.model.ROLE_SUBCONTRACTOR, then=Value(None))]
    connection_model = _model("connect", "Connection")
    if connection_model is not None:
        receiving = connection_model._default_manager.filter(
            partner=OuterRef("pk"), direction=connection_model.DIRECTION_RECEIVE
        )
        whens.append(When(Exists(receiving), then=Value(labels["direct"])))
    extraction_model = _model("intake", "Extraction")
    if extraction_model is not None:
        first_year, first_month = _periods(today, 3)[0]
        # 期間の境は地域の時刻の 0 時 (production_trend と同じ)。
        since = timezone.make_aware(datetime.combine(date(first_year, first_month, 1), time.min))
        recent = extraction_model._default_manager.filter(customer=OuterRef("pk"), read_at__gte=since)
        whens.append(When(Exists(recent), then=Value(labels["intake"])))
    return partners.annotate(
        order_inlet=Case(*whens, default=Value(labels["manual"]), output_field=CharField())
    )


# 注文の入口の図の枠と置き場 (y は下へ増え、単位は画面の px)。得意先の行 (丸・名前・行の数) と受注の箱は home.html の
# HTML の字で置き、svg は丸と箱の間の線だけを描く (作者の決め。字と箱が図と一緒に縮まない)。svg の高さは viewBox の高さと
# 同じ px にし、幅は月次の図と同じ _WIDTH を丸と箱の間の幅に伸ばす (preserveAspectRatio="none"。線は vector-effect で 1px の
# まま)。線の左の端 (x = 0) は丸の右の端、右の端 (x = _WIDTH) は箱の左の端。1 行の帯の高さ・上下の余白・受注の箱の高さ・
# 名前の字数の上限。
_INLET_ROW = 28
_INLET_TOP = 0
_INLET_BOX_HEIGHT = 56
_INLET_NAME_MAX = 12
# svg に字と丸と箱を描いていた時の置き場 (名前の字の右の端・丸の中心と半径・受注の箱の左の端と幅)。今の home.html は使わない。
# 返す物の鍵 (label_x・dot_x・dot_r と box の x・width・cx・label_y・count_y) を変えないために残す (作者の決め「今の座標の鍵は残す」)。
_INLET_LABEL_X = 232
_INLET_DOT_X = 248
_INLET_DOT_R = 7
_INLET_BOX_LEFT = 448
_INLET_BOX_WIDTH = 168


def order_inlets(today=None, *, limit=12) -> dict | None:
    """ダッシュボードの「注文の入口」の図。得意先 (両方を含む) を今月 (受注日が today の月) の取消でない受注の明細の多い順 (同じ数はコードの順) に limit 社まで並べ、注文の入口 (annotate_order_inlet) と今月の明細の数を、図の座標つきで返す。残りの得意先は「ほか n 社」の 1 行にまとめる。得意先が 1 社も無い・取引先か受注の部品が無い・DB が読めなければ None。問い合わせは 1 回。

    返す物は _inlet_chart の形。
    """
    partner_model = _model("masters", "Partner")
    order_model = _model("orders", "Order")
    if partner_model is None or order_model is None or _model("orders", "OrderLine") is None:
        return None
    if today is None:
        today = timezone.localdate()
    start = date(today.year, today.month, 1)
    end = _first_of_next_month(today.year, today.month)  # この日は含まない
    customers = partner_model._default_manager.filter(
        role__in=(partner_model.ROLE_CUSTOMER, partner_model.ROLE_BOTH)
    )
    month_lines = Count(
        "orders__lines",
        filter=Q(orders__order_date__gte=start, orders__order_date__lt=end)
        & ~Q(orders__status=order_model.STATUS_CANCELLED),
    )
    try:
        partners = list(
            annotate_order_inlet(customers, today).annotate(month_lines=month_lines).order_by("-month_lines", "code")
        )
    except DatabaseError:
        return None
    if not partners:
        return None
    keys = {label: key for key, label in INLETS}
    rows = []
    for partner in partners[:limit]:
        name = partner.short_name or partner.name
        if len(name) > _INLET_NAME_MAX:
            name = name[:_INLET_NAME_MAX] + "…"
        rows.append(
            {
                "name": name,
                "lines": partner.month_lines,
                "inlet": keys.get(partner.order_inlet, "manual"),
                "inlet_label": partner.order_inlet,
            }
        )
    rest = partners[limit:]
    others = {"count": len(rest), "lines": sum(partner.month_lines for partner in rest)} if rest else None
    return _inlet_chart(rows, others)


def _inlet_chart(rows, others) -> dict:
    """注文の入口の図の座標 (字は小数 1 桁 = _num)。1 行 = {"name" (略称か名前を _INLET_NAME_MAX 字まで。長ければ末尾に「…」), "lines", "inlet" (INLETS の鍵), "inlet_label", "y", "x1", "x2", "y2"} (線は丸の右の端 = svg の左の端から、箱の左の端 = svg の右の端の中央へ)。箱 = {"x", "y", "width", "height", "label_y", "count_y", "cx"}。others = {"count", "lines", "y"} か None。高さは行の数で決め、箱は行の帯の中央に置く。

    返す物 = {"width", "height", "row_height", "rows", "others", "box", "total_lines", "dot_x", "dot_r", "label_x"}。
    行の帯 (others の 1 行を含む) が箱より低いときは、帯を箱の高さの中央に置く。
    home.html は、svg の高さ (height の px)・HTML の 1 行の高さ (row_height の px)・箱の最小の高さ (box の height の px) を style に置き、
    行と svg と箱を縦の中央で揃える。y は px なので、HTML の行の中央と線の左の端が合う。
    dot_x・dot_r・label_x と box の x・width・cx・label_y・count_y は、svg に字と丸と箱を描いていた時の置き場で、今の home.html は使わない。
    """
    bands = len(rows) + (1 if others else 0)
    content = max(_INLET_ROW * bands, _INLET_BOX_HEIGHT)
    first_y = _INLET_TOP + (content - _INLET_ROW * bands) / 2 + _INLET_ROW / 2
    box_y = _INLET_TOP + (content - _INLET_BOX_HEIGHT) / 2
    box_middle = box_y + _INLET_BOX_HEIGHT / 2
    placed = [
        {
            **row,
            "y": _num(first_y + _INLET_ROW * index),
            "x1": _num(0),
            "x2": _num(_WIDTH),
            "y2": _num(box_middle),
        }
        for index, row in enumerate(rows)
    ]
    placed_others = None
    if others:
        placed_others = {**others, "y": _num(first_y + _INLET_ROW * len(rows))}
    total_lines = sum(row["lines"] for row in rows) + (others["lines"] if others else 0)
    return {
        "width": _WIDTH,
        "height": _INLET_TOP * 2 + content,
        "row_height": _num(_INLET_ROW),
        "rows": placed,
        "others": placed_others,
        "box": {
            "x": _num(_INLET_BOX_LEFT),
            "y": _num(box_y),
            "width": _num(_INLET_BOX_WIDTH),
            "height": _num(_INLET_BOX_HEIGHT),
            "label_y": _num(box_middle - 4),
            "count_y": _num(box_middle + 14),
            "cx": _num(_INLET_BOX_LEFT + _INLET_BOX_WIDTH / 2),
        },
        "total_lines": total_lines,
        "dot_x": _num(_INLET_DOT_X),
        "dot_r": _num(_INLET_DOT_R),
        "label_x": _num(_INLET_LABEL_X),
    }


# 業務の流れ (ダッシュボードの「はじめての方へ」の枠と、上の帯の「案内」の小窓の前後の段)。固定の字で、画面から直す口は無い。
# 1 段 = key・label・model (部品の有無を見る (app_label, name))・url_name・count (数える段の鍵。数えない段は None)・
# count_label・unit・next_text。部品の入っていない段は出さない (_flow_steps)。数え方は _FLOW_COUNTS の関数。
# 「受注」の段の移る先は受注の一覧 (受注の 3 つの入口 = 新規登録・注文書の読み取り・受信した注文の帯がそろう画面。作者の決め。
# 紙や FAX で注文を受ける会社が、導入の初日に空の受信した注文の画面へ着かないため)。数える物は受信した注文の未処理のままで、
# 「今日やること」の行の移る先 (_task_targets。受信した注文の一覧の未処理の絞り) は変えない。
FLOWS = (
    {
        "key": "sales",
        "title": "受注から入金まで",
        "steps": (
            {"key": "order", "label": "受注", "model": ("orders", "Order"), "url_name": "orders:order_list",
             "count": "inbox", "count_label": "受信した注文の未処理", "unit": "件",
             "next_text": "受信した注文を受け入れて、受注にします。"},
            {"key": "workorder", "label": "指示書", "model": ("workorders", "WorkOrder"), "url_name": "orders:orderline_list",
             "count": "workorder", "count_label": "指示書の無い明細", "unit": "行",
             "next_text": "生産管理ボードから、明細に指示書を出します。"},
            {"key": "shipment", "label": "出荷", "model": ("shipping", "Shipment"), "url_name": "shipping:shipment_list",
             "count": "shipment", "count_label": "出荷の残りがある受注", "unit": "件",
             "next_text": "出荷できる明細を出荷します。"},
            {"key": "delivery_note", "label": "納品書", "model": ("shipping", "DeliveryNote"), "url_name": "shipping:deliverynote_list",
             "count": "delivery_note", "count_label": "納品書に入っていない出荷", "unit": "件",
             "next_text": "出荷をまとめて納品書を出します。"},
            {"key": "invoice", "label": "請求書", "model": ("billing", "Invoice"), "url_name": "billing:invoice_list",
             "count": "invoice", "count_label": "請求書に入っていない出荷", "unit": "件",
             "next_text": "納品書に入った出荷をまとめて請求書を出します。"},
            {"key": "payment", "label": "入金", "model": ("billing", "Payment"), "url_name": "billing:invoice_list",
             "count": "payment", "count_label": "入金の残っている請求書", "unit": "件",
             "next_text": "入金があった請求書に入金を記録します。"},
        ),
    },
    {
        "key": "purchase",
        "title": "発注から検収まで",
        "steps": (
            # needs = 段の仕事 (工程を発注に出す) をする部品。移る先 (指示書の一覧) を開けても、この部品を部署で開けない人には段を出さない (_step_open)
            {"key": "subcontract", "label": "協力企業に出す", "model": ("purchasing", "PurchaseOrderLine"), "url_name": "workorders:workorder_list",
             "needs": "purchasing",
             "count": "subcontract", "count_label": "協力企業に出していない工程", "unit": "行",
             "next_text": "指示書の詳細の工程の行から、協力企業に出す工程を発注に出します。"},
            {"key": "purchase_order", "label": "発注", "model": ("purchasing", "PurchaseOrder"), "url_name": "purchasing:purchaseorder_list",
             "count": "purchase_draft", "count_label": "まだ送っていない発注", "unit": "件",
             "next_text": "下書きのままの発注を協力企業に送ります。"},
            {"key": "reply", "label": "注文回答", "model": ("connect", "Connection"), "url_name": "purchasing:purchaseorder_list",
             "count": "purchase_sent", "count_label": "返事を待つ発注", "unit": "件",
             "next_text": "協力企業の返事を待ちます。断られた発注は直して送り直します。"},
            {"key": "shipping_notice", "label": "出荷の知らせ", "model": ("connect", "ShippingNotice"), "url_name": "purchasing:purchaseorder_list",
             "count": None, "count_label": "", "unit": "", "next_text": ""},
            {"key": "handover", "label": "渡す", "model": ("purchasing", "PurchaseOrderLine"), "url_name": "purchasing:purchaseorder_list",
             "count": "handover", "count_label": "協力企業に渡していない明細", "unit": "行",
             "next_text": "現物を協力企業に渡したら、発注の明細の ⋮ の「渡した日」で日を入れます。"},
            {"key": "receipt", "label": "受入", "model": ("purchasing", "Receipt"), "url_name": "purchasing:receipt_list",
             "count": "receipt", "count_label": "受入の残っている知らせの明細", "unit": "行",
             "next_text": "届いた物を受入に記録します。"},
            {"key": "acceptance", "label": "検収", "model": ("connect", "Acceptance"), "url_name": "purchasing:receipt_list",
             "count": None, "count_label": "", "unit": "", "next_text": ""},
        ),
    },
)


def _flow_steps(flow):
    """流れの段のうち、部品の model が引けて URL がある段だけを、URL を足した写しにして並びの順に返す。問い合わせをしない。"""
    steps = []
    for step in flow["steps"]:
        if _model(*step["model"]) is None:
            continue
        url = _url(step["url_name"])
        if url is None:
            continue
        steps.append({**step, "url": url})
    return steps


def _flow_count(key):
    """数える段の鍵から件数 (1 回の問い合わせ)。数える元の部品が無いか DB が読めなければ None (偽の 0 を置かない)。"""
    queryset_of = _FLOW_COUNTS.get(key)
    queryset = queryset_of() if queryset_of is not None else None
    if queryset is None:
        return None
    try:
        return queryset.count()
    except DatabaseError:
        return None


def _received_inbox():
    """受信した注文のうち、人が受け入れていない物 (受信箱の状態が「届いた」)。"""
    inbox_model = _model("connect", "Inbox")
    if inbox_model is None:
        return None
    return inbox_model._default_manager.filter(status=inbox_model.STATUS_RECEIVED)


def without_workorder(queryset):
    """受注の明細の queryset を、未出荷か一部出荷の受注の明細のうち、取消でない指示書が 1 枚も無い物に狭める (読むだけ)。

    生産管理ボードの絞り「指示書の無い明細だけ」の when (orders.views) と、左のメニューと今日やることの「指示書の無い明細」の数
    (_lines_without_workorder) が、この 1 つの関数を使う (数と表が食い違わないため。決め 11-07 20:5x の 2。nav-01)。
    指示書の絞り込み (副問い合わせ) は残を足す前に置く (残を足した後に多の join を足さない。出荷の数が重なるため)。
    workorders の部品が無ければ、受注の状態の絞りだけ。
    """
    order_model = _model("orders", "Order")
    workorder_model = _model("workorders", "WorkOrder")
    if order_model is None:
        return queryset
    queryset = queryset.filter(order__status__in=(order_model.STATUS_OPEN, order_model.STATUS_PARTIAL))
    if workorder_model is not None:
        queryset = queryset.exclude(workorders__status=workorder_model.STATUS_ISSUED)
    return queryset


def _lines_without_workorder():
    """未出荷か一部出荷の受注の明細のうち、取消でない指示書が 1 枚も無く (without_workorder)、残が 0 より大きい物。"""
    order_model = _model("orders", "Order")
    line_model = _model("orders", "OrderLine")
    workorder_model = _model("workorders", "WorkOrder")
    if order_model is None or line_model is None or workorder_model is None:
        return None
    from seizou.orders.services import annotate_remaining

    return annotate_remaining(without_workorder(line_model._default_manager.all())).filter(remaining_total__gt=0)


def orders_to_ship():
    """出荷の残りがある受注 (状態が未出荷か一部出荷)。受注の一覧のタブ「出荷の残りがある」(orders の OPEN_STATUSES) と
    同じ数え方。ダッシュボードの「出荷の残りがある明細」のカードの「受注 n 件」と、今日やることの出荷の行の件数は、
    この 1 つの関数で数える (2 か所の件数を合わせるため。作者の決め)。部品が無ければ None。"""
    order_model = _model("orders", "Order")
    if order_model is None:
        return None
    return order_model._default_manager.filter(status__in=(order_model.STATUS_OPEN, order_model.STATUS_PARTIAL))


def _shipments_without_note():
    """発行の納品書に入っていない出荷 (納品書の無い出荷と、取消の納品書を指したままの出荷)。"""
    shipment_model = _model("shipping", "Shipment")
    note_model = _model("shipping", "DeliveryNote")
    if shipment_model is None or note_model is None:
        return None
    return shipment_model._default_manager.exclude(delivery_note__status=note_model.STATUS_ISSUED)


def _shipments_to_invoice():
    """請求書に入っていない出荷 (billing の読むだけの関数 billable_shipments)。"""
    if _model("billing", "Invoice") is None:
        return None
    from seizou.billing.services import billable_shipments

    return billable_shipments()


def _invoices_to_be_paid():
    """発行の請求書のうち、取消でない入金の合計が合計に足りない物 (一部入金も数える)。"""
    invoice_model = _model("billing", "Invoice")
    payment_model = _model("billing", "Payment")
    if invoice_model is None or payment_model is None:
        return None
    # 保存した入金の金額を足すだけ (掛け算と丸めをしない)。名前を paid_total にしない (Invoice の method と重なる)。
    paid = Coalesce(
        Sum("payments__amount", filter=Q(payments__status=payment_model.STATUS_RECORDED)),
        Value(Decimal("0")),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    return (
        invoice_model._default_manager.filter(status=invoice_model.STATUS_ISSUED)
        .annotate(paid_sum=paid)
        .filter(paid_sum__lt=F("total"))
    )


def _purchase_drafts():
    """まだ送っていない発注 (状態が下書き)。"""
    purchase_model = _model("purchasing", "PurchaseOrder")
    if purchase_model is None:
        return None
    return purchase_model._default_manager.filter(status=purchase_model.STATUS_DRAFT)


def _purchases_sent():
    """返事を待つ発注 (状態が送った)。"""
    purchase_model = _model("purchasing", "PurchaseOrder")
    if purchase_model is None:
        return None
    return purchase_model._default_manager.filter(status=purchase_model.STATUS_SENT)


def _noticed_lines_to_receive():
    """出荷の知らせのある発注の明細のうち、受入の残が 0 より大きく、発注が取消でない物。"""
    purchase_model = _model("purchasing", "PurchaseOrder")
    line_model = _model("purchasing", "PurchaseOrderLine")
    notice_model = _model("connect", "ShippingNotice")
    if purchase_model is None or line_model is None or notice_model is None:
        return None
    from seizou.purchasing.services import annotate_received

    # 知らせは副問い合わせ (join を足さない)。join は受入の 1 つだけ。
    queryset = line_model._default_manager.filter(
        pk__in=notice_model._default_manager.values("line_id")
    ).exclude(purchase_order__status=purchase_model.STATUS_CANCELLED)
    # 取り消した明細 (明細状態コード 2。core.edi_codes の LINE_DELETED) は受け入れる明細に数えない。
    from seizou.core.edi_codes import LINE_DELETED

    queryset = queryset.exclude(line_status_code=LINE_DELETED)
    return annotate_received(queryset).filter(remaining_total__gt=0)


def _steps_to_subcontract():
    """協力企業に出していない工程 (purchasing.services.steps_to_subcontract。指示書の一覧の絞り「協力企業に出していない工程があるだけ」と
    同じ関数。数は工程の行の数)。purchasing の部品が無ければ None。"""
    if _model("purchasing", "PurchaseOrderLine") is None:
        return None
    from seizou.purchasing.services import steps_to_subcontract

    return steps_to_subcontract()


def _lines_not_handed():
    """協力企業に渡していない明細 (purchasing.services.lines_not_handed。発注の一覧の絞り「渡していない明細があるだけ」と同じ関数。
    数は明細の行の数)。purchasing の部品が無ければ None。"""
    if _model("purchasing", "PurchaseOrderLine") is None:
        return None
    from seizou.purchasing.services import lines_not_handed

    return lines_not_handed()


def _replies_undelivered():
    """相手に届いていない注文回答。受信した注文のうち、入口が直接で、注文回答のタイプが空でなく (答えた)、送った日時が空で、返事の欄が空でなく、
    送る物でない字 (connect.services の MESSAGE_REPLY_SEED・MESSAGE_REPLY_INTAKE) でない物。connect の部品が無ければ None。読むだけ。

    訳: 相手に届いていない注文回答・出荷の知らせ・検収を、詳細を開かなくても今日やることで気づけるようにするため (届かなかった物は自動では送り直さず、人が「送り直す」を押す)。作り物の種の読み込みと、相手が seizou でない物 (送る物でない) は数えない。相手に断られた物 (「断られた: …」) も、届いていない物として数える。出所 = 本体の決め 11-11 14:2x (精査 Q3 の T8・M4)。変えてよい条件 = 届いていない物の数え方を本人が決め直したとき。
    """
    inbox_model = _model("connect", "Inbox")
    if inbox_model is None:
        return None
    from seizou.connect.services import MESSAGE_REPLY_INTAKE, MESSAGE_REPLY_SEED

    return (
        inbox_model._default_manager.filter(source=inbox_model.SOURCE_DIRECT, reply_sent_at__isnull=True)
        .exclude(reply_type="")
        .exclude(reply_response="")
        .exclude(reply_response__in=(MESSAGE_REPLY_SEED, MESSAGE_REPLY_INTAKE))
    )


def _notices_undelivered():
    """相手に届いていない出荷の知らせ。出荷のうち、送った日時が空で、返事の欄が空でなく、送る物でない字
    (shipping.services の MESSAGE_NOTICE_SEED・connect.services の MESSAGE_SHIPPING_NOT_EDI_ORDER) でない物。shipping か connect の部品が無ければ None。読むだけ。

    訳: 相手に届いていない注文回答・出荷の知らせ・検収を、詳細を開かなくても今日やることで気づけるようにするため (届かなかった物は自動では送り直さず、人が「送り直す」を押す)。作り物の種の読み込みと、相手が seizou でない物 (送る物でない) は数えない。相手に断られた物 (「断られた: …」) も、届いていない物として数える。出所 = 本体の決め 11-11 14:2x (精査 Q3 の T8・M4)。変えてよい条件 = 届いていない物の数え方を本人が決め直したとき。
    """
    shipment_model = _model("shipping", "Shipment")
    if shipment_model is None or _model("connect", "Connection") is None:
        return None
    from seizou.connect.services import MESSAGE_SHIPPING_NOT_EDI_ORDER
    from seizou.shipping.services import MESSAGE_NOTICE_SEED

    return (
        shipment_model._default_manager.filter(notice_sent_at__isnull=True)
        .exclude(notice_response="")
        .exclude(notice_response__in=(MESSAGE_NOTICE_SEED, MESSAGE_SHIPPING_NOT_EDI_ORDER))
    )


def _acceptances_undelivered():
    """相手に届いていない検収。受入のうち、送った日時が空で、返事の欄が空でなく、送る物でない字
    (connect.services の MESSAGE_ACCEPTANCE_NOT_SENT_ORDER) でない物。purchasing か connect の部品が無ければ None。読むだけ。

    訳: 相手に届いていない注文回答・出荷の知らせ・検収を、詳細を開かなくても今日やることで気づけるようにするため (届かなかった物は自動では送り直さず、人が「送り直す」を押す)。作り物の種の読み込みと、相手が seizou でない物 (送る物でない) は数えない。相手に断られた物 (「断られた: …」) も、届いていない物として数える。出所 = 本体の決め 11-11 14:2x (精査 Q3 の T8・M4)。変えてよい条件 = 届いていない物の数え方を本人が決め直したとき。
    """
    receipt_model = _model("purchasing", "Receipt")
    if receipt_model is None or _model("connect", "Connection") is None:
        return None
    from seizou.connect.services import MESSAGE_ACCEPTANCE_NOT_SENT_ORDER

    return (
        receipt_model._default_manager.filter(acceptance_sent_at__isnull=True)
        .exclude(acceptance_response="")
        .exclude(acceptance_response=MESSAGE_ACCEPTANCE_NOT_SENT_ORDER)
    )


# 数える段の鍵 (FLOWS の count) → 数える物の queryset を返す関数 (部品が無ければ None)。
_FLOW_COUNTS = {
    "inbox": _received_inbox,
    "workorder": _lines_without_workorder,
    "shipment": orders_to_ship,
    "delivery_note": _shipments_without_note,
    "invoice": _shipments_to_invoice,
    "payment": _invoices_to_be_paid,
    "purchase_draft": _purchase_drafts,
    "purchase_sent": _purchases_sent,
    "receipt": _noticed_lines_to_receive,
    "subcontract": _steps_to_subcontract,
    "handover": _lines_not_handed,
}


def flow_guide() -> dict | None:
    """ダッシュボードの「はじめての方へ」の枠。業務の流れ (FLOWS) を、部品の入っている段だけ並べ、数える段の件数と、流れごとの次にすること (件数が 1 以上の最初の段) を足す。段が 1 つも無い流れは入れず、流れが 1 本も無ければ None。読むだけ。問い合わせは数える段ごとに 1 回。

    返す物 = {"flows": [{"key", "title", "steps": [段], "next": 段か None}]}。
    段 = FLOWS の段の写しに url を足し、count を件数 (数えない段と数えられない段は None) にした物。
    """
    flows = []
    for flow in FLOWS:
        steps = [
            {**step, "count": _flow_count(step["count"]) if step["count"] else None} for step in _flow_steps(flow)
        ]
        if not steps:
            continue
        first = next((step for step in steps if step["count"]), None)
        flows.append({"key": flow["key"], "title": flow["title"], "steps": steps, "next": first})
    return {"flows": flows} if flows else None


#: 「今日やること」の枠の題 (home.html と tours.py が読む。本人が題を変えるときはここだけ)
TODAY_TITLE = "今日やること"

#: 段の key が行の key (移る先の表の鍵) と違う段 (発注の 2 段は、段の key でなく数える物の名前で行を呼ぶ)
_TASK_KEY_OF_STEP = {"purchase_order": "purchase_draft", "reply": "purchase_sent"}


def _task_targets() -> dict:
    """行の key → (ボタンの字 = 移る先の画面の screen_name, URL の名前, 絞りの GET, その行を進めるのに要る書く action)。状態の値は model の定数から読む (字を直書きしない)。部品の無い物は空の GET。

    納品書に入っていない出荷と請求書に入っていない出荷の行は、その出荷だけが並ぶ作る画面 (納品書を作る・請求書の新規登録) へ移る
    (一覧には合う絞りが無く、数えた物と違う物が並ぶため。決め 11-07 20:5x の 2 = 数を押すとその数で絞った画面が開く。flow-02)。
    指示書の無い明細の行は、生産管理ボードを同じ関数 (without_workorder) の絞り「指示書の無い明細だけ」で開く (nav-01 の (b))。
    協力企業に出していない工程の行は指示書の一覧を、協力企業に渡していない明細の行は発注の一覧を、数える関数と同じ関数の絞りで開く
    (purchasing.services の steps_to_subcontract と lines_not_handed。進める action は発注の新規登録と渡した日。見比べのまとめの M01 の 3 段目)。
    書く action は、その行の仕事を保存する画面の service の名前 (検査の層と core.access の ACTION_KINDS・MONEY_ACTIONS と同じ形。
    下書きの発注は詳細の「再送」= purchasing.resend_purchase_order)。today_tasks が、その action を行えない人の行を外す (_can_proceed。作者の決め)。
    数えるだけで操作の無い行 (返事を待つ発注) は None。
    """
    inbox = _model("connect", "Inbox")
    purchase = _model("purchasing", "PurchaseOrder")
    return {
        "order": ("受信した注文", "connect:inbox_list", {"f_status": inbox.STATUS_RECEIVED} if inbox else {}, "connect.accept"),
        "workorder": ("生産管理ボード", "orders:orderline_list", {"f_open": "1", "f_no_workorder": "1"}, "workorders.issue_workorder"),
        "shipment": ("受注", "orders:order_list", {}, "shipping.ship"),
        "delivery_note": ("納品書を作る", "shipping:deliverynote_create", {}, "shipping.create_delivery_note"),
        "invoice": ("請求書の新規登録", "billing:invoice_create", {}, "billing.create_invoice"),
        "payment": ("請求書", "billing:invoice_list", {"f_unpaid": "1"}, "billing.record_payment"),
        "purchase_draft": (
            "発注",
            "purchasing:purchaseorder_list",
            {"f_status": purchase.STATUS_DRAFT} if purchase else {},
            "purchasing.resend_purchase_order",
        ),
        "purchase_sent": ("発注", "purchasing:purchaseorder_list", {"f_status": purchase.STATUS_SENT} if purchase else {}, None),
        "receipt": ("受入", "purchasing:receipt_list", {}, "purchasing.receive"),
        "subcontract": ("指示書", "workorders:workorder_list", {"f_to_subcontract": "1"}, "purchasing.create_purchase_order"),
        "handover": ("発注", "purchasing:purchaseorder_list", {"f_not_handed": "1"}, "purchasing.set_handed_on"),
    }


def _can_proceed(user, action) -> bool:
    """行を進める書く action を、その人が行えるか (その操作の入口のボタンと同じ判定 = core の tag can_write。記録は書かない)。

    編集の権限と金額の閲覧は core.access.write_allowed (編集の判定が on で種類の権限が無い・金額の判定が on で金額に関わる操作
    (MONEY_ACTIONS) なのに金額の閲覧が無い、のときだけ偽)。操作の入口がある部品 (action の . の前) を部署で開けるかは core.access.can_open
    (伝票編集の権限はあっても、部署で指示書の部品を開けない人には発行の入口が出ないため)。action が None (数えるだけで操作の無い行) は真。
    訳: 「今日やること」は個人のタスクの枠 (本人の決め 11-05 09:4x) なので、押して移っても操作のボタンが出ない段を並べない。
    出所 = 作者の決め (11-07 21:0x の決めで金額に関わる操作の入口を金額の閲覧の無い人に出さなくなり、進められない段が増えた)。
    変えてよい条件 = 入口の出し方 (can_write の tag) の判定が変わったとき (この関数も同じ判定にそろえる)。
    """
    if action is None:
        return True
    return access.write_allowed(user, action) and access.can_open(user, action.split(".")[0], record=False)


#: 相手に届いていない物の行 (今日やることで、属する流れの段の行の後に足す。FLOWS の段ではないので、「はじめての方へ」と案内の前後の段は変えない)。
#: 1 行 = key・count (数える関数)・label (count_label)・unit・button (移る先の画面の名前)・url_name (移る先)・action (その行を進める書く action =
#: 詳細の「送り直す」の口の action)・flow (属する流れの key)。移る先は素の一覧 (届いていない物だけの絞りは無い。受入の残の行と同じ扱い)。
_UNDELIVERED_TASKS = (
    {"key": "reply_undelivered", "count": _replies_undelivered, "label": "相手に届いていない注文回答", "unit": "件",
     "button": "受信した注文", "url_name": "connect:inbox_list", "action": "connect.accept", "flow": "sales"},
    {"key": "notice_undelivered", "count": _notices_undelivered, "label": "相手に届いていない出荷の知らせ", "unit": "件",
     "button": "出荷", "url_name": "shipping:shipment_list", "action": "connect.send_shipping_notice", "flow": "sales"},
    {"key": "acceptance_undelivered", "count": _acceptances_undelivered, "label": "相手に届いていない検収", "unit": "件",
     "button": "受入", "url_name": "purchasing:receipt_list", "action": "connect.send_acceptance", "flow": "purchase"},
)


def _undelivered_rows(flow_key, user) -> list:
    """流れ flow_key に属する、相手に届いていない物の行 (_UNDELIVERED_TASKS の順に、件数が 1 以上の物だけ)。問い合わせは 1 行につき 1 回。

    行の辞書の鍵は today_tasks の行と同じ。数える関数が None を返す (部品が無い) か DB が読めない行は出さない (偽の 0 を置かない)。
    user を渡すと、移る先の部品 (URL の名前の : の前) を部署で開けない行と、行の action を行えない行 (core.access の can_open と
    write_allowed。記録は書かない) は数えずに外す。_can_proceed は使わない: _can_proceed は action の . の前の部品 (3 行とも connect) を
    部署で開けるかも見るが、出荷の知らせと検収の「送り直す」の口は出荷と発注の画面にあり、口 (shipping/shipping_notice.py・
    purchasing/acceptance.py) は access.require_write で action の種類 (出荷・購買) だけを見るため。
    """
    rows = []
    for task in _UNDELIVERED_TASKS:
        if task["flow"] != flow_key:
            continue
        url = _url(task["url_name"])
        if url is None:
            continue
        if user is not None and not (
            access.can_open(user, task["url_name"].split(":")[0], record=False)
            and access.write_allowed(user, task["action"])
        ):
            continue
        queryset = task["count"]()
        if queryset is None:
            continue
        try:
            count = queryset.count()
        except DatabaseError:
            continue
        if not count:
            continue
        rows.append(
            {
                "key": task["key"],
                "flow": flow_key,
                "label": task["label"],
                "count": count,
                "unit": task["unit"],
                "button": task["button"],
                "url_name": task["url_name"],
                "url": url,
            }
        )
    return rows


def today_tasks(user=None) -> dict | None:
    """「今日やること」の枠。flow_guide() の段のうち件数が 1 以上の物を、流れの順に 1 行ずつ並べる。読むだけで、ここでは問い合わせをしない (数は flow_guide の物)。

    返す物 = {"title": TODAY_TITLE, "rows": [行], "guide": flow_guide() の値}。流れが 1 本も無ければ None。
    行 = {"key" (移る先の表の鍵), "flow" (流れの key), "label" (段の count_label), "count", "unit", "button" (移る先の画面の名前), "url_name", "url" (絞りの GET つき)}。
    user を渡すと、移る先の部品 (URL の名前の : の前) を部署で開けない行と、その行を進める書く action (_task_targets の 4 つ目) を行えない行
    (編集の権限か金額の閲覧が無い・操作の入口の部品を部署で開けない = _can_proceed。作者の決め) は外す (core.access.can_open と write_allowed。記録は書かない)。
    None なら外さない。
    user を渡すと、guide も部署で開けない段を外した写し (_open_guide。流れが 1 本も残らなければ None) にし、行が 0 で写しも None なら
    None を返す (「はじめての方へ」の枠ごと出さない。作者の決め)。行は外す前の guide から作る
    (行の移る先と段の url_name が違う物があり、段を先に外すと開ける行まで消えるため。例: 出荷の段は出荷の一覧、出荷の行は受注の一覧)。
    納品書に入っていない出荷と請求書に入っていない出荷の行は、その出荷だけが並ぶ作る画面へ移る (_task_targets)。移る先に合う絞りが無い段
    (受入の残) は素の一覧へ移る。
    流れごとの段の行の後に、その流れに属する相手に届いていない物の行 (受注の流れに注文回答と出荷の知らせ、発注の流れに検収) を件数が 1 以上の物だけ足し、この行だけはここで 1 行につき 1 回数える (_undelivered_rows。素の一覧へ移る)。
    """
    guide = flow_guide()
    if guide is None:
        return None
    targets = _task_targets()
    rows = []
    for flow in guide["flows"]:
        for step in flow["steps"]:
            key = _TASK_KEY_OF_STEP.get(step["key"], step["key"])
            if not step["count"] or key not in targets:
                continue
            button, url_name, params, action = targets[key]
            url = _url(url_name)
            if url is None:
                continue
            if user is not None and not (
                access.can_open(user, url_name.split(":")[0], record=False) and _can_proceed(user, action)
            ):
                continue
            rows.append(
                {
                    "key": key,
                    "flow": flow["key"],
                    "label": step["count_label"],
                    "count": step["count"],
                    "unit": step["unit"],
                    "button": button,
                    "url_name": url_name,
                    "url": f"{url}?{urlencode(params)}" if params else url,
                }
            )
        rows.extend(_undelivered_rows(flow["key"], user))
    if user is None:
        return {"title": TODAY_TITLE, "rows": rows, "guide": guide}
    guide = _open_guide(guide, user)
    if not rows and guide is None:
        return None
    return {"title": TODAY_TITLE, "rows": rows, "guide": guide}


def _step_open(user, step) -> bool:
    """段を、その人が部署で開けるか (記録は書かない)。段の移る先の部品 (URL の名前の : の前) と、段の仕事をする部品 (段の needs。無い段は見ない) の両方を開ける人だけ真。

    訳: 「協力企業に出す」の段は指示書の一覧へ移るが、仕事 (工程を発注に出す) は発注の部品でする。指示書だけを開ける現場の人に、進められない段 1 つだけの「はじめての方へ」の枠と、開けない画面への案内の link を出さないため。出所 = 作者の決め (部署で開けない段は外し、何も残らなければ枠ごと出さない) と本体の直し 11-12 (504 回目。N406c の記録の「本体の判断が要る事」)。
    変えてよい条件 = 移る先の部品と仕事をする部品が違う段を足すときは、その段に needs を書く (今ある段の出方は変えない)。
    """
    parts = [step["url_name"].split(":")[0]]
    if step.get("needs"):
        parts.append(step["needs"])
    return all(access.can_open(user, part, record=False) for part in parts)


def _open_guide(guide: dict, user) -> dict | None:
    """flow_guide() の値から、部署で開けない段を外した写し。段の移る先の部品と、段の仕事をする部品 (needs) を core.access.can_open で見る (_step_open。記録は書かない)。

    段の残らない流れは入れず、流れが 1 本も残らなければ None。写しの流れの next は、残した段のうち件数が 1 以上の最初の段
    (flow_guide と同じ選び方)。元の guide の dict は書き換えない。問い合わせは増えない (can_open は Access の行の控えを読む)。
    """
    flows = []
    for flow in guide["flows"]:
        steps = [step for step in flow["steps"] if _step_open(user, step)]
        if not steps:
            continue
        first = next((step for step in steps if step["count"]), None)
        flows.append({**flow, "steps": steps, "next": first})
    return {"flows": flows} if flows else None


def flow_neighbors(url_name: str, user=None) -> dict | None:
    """「案内」の小窓の、流れの中の前後の画面。url_name の画面が段にある最初の流れで、その画面の段の前の段と後の段 (同じ画面の段は飛ばす) を返す。無ければ None。問い合わせをしない。

    user を渡すと、その人が部署で開けない段 (_step_open) を先に外してから前後を決める (開けない画面への link を出さない。
    その画面の段を開けない人には None)。None なら外さない。

    返す物 = {"flow": 流れの title, "label": その画面の段の label を「・」でつないだ字, "prev": {"label", "url"} か None, "next": 同じ形か None}。
    前後は部品の入っている段だけで決める (指示書の部品が無い会社では、出荷の前の段が受注)。
    """
    for flow in FLOWS:
        steps = _flow_steps(flow)
        if user is not None:
            steps = [step for step in steps if _step_open(user, step)]
        here = [index for index, step in enumerate(steps) if step["url_name"] == url_name]
        if not here:
            continue
        before = steps[here[0] - 1] if here[0] > 0 else None
        after = steps[here[-1] + 1] if here[-1] + 1 < len(steps) else None
        return {
            "flow": flow["title"],
            "label": "・".join(steps[index]["label"] for index in here),
            "prev": _neighbor(before),
            "next": _neighbor(after),
        }
    return None


def _neighbor(step):
    return None if step is None else {"label": step["label"], "url": step["url"]}


def reading_match(*, customer=None, extraction=None) -> dict | None:
    """読み取りの一致率 (intake.outcome.match_counts)。extraction を渡せばその 1 枚、customer を渡せばその得意先のこれまでの全部。読み取りの部品が無い・どちらも渡さない・受注にした行が 1 枚も無い (lines が 0)・DB が読めなければ None。

    対象は受注になった読み取りの行 (取消の受注も数える)。画面の道 = 状態が「受注にした」で受注のある行 (得意先は読み取りの行の得意先)。
    見張りの道 = 状態が「受信した注文へ回した」で、その受信した注文 (connect の Inbox) が受注になった行 (得意先は受信した注文の取引先 =
    受注の得意先。差出人を後で選んだ行は、読み取りの行の得意先が空のまま)。受信した注文の部品が無ければ画面の道だけ。
    比べる明細はどちらもその受注の明細。問い合わせは 2 回まで (受信した注文は 1 回目に join で読む)。返す物 = {"extractions", "lines", "same", "fixed"}。
    """
    extraction_model = _model("intake", "Extraction")
    if extraction_model is None or (customer is None and extraction is None):
        return None
    from seizou.intake.outcome import match_counts

    ordered = Q(status=extraction_model.STATUS_ORDERED, order__isnull=False)
    watched = Q(status=extraction_model.STATUS_INBOX, inbox_messages__order__isnull=False)
    if extraction is not None:
        ordered &= Q(pk=extraction.pk)
        watched &= Q(pk=extraction.pk)
    else:
        ordered &= Q(customer=customer)
        watched &= Q(inbox_messages__partner=customer)
    if _model("connect", "Inbox") is None:
        queryset = extraction_model._default_manager.filter(ordered).annotate(order_key=F("order"))
    else:
        # 見張りの道の受注は受信した注文の側にある (読み取りの行の order は空)。絞りと同じ join を使う。
        queryset = extraction_model._default_manager.filter(ordered | watched).annotate(
            order_key=Coalesce(F("order"), F("inbox_messages__order"))
        )
    try:
        counts = match_counts(queryset, order_field="order_key")
    except DatabaseError:
        return None
    return counts if counts["lines"] else None
