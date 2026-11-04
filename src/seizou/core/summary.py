"""一覧の集計 (決め 11-04 17:5x〜18:0x「集計を見える化」と 22:0x「集計の図は 1 か所で色と形を決める」)。

一覧 (BaseListView) の GET view=summary で、表の代わりに集計の表と横棒の図を出す。絞りの枠はそのまま効く (絞った後の集計)。
- 軸 (GET g_by): 一覧の絞り (filters と会社ごとの項目の絞り) の choice と lookup の項目と、date_range の項目の月 (<name>:month)。
  多の側へ辿る項目 (逆向きの FK と多対多。例 受注の一覧の納期 = lines__due_date) は軸にしない (1 件が何回も数えられるため)。
- 量 (GET g_val): count (件数) か、一覧の列のうち表の数の項目の名前 (合計)。どの列を渡すかは BaseListView が決める。
  金額の列は、金額の閲覧が無い人には出さない (GET で指定しても件数に戻す)。
- 既定は最初の choice の軸 (無ければ最初の軸) × 件数。知らない値は既定に戻す (誤りにしない)。
集計は ORM の values と annotate (Count・Sum。月は TruncMonth) で作り、SQL を書かない。一覧の queryset (検索と絞りと部品の
get_queryset の後) は pk の集まりとして受け、model の素の queryset で数える (部品の annotate・並び・distinct・prefetch を持ち込まない)。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models import Count, F, Sum
from django.db.models.functions import TruncMonth

#: GET の view の値 (集計の形)
VIEW = "summary"
#: 集計が使う GET の名前 (表に戻る link はこれを外す)
PARAMS = ("view", "g_by", "g_val")
#: 量の「件数」の鍵
COUNT = "count"
#: 月の軸の鍵の末尾 (<絞りの name>:month)
MONTH = ":month"
#: 値が空の行の字
EMPTY_LABEL = "未設定"
#: 割合の桁 (小数 1 桁)
SHARE_PLACES = Decimal("0.1")


@dataclass(frozen=True)
class Axis:
    """集計の軸 1 つ。key は GET の g_by の値、path は values に渡す項目の名前 (__ で辿る)。"""

    key: str
    label: str
    kind: str  # choice / lookup / month
    path: str
    spec: object  # 元の FilterSpec (choice の字と並び、lookup の先の model を読む)


@dataclass(frozen=True)
class Measure:
    """集計の量 1 つ。key は GET の g_val の値。path が None なら件数。"""

    key: str
    label: str
    path: str | None
    money: bool


def _fields_on_path(model, path: str):
    """__ で辿る項目の並び。辿れなければ None。"""
    fields, current = [], model
    for part in path.split("__"):
        if current is None:
            return None
        try:
            field = current._meta.get_field(part)
        except FieldDoesNotExist:
            return None
        fields.append(field)
        current = field.related_model
    return fields


def _single_valued(fields) -> bool:
    """辿る途中に多の側 (逆向きの FK・多対多) が無いか。"""
    return not any(field.one_to_many or field.many_to_many for field in fields)


def axes_for(model, filter_specs) -> list[Axis]:
    """絞りの並びから軸の並びを作る (宣言の順。月の軸は date_range の項目の位置)。"""
    axes = []
    for spec in filter_specs:
        if spec.kind not in ("choice", "lookup", "date_range"):
            continue
        fields = _fields_on_path(model, spec.field_name)
        if not fields or not _single_valued(fields):
            continue
        last = fields[-1]
        if spec.kind == "date_range":
            if isinstance(last, models.DateField):  # DateTimeField は DateField の子
                axes.append(Axis(spec.name + MONTH, f"{spec.label}の月", "month", spec.field_name, spec))
            continue
        axes.append(Axis(spec.name, str(spec.label), spec.kind, spec.field_name, spec))
    return axes


def measures_for(column_specs, *, money_ok: bool) -> list[Measure]:
    """件数と、渡された列 (表の数の項目) の合計。金額の列は money_ok が真のときだけ。

    見出しが「合計」で終わる列 (請求書の一覧の「合計」) は、その字のまま量の名前にする (「合計の合計」にしない)。
    """
    measures = [Measure(COUNT, "件数", None, False)]
    for spec in column_specs:
        if spec.money and not money_ok:
            continue
        label = str(spec.label)
        if not label.endswith("合計"):
            label = f"{label}の合計"
        measures.append(Measure(spec.name, label, spec.name, spec.money))
    return measures


def pick_axis(axes: list[Axis], key: str) -> Axis:
    """GET の値の軸。知らない値は最初の choice の軸 (無ければ最初の軸)。axes は空でない。"""
    for axis in axes:
        if axis.key == key:
            return axis
    return next((axis for axis in axes if axis.kind == "choice"), axes[0])


def pick_measure(measures: list[Measure], key: str) -> Measure:
    """GET の値の量。知らない値 (金額の閲覧が無い人の金額を含む) は件数。"""
    return next((measure for measure in measures if measure.key == key), measures[0])


def summarize(queryset, axis: Axis, measure: Measure) -> dict:
    """軸ごとの量の行と合計。

    返す物 = {"axis", "measure", "rows", "total", "count"}。1 行 = {"key", "label", "value", "share"}。
    share は合計に対する割合 (%。小数 1 桁) で、合計が 0 以下なら None。並びは軸の順 (月は昇順、choice は候補の順、
    lookup は字の順。値が空の行は最後)。
    """
    base = queryset.model._default_manager.filter(pk__in=queryset.prefetch_related(None).order_by().values("pk"))
    if axis.kind == "month":
        base = base.annotate(seizou_axis=TruncMonth(axis.path))
    else:
        base = base.annotate(seizou_axis=F(axis.path))
    amount = Count("pk") if measure.path is None else Sum(measure.path)
    found = base.order_by().values("seizou_axis").annotate(seizou_value=amount).order_by()
    pairs = [(row["seizou_axis"], row["seizou_value"] or 0) for row in found]
    rows = [{"key": key, "label": label, "value": value} for key, label, value in _labelled(axis, pairs)]
    total = sum((row["value"] for row in rows), 0)
    for row in rows:
        row["share"] = _share(row["value"], total)
    return {"axis": axis, "measure": measure, "rows": rows, "total": total, "count": len(rows)}


def _labelled(axis: Axis, pairs):
    """(値, 量) の組を (値, 字, 量) にし、軸の順に並べる。"""
    empty = [(None, EMPTY_LABEL, value) for key, value in pairs if key in (None, "")]
    present = [(key, value) for key, value in pairs if key not in (None, "")]
    if axis.kind == "month":
        named = [(key, key.strftime("%Y-%m"), value) for key, value in sorted(present, key=lambda pair: pair[0])]
        return named + empty
    names = {}
    order = {}
    if axis.kind == "choice":
        choices = axis.spec.choice_values()
        names = dict(choices)
        order = {value: index for index, (value, _text) in enumerate(choices)}
    related = axis.spec.field.related_model if axis.spec.field is not None else None
    missing = [key for key, _value in present if str(key) not in names]
    if related is not None and missing:
        objects = related._default_manager.in_bulk(missing)
        names.update({str(pk): str(obj) for pk, obj in objects.items()})
    named = [(key, names.get(str(key), str(key)), value) for key, value in present]
    named.sort(key=lambda item: (order.get(str(item[0]), len(order)), item[1]))
    return named + empty


def _share(value, total):
    if not total or total <= 0:
        return None
    return (Decimal(str(value)) * 100 / Decimal(str(total))).quantize(SHARE_PLACES, rounding=ROUND_HALF_UP)


def csv_rows(summary: dict) -> list[list[str]]:
    """CSV の行 (見出し・軸ごとの行・合計の行)。画面の表と同じ行。"""
    head = [summary["axis"].label, summary["measure"].label, "割合 (%)"]
    body = [[row["label"], str(row["value"]), "" if row["share"] is None else str(row["share"])] for row in summary["rows"]]
    total = summary["total"]
    foot = ["合計", str(total), "100.0" if total and total > 0 else ""]
    return [head, *body, foot]
