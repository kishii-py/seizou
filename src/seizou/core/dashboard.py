"""ダッシュボードの納期アラートと月ごとの推移を読む関数 (第 3 波)。

- due_alerts: 納期を過ぎても出荷が終わっていない明細と、近く納期が来る明細を、受注の詳細への URL つきで返す。
- monthly_trend: 直近の月ごとの受注と出荷の件数と金額と、図 (inline SVG) に置く座標の字を返す。
  画面からは外れたが、ほかの所 (MCP の道具など) が使えるように関数と試験は残す。
- shipping_trend: 直近の月ごとの出荷の件数と数量と金額と、数量の図と金額の図の座標の字を返す。
- production_trend: 直近の月ごとの指示書の発行の件数と数量と、完成の数 (最後の工程の実績) と、図の座標の字を返す。
- without_amounts: shipping_trend の結果から金額を抜いた写しを返す (金額を見られない人の画面に使う)。
- payable_this_month: 今月の支払の見込み (受入日が今月の受入の金額の合計)。金額の閲覧の判定は呼ぶ側 (purchasing の tag)。
- payments_this_month: 今月の入金 (入金日が今月の、取消でない入金の金額の合計)。金額の閲覧の判定は呼ぶ側 (core の views の dashboard_stats と HomeView)。
- どれも読むだけで、表に書かない。金額の掛け算と丸めと合計は Python で行う (R03。DB で計算しない)。
- 読む部品が無いか DB が読めなければ None を返す (画面は節ごと出さない。偽の 0 を置かない)。

ほかの部品の model は apps.get_model で名前から引き、ほかの部品の services は model が引けた後に関数の中で
import する (部品の入っていない会社で落ちないため)。core の画面の module は import しない (画面の module が
この module を import するので輪になる)。色と形の class はテンプレートに固定の字で書き、ここは数と字だけを返す。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal

from django.apps import apps
from django.db import DatabaseError
from django.db.models import Sum
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

# 図の枠 (viewBox の大きさ) と描く所。y は下へ増え、_BASELINE が値 0 の基線。
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


def monthly_trend(today=None, *, months=6) -> dict | None:
    """月次の推移。today の月を含む直近 months か月を古い順に。受注の部品が無いか DB が読めなければ None。

    受注 = 取消でない受注の件数 (受注日の月) と、その明細の金額 (R03 で丸めた値) の合計。
    出荷 = 出荷の件数 (出荷日の月) と、1 件ずつ round_amount(得意先, 出荷の数量 × 明細の単価) の合計
    (請求書の明細と同じ式)。後で受注が取消になった出荷も数える (出荷は起きた事 = 現物優先)。
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
            from seizou.masters.services import round_amount

            shipments = shipment_model._default_manager.filter(
                shipped_at__gte=start, shipped_at__lt=end
            ).select_related("order_line__order__customer")
            for shipment in shipments:
                line = shipment.order_line
                row = by_period[(shipment.shipped_at.year, shipment.shipped_at.month)]
                row["shipment_count"] += 1
                row["shipment_amount"] += round_amount(line.order.customer, shipment.qty * line.unit_price)
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
    round_amount(受注の得意先, 出荷の数量 × 明細の単価) の合計 (月次の推移の出荷の側と同じ式)。
    後で受注が取消になった出荷も数える (出荷は起きた事 = 現物優先)。

    返す物 = {"months", "is_empty", "max_qty", "max_amount", "qty_chart", "amount_chart"}。
    1 月 = {"key" (YYYY-MM), "label" (M 月), "shipment_count", "shipment_qty", "shipment_amount"}。
    数量と金額は単位が違うので 1 つの軸に置かず、図を 2 つにする (qty_chart の棒は qty_bars、amount_chart は amount_bars)。
    """
    shipment_model = _model("shipping", "Shipment")
    if shipment_model is None:
        return None
    from seizou.masters.services import round_amount

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
        shipments = shipment_model._default_manager.filter(
            shipped_at__gte=start, shipped_at__lt=end
        ).select_related("order_line__order__customer")
        for shipment in shipments:
            line = shipment.order_line
            row = by_period[(shipment.shipped_at.year, shipment.shipped_at.month)]
            row["shipment_count"] += 1
            row["shipment_qty"] += shipment.qty
            row["shipment_amount"] += round_amount(line.order.customer, shipment.qty * line.unit_price)
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
    access の without_totals と同じ考えで、値を None にすると画面が「—」を出す。元の辞書は変えない。
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
    """今月の支払の見込み = 受入日が今月 (1 日から月末まで) の受入の金額の合計。受入が無ければ 0。

    保存した金額 (受け入れた数 × 発注の単価を purchasing.services が丸めた物) を足すだけで、DB で掛け算をしない。
    受入の表 (purchasing) が無いか DB が読めなければ None (画面はカードを出さない。偽の 0 を置かない)。
    """
    receipt_model = _model("purchasing", "Receipt")
    if receipt_model is None:
        return None
    if today is None:
        today = timezone.localdate()
    start = date(today.year, today.month, 1)
    end = _first_of_next_month(today.year, today.month)  # この日は含まない
    try:
        total = receipt_model._default_manager.filter(received_on__gte=start, received_on__lt=end).aggregate(
            total=Sum("amount")
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
    """
    step, intervals = _scale(max_value)
    top = step * intervals
    plot_height = _BASELINE - _PLOT_TOP
    band = (_PLOT_RIGHT - _PLOT_LEFT) / len(rows)
    group_width = len(series) * _BAR_WIDTH + (len(series) - 1) * _BAR_GAP

    def y_of(value):
        return _BASELINE - float(value / top) * plot_height

    ticks = [
        {"y": _num(y_of(step * index)), "label": _tick_label(step * index, top)}
        for index in range(intervals + 1)
    ]
    bars_by_name = {name: [] for _key, name in series}
    labels = []
    for index, row in enumerate(rows):
        center = _PLOT_LEFT + band * (index + 0.5)
        labels.append({"x": _num(center), "label": row["label"]})
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
        "label_y": _num(_LABEL_Y),
        "top": top,
        "ticks": ticks,
        **bars_by_name,
        "labels": labels,
    }
