"""集計の図 (横棒の inline SVG。決め 11-04 22:0x = 集計の図は 1 か所で色と形を決める)。

大きさの決まりはこの module の数、色と形の決まりは core/_summary_chart.html の 1 か所に置く。
棒は主色の 1 色 (fill-primary)、字は fill-base-content/70 の text-xs、基線は stroke-base-300 (ダッシュボードの図
core/_chart_bars.html と同じ class)。ほかの図も後でこの決まりに寄せる。座標は小数 1 桁の字。JS を使わない。
"""

from decimal import Decimal

from django import template

register = template.Library()

# 図の枠の幅 (viewBox。ダッシュボードの図と同じ 640)・1 行の高さ・上下の余白。高さは行の数で決める。
WIDTH = 640
ROW = 28
PAD = 8
# 軸の字の右の端 (text-anchor end)・棒の左の端 (基線)・一番大きい値の棒の長さ・棒の太さ・右の角の丸み・値の字までの隙間。
LABEL_X = 184
BAR_LEFT = 192
BAR_MAX = 360
BAR_HEIGHT = 16
RADIUS = 4
VALUE_GAP = 8
# 軸の字の字数の上限 (長ければ末尾に「…」。全文は棒の title と表で読む)・図に描く行の上限 (残りは表で読む)。
LABEL_MAX = 14
MAX_BARS = 30


def _num(value) -> str:
    return f"{value:.1f}"


def _short(text: str) -> str:
    return text if len(text) <= LABEL_MAX else text[:LABEL_MAX] + "…"


def _bar_path(top: float, length: float) -> str:
    """横棒の形 (path の d)。右の 2 つの角だけ丸み (棒が短ければ長さまで)、基線の側は角のまま。"""
    radius = min(RADIUS, length, BAR_HEIGHT / 2)
    right = BAR_LEFT + length
    bottom = top + BAR_HEIGHT
    return (
        f"M{_num(BAR_LEFT)},{_num(top)}"
        f" L{_num(right - radius)},{_num(top)}"
        f" Q{_num(right)},{_num(top)} {_num(right)},{_num(top + radius)}"
        f" L{_num(right)},{_num(bottom - radius)}"
        f" Q{_num(right)},{_num(bottom)} {_num(right - radius)},{_num(bottom)}"
        f" L{_num(BAR_LEFT)},{_num(bottom)} Z"
    )


def chart_for(rows, *, limit: int = MAX_BARS) -> dict:
    """横棒の図の数。rows = [{"label", "value"}, ...] (並びのまま上から)。値が 0 以下の行は棒を描かない (字と値は出す)。

    返す物 = {"width", "height", "label_x", "base_x", "top", "bottom", "bars", "rest"}。
    1 本 = {"label" (LABEL_MAX 字まで), "full" (全文), "value", "y" (行の中央), "d" (棒が無ければ空), "value_x"}。
    """
    shown = list(rows[:limit])
    largest = max((Decimal(str(row["value"])) for row in shown), default=Decimal(0))
    bars = []
    for index, row in enumerate(shown):
        middle = PAD + ROW * index + ROW / 2
        value = Decimal(str(row["value"]))
        length = float(value / largest) * BAR_MAX if largest > 0 and value > 0 else 0.0
        bars.append(
            {
                "label": _short(str(row["label"])),
                "full": str(row["label"]),
                "value": row["value"],
                "y": _num(middle),
                "d": _bar_path(middle - BAR_HEIGHT / 2, length) if length > 0 else "",
                "value_x": _num(BAR_LEFT + length + VALUE_GAP),
            }
        )
    height = PAD * 2 + ROW * max(len(shown), 1)
    return {
        "width": WIDTH,
        "height": height,
        "label_x": _num(LABEL_X),
        "base_x": _num(BAR_LEFT),
        "top": _num(PAD),
        "bottom": _num(height - PAD),
        "bars": bars,
        "rest": max(len(rows) - len(shown), 0),
        "row_height": ROW,
        "plot_left": _num(LABEL_X),
        "plot_width": _num(BAR_LEFT + BAR_MAX + VALUE_GAP - LABEL_X),
    }


@register.inclusion_tag("core/_summary_chart.html")
def summary_chart(summary, chart_id="summary-chart"):
    """集計 (core/summary.py の summarize の戻り値) の横棒の図。{% summary_chart summary %}。"""
    title = f"{summary['axis'].label}ごとの{summary['measure'].label}"
    return {"chart": chart_for(summary["rows"]), "chart_id": chart_id, "title": title, "money": summary["measure"].money}
