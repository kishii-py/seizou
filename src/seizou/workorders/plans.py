"""生産計画の盤 (生産計画の盤の設計の決め) を作る、読むだけの関数。表に書かない (書くのは services.plan_step)。

工程の決め方 (resolve_process) と期間の解き方 (parse_start・parse_weeks) はここの 1 か所に置く。
盤の画面の get_queryset・template tag (workorders_plan)・plan_place の 3 つがこれを呼ぶ。

- 期間: start (YYYY-MM-DD。無いか読めなければ今日) の週の月曜から 7 × weeks 日。weeks は 1 か 6。
- 盤に出すのは、指示書が取消でなく、受注も取消でない予定だけ。
- 段: 棒を開始日・終了日・指示書番号の順に並べ、最後の棒の終了日が開始日より前の段に上から入れる。
  段の数は棒の段の数 + 1 (下に空の段を 1 つ置き、どの日にも落とせる所を残す)。
- 見た目の材料: 部品の色 (tone。部品の pk で決まる theme の色の名前) と棒の状態 (state。計画・着手・完了・遅れ)。
  class の字は返さない (Tailwind はテンプレートしか読まないので、テンプレートが data- の属性で切り替える)。
- 指示書の詳細の頭の「製造の完了を入庫にする」を出すか (finished_due) もここで決める。真 = 発行の指示書で、指示書の状態が完了
  (最後の工程に完了の印。作者の決め) で、まだ入庫にしていない完成の数が 0 より大きい。工程ごとの実績のまとめ (_step_totals) を使うため。
- 指示書の詳細の工程のタブの行 (step_rows) もここで作る。工程ごとの実績のまとめ (_step_totals) を使うため。会社の設定 production_plan は見ない。
  行には協力企業に出した明細の値 (subcontract。N406b) も足す。明細と数は purchasing の services の関数を関数の中で import して読む (purchasing の部品が無い置き場では None)。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Prefetch
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from seizou.core import calendar
from seizou.core.conf import setting

from .models import NO_TOTALS, StepTotals, WorkOrder, WorkOrderStep, step_total_expressions, step_totals_of

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


def bar_state(step, totals) -> str:
    """棒の状態。上から順に最初に当たった物 (完了 > 遅れ > 着手 > 計画)。

    totals は指示書の工程の pk → StepTotals (_step_totals。辞書に無い工程は 0・0・印なし。進捗の部品が無ければ空の辞書)。
    完了と着手は指示書の進みの状態 (WorkOrder.progress_state_of。詳細の題の横と同じ決め方) で決める:
    - done 完了: 最後の工程に完了の印の付いた実績がある (指示書の状態の完了と同じ)。
    - late 遅れ: 終了日が納期より後 (納期が空なら当たらない)。
    - accepted 着手: その指示書のどれかの工程に実績がある (着手の印は読まない。作者の決め)。
    - planned 計画: ほか。
    """
    workorder = step.workorder
    progress = WorkOrder.progress_state_of(workorder.status, workorder.steps.all(), totals)
    if progress == WorkOrder.PROGRESS_DONE:
        return "done"
    due_date = workorder.order_line.due_date
    if due_date is not None and step.planned_end is not None and step.planned_end > due_date:
        return "late"
    if progress == WorkOrder.PROGRESS_STARTED:
        return "accepted"
    return "planned"


def _step_totals(step_pks) -> dict[int, StepTotals]:
    """工程の予定ごとの実績のまとめ {pk: StepTotals (良品の合計・不良の合計・完了の印)}。盤 1 枚で 1 回の query (棒ごとに読まない)。

    progress を import せず、予定の逆の関係 (progress の外部キー) を通して集計する (式は models.step_total_expressions)。
    進捗の部品が無い置き場では空の辞書 (= 「完了」は出ない)。
    """
    try:
        WorkOrderStep._meta.get_field("progress_records")
    except FieldDoesNotExist:
        return {}
    if not step_pks:
        return {}
    rows = WorkOrderStep.objects.filter(pk__in=step_pks).order_by().annotate(**step_total_expressions())
    return {step.pk: step_totals_of(step) for step in rows}


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

    # 休日の表を 1 回だけ読み、日ごとに渡す (生産管理ボードの BoardRow と同じ形。棒の数で問い合わせを増やさない)
    holidays = calendar.holiday_dates()
    days = []
    for offset in range(length):
        day = first + timedelta(days=offset)
        days.append(
            {
                "date": day,
                "iso": day.isoformat(),
                "weekday": WEEKDAYS[day.weekday()],
                # 稼働日でない日 (定休日の曜日と休日の一覧の日。core.calendar)。鍵の名前は土日だけを塗っていた頃のまま (テンプレートの class が読む)
                "weekend": not calendar.is_workday(day, holidays=holidays),
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
    # 指示書の全部の工程の実績の合計を 1 回で読む (着手 = どれかの工程に実績、完了 = 最後の工程。棒ごとに読まない)
    totals = _step_totals({other.pk for step in placed for other in step.workorder.steps.all()})
    bars = []
    for step in placed:
        bar = _card(step)
        shown_from = max(step.planned_start, first)
        shown_to = min(step.planned_end, last)
        bar["col"] = (shown_from - first).days + 1
        bar["span"] = (shown_to - shown_from).days + 1
        bar["neighbors"] = _neighbors(step)
        state = bar_state(step, totals)
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
        state_label = STATE_LABELS[bar_state(step, _step_totals([other.pk for other in steps]))]
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

# 図の大きさと余白 (viewBox の単位。小窓の幅に合わせた小さな図。目盛りと日付の字は svg の外の HTML の text-xs = 12px で、
# 図と一緒に縮まない。左の余白 _CHART_LEFT と下の余白 (_CHART_BOTTOM から下) は、その字を置く場所)
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
    目盛りと日付の字は svg の外の HTML の字 (作者の決め。ダッシュボードの棒の図と同じ形) なので、svg の箱に対する割合 (0〜100 の字) も返す:
    目盛り 1 本の "top" (箱の上の端から y まで)・日付 1 つの "left" (箱の左の端から x まで)・
    "tick_label_right" (箱の右の端から目盛りの字の右の端 tick_label_x まで)。割合は受け取った見込みの数だけから作る (DB を引き直さない)。
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
        tick_y = y_of(value)
        ticks.append({"y": _num(tick_y), "top": _num(tick_y * 100 / _CHART_HEIGHT), "label": f"{int(value):,}"})
        value += step
    labels = []
    for index, day in enumerate(days):
        if index % 7 == 0:
            label_x = _CHART_LEFT + band * index
            labels.append(
                {
                    "x": _num(label_x),
                    "left": _num(label_x * 100 / _CHART_WIDTH),
                    "label": f"{day['date'].month}/{day['date'].day}",
                }
            )
    tick_label_x = _CHART_LEFT - 6
    return {
        "width": _CHART_WIDTH,
        "height": _CHART_HEIGHT,
        "left": _num(_CHART_LEFT),
        "right": _num(_CHART_RIGHT),
        "tick_label_x": _num(tick_label_x),
        "tick_label_right": _num((_CHART_WIDTH - tick_label_x) * 100 / _CHART_WIDTH),
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


# --- 指示書の詳細の頭の右の緑の「製造の完了を入庫にする」(作者の決め 11-06 22:1x。作者の決め) ---


def finished_due(workorder) -> bool:
    """指示書の詳細の頭の右に緑の「製造の完了を入庫にする」を出すか。読むだけ。

    真 = 発行の指示書で、指示書の状態が完了 (最後の工程に完了の印。作者の決め。WorkOrder.progress_state_of と同じ決め方。
    _step_totals で 1 回の問い合わせ。進捗の部品が無ければ空の辞書なので偽) で、
    まだ入庫にしていない完成の数 (stock.services の finished_qty − stocked_qty。入庫の画面が ?workorder= で入れる数と同じ。良品だけ)
    が 0 より大きい。在庫の部品が無ければ偽。
    """
    if workorder.status != WorkOrder.STATUS_ISSUED:
        return False
    steps = list(workorder.steps.all())
    if not steps:
        return False
    totals = _step_totals([step.pk for step in steps])
    if WorkOrder.progress_state_of(workorder.status, steps, totals) != WorkOrder.PROGRESS_DONE:
        return False
    if not apps.is_installed("seizou.stock"):
        return False
    from seizou.stock.services import finished_qty, stocked_qty

    return finished_qty(workorder) - stocked_qty(workorder) > 0


# --- 指示書の詳細の工程のタブ (作者の決め) ---


def _qty(value) -> Decimal:
    """集計の値を Decimal にそろえる (SQLite は桁なしや int で返すことがある)。"""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _subcontract_reads(workorder, steps):
    """工程の queryset に協力企業の数の合計を足した物と、指示書の工程を指す取り消していない明細の list を返す (N406b。読むだけ)。

    数の合計は purchasing.services.annotate_subcontract (工程の queryset の Subquery。問い合わせは増えない)、明細は指示書の property
    subcontract_lines に purchasing.services.annotate_received (受入の残 remaining_total) を足して 1 回で読む (工程の数で増えない)。
    purchasing の部品が無い置き場 (property が AttributeError) では (steps, None)。
    """
    try:
        lines = workorder.subcontract_lines
    except AttributeError:
        return steps, None
    from seizou.purchasing import services as purchasing_services

    return purchasing_services.annotate_subcontract(steps), list(purchasing_services.annotate_received(lines))


def _subcontract_value(workorder, step, lines):
    """工程 1 つの協力企業の値 (N406b。読むだけ)。lines はその工程を指す取り消していない明細 (受入の残 remaining_total 付き)。

    {"lines": 明細ごとの {"pk", "purchase_order", "po_no", "line_no", "partner", "out", "waiting", "done"} の並び,
     "out_total": 出ている数の合計, "waiting_total": まだ渡していない数の合計, "can_send": 出せるか, "open_line": 終わっていない最初の明細の pk か None}。
    明細ごとの出ている数とまだ渡していない数は purchasing.services.out_qty・waiting_qty と同じ決め方 (受入の残のうち 0 より大きい分を、
    渡した日の有る無しで分ける) を、読んだ受入の残から出す (明細ごとに問い合わせない)。done = 受入の残が 0 以下 (RULES.md R25 の終わっている)。
    出せる = 工程の「協力企業に出すか」が真・指示書が発行・その工程を指す取り消していない明細が無い。
    """
    entries = []
    for line in lines:
        rest = _qty(line.remaining_total)
        open_qty = rest if rest > 0 else _ZERO_QTY
        entries.append(
            {
                "pk": line.pk,
                "purchase_order": line.purchase_order_id,
                "po_no": line.purchase_order.po_no,
                "line_no": line.line_no,
                "partner": line.purchase_order.partner.name,
                "out": open_qty if line.handed_on is not None else _ZERO_QTY,
                "waiting": open_qty if line.handed_on is None else _ZERO_QTY,
                "done": rest <= 0,
            }
        )
    return {
        "lines": entries,
        "out_total": _qty(step.subcontract_out_total),
        "waiting_total": _qty(step.subcontract_waiting_total),
        "can_send": bool(step.process.is_outsourced) and workorder.status == WorkOrder.STATUS_ISSUED and not entries,
        "open_line": next((entry["pk"] for entry in entries if not entry["done"]), None),
    }


def step_rows(workorder) -> list[dict]:
    """指示書の詳細の工程のタブの行 (読むだけ)。1 行 = {"step", "recorded", "defect", "done", "current", "subcontract"}。

    工程は順 (seq) の順で、process を select_related で一緒に読む。実績のまとめは _step_totals を 1 回だけ呼ぶ
    (工程の数で問い合わせが増えない)。recorded = その工程の良品の合計、defect = 不良の合計、
    done = その工程に完了の印の付いた実績がある (数は見ない。作者の決め)、current = 指示書が完了でないときの、印の無い最初の工程
    (生産管理ボードの今の工程 = orders.board の BoardRow.current_step と同じ。指示書が完了なら、どの行も偽)。
    進捗の部品が無い置き場 (_step_totals が空の辞書) では、recorded と defect は None で done と current は偽。
    会社の設定 production_plan は見ない (工程のタブは生産計画を使わない会社でも出る)。
    済みは工程ごとの印。今の工程は、指示書が完了でないときの印の無い最初の工程 (生産管理ボードの今の工程と同じ)。
    途中の工程の印の抜けは、その行に「完了」が無いことで見える (作者の決め)。
    subcontract = 協力企業に出した明細の値 (_subcontract_value。N406b)。purchasing の部品が無い置き場では None。
    明細は 1 つの指示書の工程の全部を 1 回で読み、数の合計は工程の問い合わせに足す (工程の数で問い合わせが増えない)。
    """
    steps, lines = _subcontract_reads(workorder, workorder.steps.select_related("process").order_by("seq"))
    steps = list(steps)
    by_step = {}
    for line in lines or ():
        by_step.setdefault(line.workorder_step_id, []).append(line)
    subcontract = {step.pk: None if lines is None else _subcontract_value(workorder, step, by_step.get(step.pk, [])) for step in steps}
    totals = _step_totals([step.pk for step in steps])
    if not totals:
        return [
            {"step": step, "recorded": None, "defect": None, "done": False, "current": False, "subcontract": subcontract[step.pk]}
            for step in steps
        ]
    workorder_done = WorkOrder.progress_state_of(workorder.status, steps, totals) == WorkOrder.PROGRESS_DONE
    rows = []
    current_found = workorder_done
    for step in steps:
        step_totals = totals.get(step.pk, NO_TOTALS)
        done = step_totals.completed
        current = not done and not current_found
        current_found = current_found or current
        rows.append(
            {
                "step": step,
                "recorded": step_totals.recorded,
                "defect": step_totals.defect,
                "done": done,
                "current": current,
                "subcontract": subcontract[step.pk],
            }
        )
    return rows
