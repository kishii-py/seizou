"""生産計画の盤 (02 の 4 節) を作る、読むだけの関数。表に書かない (書くのは services.plan_step)。

工程の決め方 (resolve_process) と期間の解き方 (parse_start・parse_weeks) はここの 1 か所に置く。
盤の画面の get_queryset・template tag (workorders_plan)・plan_place の 3 つがこれを呼ぶ。

- 期間: start (YYYY-MM-DD。無いか読めなければ今日) の週の月曜から 7 × weeks 日。weeks は 1 か 6。
- 盤に出すのは、指示書が取消でなく、受注も取消でない予定だけ。
- 段: 棒を開始日・終了日・指示書番号の順に並べ、最後の棒の終了日が開始日より前の段に上から入れる。
  段の数は棒の段の数 + 1 (下に空の段を 1 つ置き、どの日にも落とせる所を残す)。
- 見た目の材料: 部品の色 (tone。部品の pk で決まる theme の色の名前) と棒の状態 (state。計画・着手・完了・遅れ)。
  class の字は返さない (Tailwind はテンプレートしか読まないので、テンプレートが data- の属性で切り替える)。
- 指示書の詳細の頭の「製造の完了を入庫にする」を出すか (finished_due) もここで決める。工程ごとの実績の合計 (_recorded_totals) を使うため。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db.models import DecimalField, Prefetch, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from seizou.core.conf import setting

from .models import WorkOrder, WorkOrderStep

WEEKDAYS = "月火水木金土日"
WEEKS_CHOICES = (1, 6)

# 部品の色 (棒の塗りと未計画のカードの左の線)。部品の pk で決まる = 同じ部品はどの盤でも同じ色。
# error は遅れの枠線の色なので、塗りに使わない。
PART_TONES = ("primary", "secondary", "accent", "info", "success", "warning")

# 棒の状態 (枠線の色)。重なったときは 完了 > 遅れ > 着手 > 計画 の順に決める (bar_state)。
STATES = (("planned", "計画"), ("accepted", "着手"), ("done", "完了"), ("late", "遅れ"))
STATE_LABELS = dict(STATES)

# 工程の表は、予定の外部キーの先として引く (masters の models を import しない)。
_Process = WorkOrderStep._meta.get_field("process").related_model


def marked_processes():
    """「計画に使う」の印の付いた工程 (並び順・コードの順)。"""
    return _Process.objects.filter(use_in_plan=True).order_by("sort_order", "code")


def resolve_process(process_pk=None):
    """盤の工程を決める。会社の設定 production_plan が偽なら、印の付いた工程があっても Http404。

    process_pk が None なら印の付いた最初の工程 (印が 1 つも無ければ None)。
    pk が来たら印の付いた工程だけを返し、無い工程・印の無い工程・読めない pk は Http404。
    """
    if not setting("production_plan"):
        raise Http404("生産計画を使わない設定です。")
    marked = marked_processes()
    if process_pk is None:
        return marked.first()
    try:
        return marked.get(pk=process_pk)
    except (_Process.DoesNotExist, ValueError, TypeError):
        raise Http404("計画に使う工程ではありません。") from None


def active_steps(process):
    """工程の予定のうち、指示書が取消でなく受注も取消でない物。工程が None なら空。"""
    if process is None:
        return WorkOrderStep.objects.none()
    return (
        WorkOrderStep.objects.filter(process=process)
        .exclude(workorder__status=WorkOrder.STATUS_CANCELLED)
        .exclude(workorder__order_line__order__status="cancelled")
    )


def parse_start(raw):
    """期間の start (YYYY-MM-DD)。無いか読めなければ None (= 今日)。"""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def parse_weeks(raw):
    """期間の長さ (週の数)。1 か 6。ほかは 1。"""
    return 6 if str(raw or "") == "6" else 1


def _query(start, weeks):
    params = {}
    if start is not None:
        params["start"] = start.isoformat()
    if weeks != 1:
        params["weeks"] = weeks
    return f"?{urlencode(params)}" if params else ""


def _board_url(process, start, weeks):
    return reverse("workorders:plan_board", args=[process.pk]) + _query(start, weeks)


def _period_links(process, start, weeks, base):
    """「前」「今日」「次」と「週」「6 週」の link (ほかの引数は引き継ぐ)。"""
    shift = timedelta(days=7 * weeks)
    return {
        "prev": _board_url(process, base - shift, weeks),
        "today": _board_url(process, None, weeks),
        "next": _board_url(process, base + shift, weeks),
        "week": _board_url(process, start, 1),
        "six_weeks": _board_url(process, start, 6),
    }


def _neighbors(step):
    """同じ指示書のほかの工程 (順の順)。1 つ = {"name", "start", "end"} (日が無ければ start は None)。"""
    return [
        {"name": other.process.name, "start": other.planned_start, "end": other.planned_end}
        for other in step.workorder.steps.all()
        if other.pk != step.pk
    ]


def part_tone(item_pk) -> str:
    """部品の色の名前 (PART_TONES の 1 つ)。部品の pk の剰余で決める。"""
    return PART_TONES[item_pk % len(PART_TONES)]


def _last_step(step):
    """同じ指示書の最後の工程 (順が最大。prefetch した並びの最後)。"""
    steps = list(step.workorder.steps.all())
    return steps[-1] if steps else step


def bar_state(step, recorded_last) -> str:
    """棒の状態。上から順に最初に当たった物 (完了 > 遅れ > 着手 > 計画)。

    recorded_last は指示書の最後の工程の実績の合計 (進捗の部品が無ければ None)。
    - done 完了: 最後の工程の実績の合計が、その工程の予定の数以上。
    - late 遅れ: 終了日が納期より後 (納期が空なら当たらない)。
    - accepted 着手: 製造部が指示書を受けた (accepted_at がある)。
    - planned 計画: ほか。
    """
    if recorded_last is not None and recorded_last >= _last_step(step).planned_qty:
        return "done"
    due_date = step.workorder.order_line.due_date
    if due_date is not None and step.planned_end is not None and step.planned_end > due_date:
        return "late"
    if step.workorder.accepted_at is not None:
        return "accepted"
    return "planned"


def _recorded_totals(step_pks) -> dict[int, Decimal]:
    """工程の予定ごとの実績の合計 {pk: 合計}。盤 1 枚で 1 回の query (棒ごとに読まない)。

    progress を import せず、予定の逆の関係 (progress の外部キー) を通して集計する。
    進捗の部品が無い置き場では空の辞書 (= 「完了」は出ない)。
    """
    try:
        WorkOrderStep._meta.get_field("progress_records")
    except FieldDoesNotExist:
        return {}
    if not step_pks:
        return {}
    rows = (
        WorkOrderStep.objects.filter(pk__in=step_pks)
        .order_by()
        .annotate(
            total=Coalesce(
                Sum("progress_records__qty"),
                Value(Decimal("0")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
        .values_list("pk", "total")
    )
    return {pk: Decimal(str(total)) for pk, total in rows}


def _card(step):
    line = step.workorder.order_line
    return {
        "step": step.pk,
        "workorder": step.workorder.pk,
        "wo_no": step.workorder.wo_no,
        "part_no": line.item.part_no,
        "item_name": line.item.name,
        "qty": step.planned_qty,
        "due_date": line.due_date,
        "start": step.planned_start,
        "end": step.planned_end,
        "tone": part_tone(line.item_id),
    }


def _with_neighbors(queryset):
    return queryset.select_related("workorder__order_line__item").prefetch_related(
        Prefetch(
            "workorder__steps",
            queryset=WorkOrderStep.objects.select_related("process").order_by("seq"),
        )
    )


def _place_in_rows(bars):
    """棒に段の番号 (1 から) を付ける。返す物は棒の段の数。"""
    row_ends: list[date] = []
    for bar in bars:
        for number, last_end in enumerate(row_ends):
            if last_end < bar["start"]:
                row_ends[number] = bar["end"]
                bar["row"] = number + 1
                break
        else:
            row_ends.append(bar["end"])
            bar["row"] = len(row_ends)
    return len(row_ends)


def _months(days):
    """日の並びを月で区切った帯。1 つ = {"label": "11 月", "col": 帯の最初の日の列 (1 から), "span": 日の数}。"""
    months = []
    for day in days:
        key = (day["date"].year, day["date"].month)
        if months and months[-1]["key"] == key:
            months[-1]["span"] += 1
        else:
            months.append(
                {"key": key, "label": f"{day['date'].month} 月", "col": day["col"], "span": 1}
            )
    return [{"label": m["label"], "col": m["col"], "span": m["span"]} for m in months]


def _row_heads(bars, rows):
    """段の見出し。1 つ = {"row": 1 から, "label": その段に最初に置かれた棒の品名 (棒の無い段は空)}。"""
    labels: dict[int, str] = {}
    for bar in bars:
        labels.setdefault(bar["row"], bar["item_name"])
    return [{"row": number, "label": labels.get(number, "")} for number in range(1, rows + 1)]


def build_board(process, start, weeks):
    """盤の中身の辞書。process が None (印が 1 つも無い) なら process だけが None の辞書を返す。

    start は期間の基の日 (None = 今日)、weeks は 1 か 6。
    鍵: process・pills・days・bars・rows・unplanned・unplanned_count・links・place_url・
    board_start (今の期間の start。今日なら空)・weeks・months (月の帯)・row_heads (段の見出し)。
    棒と未計画のカードに tone (部品の色)、棒に state・state_label (状態) と compact (1 行にする棒)。
    """
    weeks = weeks if weeks in WEEKS_CHOICES else 1
    today = timezone.localdate()
    base = start or today
    board = {
        "process": process,
        "weeks": weeks,
        "board_start": start.isoformat() if start is not None else "",
        "place_url": reverse("workorders:plan_place"),
    }
    if process is None:
        return board

    first = base - timedelta(days=base.weekday())
    length = 7 * weeks
    last = first + timedelta(days=length - 1)

    days = []
    for offset in range(length):
        day = first + timedelta(days=offset)
        days.append(
            {
                "date": day,
                "iso": day.isoformat(),
                "weekday": WEEKDAYS[day.weekday()],
                "weekend": day.weekday() >= 5,
                "today": day == today,
                "col": offset + 1,
            }
        )

    steps = active_steps(process)
    placed = list(
        _with_neighbors(
            steps.filter(planned_start__lte=last, planned_end__gte=first)
        ).order_by("planned_start", "planned_end", "workorder__wo_no")
    )
    totals = _recorded_totals({_last_step(step).pk for step in placed})
    bars = []
    for step in placed:
        bar = _card(step)
        shown_from = max(step.planned_start, first)
        shown_to = min(step.planned_end, last)
        bar["col"] = (shown_from - first).days + 1
        bar["span"] = (shown_to - shown_from).days + 1
        bar["neighbors"] = _neighbors(step)
        state = bar_state(step, totals.get(_last_step(step).pk))
        bar["state"] = state
        bar["state_label"] = STATE_LABELS[state]
        # 6 週の盤の 1 日の棒は狭いので 1 行だけにする (切れた字の全部は title で出す)。
        bar["compact"] = weeks == 6 and bar["span"] < 2
        bars.append(bar)
    rows = _place_in_rows(bars) + 1

    unplanned = [
        _card(step)
        for step in steps.filter(planned_start__isnull=True)
        .select_related("workorder__order_line__item")
        .order_by("workorder__issued_on", "workorder__wo_no")
    ]

    return {
        **board,
        "pills": [
            {
                "name": marked.name,
                "url": _board_url(marked, start, weeks),
                "current": marked.pk == process.pk,
            }
            for marked in marked_processes()
        ],
        "days": days,
        "months": _months(days),
        "columns": length,
        "bars": bars,
        "rows": rows,
        "row_heads": _row_heads(bars, rows),
        "unplanned": unplanned,
        "unplanned_count": len(unplanned),
        "links": _period_links(process, start, weeks, base),
        "first": first,
        "last": last,
    }


# --- 計画の詳細の小窓 (決め 11-04 14:5x。盤の棒と未計画のカードを押すと開く) ---


def detail_steps():
    """計画の詳細の小窓が引く工程の予定。会社の設定 production_plan が偽なら Http404 (盤と同じ)。"""
    if not setting("production_plan"):
        raise Http404("生産計画を使わない設定です。")
    return WorkOrderStep.objects.select_related(
        "process", "workorder__order_line__order__customer", "workorder__order_line__item"
    )


def plan_detail(step):
    """計画の詳細の小窓の中身 (読むだけ)。step は detail_steps() で引いた工程の予定。

    鍵: step・workorder・line・order・customer・item・state_label (盤の棒と同じ状態の字。取消の指示書は「取消」)・
    steps (指示書の工程の並び。1 つ = {"step", "current" (押した棒の工程か)})。在庫の見込みは item_projection が作る。
    """
    workorder = step.workorder
    line = workorder.order_line
    steps = list(workorder.steps.select_related("process").order_by("seq"))
    if workorder.status == WorkOrder.STATUS_CANCELLED:
        state_label = workorder.get_status_display()
    else:
        last = steps[-1] if steps else step
        state_label = STATE_LABELS[bar_state(step, _recorded_totals({last.pk}).get(last.pk))]
    return {
        "step": step,
        "workorder": workorder,
        "line": line,
        "order": line.order,
        "customer": line.order.customer,
        "item": line.item,
        "state_label": state_label,
        "steps": [{"step": other, "current": other.pk == step.pk} for other in steps],
    }


# --- 在庫の見込みの図 (計画の詳細の小窓の下。部品の在庫の見込みを日ごとの棒にする) ---

PROJECTION_DAYS = 56  # 在庫の見込みの日の数 (今日から 8 週)

# 図の大きさと余白 (viewBox の単位。小窓の幅に合わせた小さな図。字の大きさは text-xs)
_CHART_WIDTH = 448
_CHART_HEIGHT = 168
_CHART_LEFT = 44
_CHART_RIGHT = 440
_CHART_TOP = 8
_CHART_BOTTOM = 140
_CHART_LABEL_Y = 160
_CHART_STEPS = (1, 2, 5)  # 目盛りの刻みは 1・2・5 × 10 の累乗
_CHART_INTERVALS = 4  # 目盛りの間は 4 つまで
_ZERO_QTY = Decimal("0")


def _num(value) -> str:
    """図の座標を小数 1 桁の字にする。"""
    return f"{float(value):.1f}"


def _chart_scale(low, high):
    """目盛りの刻みと下と上の端。刻みは 1・2・5 × 10 の累乗のうち、間が 4 以下になる一番小さい物 (1 より小さくしない)。"""
    span = max(high - low, Decimal(1))
    power = Decimal(1)
    while True:
        for base in _CHART_STEPS:
            step = base * power
            bottom = (low / step).to_integral_value(rounding=ROUND_FLOOR) * step
            top = (high / step).to_integral_value(rounding=ROUND_CEILING) * step
            if top == bottom:
                top = bottom + step
            if (top - bottom) / step <= _CHART_INTERVALS or step >= span:
                return step, bottom, top
        power *= 10


def projection_chart(projection):
    """在庫の見込み (stock.services.stock_projection の返す物) を小さな棒の図の数にする。

    日ごとに 1 本。0 より大きい棒は 0 の線から上 (up)、0 より下の棒は 0 の線から下 (down) に分ける
    (テンプレートが系列ごとに for を分けて固定の class で塗る。class を組み立てない)。0 の日は棒を置かない。
    週の始め (7 日ごと) に日付の字を置く。返す物 = 枠と目盛り (0 の線を含む) と棒と日付の字の数。
    """
    days = projection["days"]
    values = [day["qty"] for day in days] or [_ZERO_QTY]
    low = min(min(values), _ZERO_QTY)
    high = max(max(values), _ZERO_QTY)
    step, bottom, top = _chart_scale(low, high)
    plot_height = _CHART_BOTTOM - _CHART_TOP

    def y_of(value):
        return _CHART_BOTTOM - float((value - bottom) / (top - bottom)) * plot_height

    zero_y = y_of(_ZERO_QTY)
    band = (_CHART_RIGHT - _CHART_LEFT) / max(len(days), 1)
    width = max(band - 1, 1)
    up, down = [], []
    for index, day in enumerate(days):
        qty = day["qty"]
        if qty == 0:
            continue
        x = _CHART_LEFT + band * index + 0.5
        y = y_of(qty)
        bar = {"date": day["date"], "qty": qty, "x": _num(x), "width": _num(width)}
        if qty > 0:
            up.append({**bar, "y": _num(y), "height": _num(zero_y - y)})
        else:
            down.append({**bar, "y": _num(zero_y), "height": _num(y - zero_y)})
    ticks = []
    value = bottom
    while value <= top:
        ticks.append({"y": _num(y_of(value)), "label": f"{int(value):,}"})
        value += step
    labels = [
        {"x": _num(_CHART_LEFT + band * index), "label": f"{day['date'].month}/{day['date'].day}"}
        for index, day in enumerate(days)
        if index % 7 == 0
    ]
    return {
        "width": _CHART_WIDTH,
        "height": _CHART_HEIGHT,
        "left": _num(_CHART_LEFT),
        "right": _num(_CHART_RIGHT),
        "tick_label_x": _num(_CHART_LEFT - 6),
        "label_y": _num(_CHART_LABEL_Y),
        "ticks": ticks,
        "up": up,
        "down": down,
        "labels": labels,
    }


def item_projection(item):
    """部品の在庫の見込み (stock.services.stock_projection) と、その図の数 (projection_chart)。読むだけ。

    返す物 = {"projection", "chart"}。在庫の部品 (stock) が無い置き場では None (小窓は図を出さない)。
    """
    if not apps.is_installed("seizou.stock"):
        return None
    from seizou.stock.services import stock_projection

    projection = stock_projection(item, PROJECTION_DAYS)
    return {"projection": projection, "chart": projection_chart(projection)}


# --- 指示書の詳細の頭の右の緑の「製造の完了を入庫にする」(UX-2 a。11-06 22:1x。初見テスト R5 の候補 C93) ---


def finished_due(workorder) -> bool:
    """指示書の詳細の頭の右に緑の「製造の完了を入庫にする」を出すか。読むだけ。

    真 = 発行の指示書で工程が 1 つ以上あり、全部の工程の実績の合計が予定の数以上 (QR を読んだ先の「完了」と同じ >=。
    _recorded_totals で 1 回の問い合わせ。辞書に無い工程は 0。進捗の部品が無ければ空の辞書なので偽) で、
    まだ入庫にしていない完成の数 (stock.services の finished_qty − stocked_qty。入庫の画面が ?workorder= で入れる数と同じ)
    が 0 より大きい。在庫の部品が無ければ偽。
    """
    if workorder.status != WorkOrder.STATUS_ISSUED:
        return False
    steps = list(workorder.steps.all())
    if not steps:
        return False
    totals = _recorded_totals([step.pk for step in steps])
    if any(totals.get(step.pk, Decimal("0")) < step.planned_qty for step in steps):
        return False
    if not apps.is_installed("seizou.stock"):
        return False
    from seizou.stock.services import finished_qty, stocked_qty

    return finished_qty(workorder) - stocked_qty(workorder) > 0
