"""注残の盤 (注文残の一覧。02 の 8 節) の列の値・絞り込み・合計を作る、読むだけの module。表に書かない。

- 1 行の判断の値は BoardRow。OrderLine.board (読むだけの cached_property) が作り、一覧の columns が board__<名前> で読む。
- 指示書・工程・実績の合計は prefetch_for で 1 ページ分まとめて読む。annotate_remaining の後に多の join を足さない
  (出荷の Sum と同じ問い合わせに指示書の join を足すと、残が重なって数えられるため)。BoardRow は .all() だけを使う。
- 部品 (workorders・progress) が無い会社では、指示書と進捗の値は空 (「未」・空の字・None) で、盤は開く。
- ほかの部品の model は import せず、apps.get_model と外部キーの先で関数の中で引く。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import cached_property

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db.models import DecimalField, Prefetch, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from . import services
from .models import Order

#: 判断の印の code と字 (印の字はこの 1 か所。テンプレートは code で色を選ぶ)。規則は BoardRow.judgement_code
JUDGEMENTS = (
    ("cancelled", "取消"),
    ("done", "出荷済み"),
    ("ship", "出荷できる"),
    ("late", "遅れ"),
    ("need_workorder", "指示書が要る"),
    ("making", "製造中"),
)
_JUDGEMENT_LABELS = dict(JUDGEMENTS)

# 数の合計の型 (OrderLine.qty と同じ桁)
_QTY_FIELD = DecimalField(max_digits=12, decimal_places=2)
_ZERO = Decimal("0")


def _num(value) -> str:
    """数の字。末尾の 0 を落とす (10.00 → 10、4.50 → 4.5)。SQLite は集計の値を桁なしで返すので Decimal(str()) で受ける。"""
    dec = (value if isinstance(value, Decimal) else Decimal(str(value))).normalize()
    if dec == dec.to_integral_value():
        return str(dec.quantize(Decimal(1)))
    return format(dec, "f")


def _workorder_models():
    """(WorkOrder, WorkOrderStep)。workorders の部品が無ければ (None, None)。"""
    try:
        return apps.get_model("workorders", "WorkOrder"), apps.get_model("workorders", "WorkOrderStep")
    except LookupError:
        return None, None


def _has_progress(step_model) -> bool:
    """指示書の工程に実績の逆の関係 (progress_records) があるか (progress の部品が入っているか)。"""
    try:
        step_model._meta.get_field("progress_records")
    except FieldDoesNotExist:
        return False
    return True


def prefetch_for(queryset):
    """指示書 (取消を除く) と工程と、工程ごとの実績の合計 (recorded_total) を 1 ページ分まとめて読む prefetch を足す。

    行の数に関わらず、指示書と工程の 2 回の問い合わせで済む。workorders の部品が無ければ queryset をそのまま返す。
    """
    workorder_model, step_model = _workorder_models()
    if workorder_model is None:
        return queryset
    if _has_progress(step_model):
        # progress.boards.with_totals と同じ式 (実績の数の合計、実績が無ければ 0)。
        recorded = Coalesce(Sum("progress_records__qty"), Value(_ZERO), output_field=_QTY_FIELD)
    else:
        recorded = Value(_ZERO, output_field=_QTY_FIELD)
    steps = step_model.objects.select_related("process").order_by("seq").annotate(recorded_total=recorded)
    workorders = (
        workorder_model.objects.exclude(status=workorder_model.STATUS_CANCELLED)
        .order_by("issued_on", "pk")
        .prefetch_related(Prefetch("steps", queryset=steps))
    )
    return queryset.prefetch_related(Prefetch("workorders", queryset=workorders))


def _date(raw):
    """YYYY-MM-DD の字を日付にする。空・読めない字は None (画面は 200 のまま)。"""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def filters(params) -> dict:
    """GET の値から絞り込みの辞書を作る (納期の期間 due_from・due_to と、納期が過ぎた物だけ overdue)。"""
    return {
        "due_from": _date(params.get("due_from")),
        "due_to": _date(params.get("due_to")),
        "overdue": params.get("overdue") == "1",
    }


def apply_filters(queryset, filters, today=None):
    """受注の明細の queryset に絞り込みを当てる。annotate_remaining の前に当てる (WHERE に入る)。"""
    if filters.get("due_from") is not None:
        queryset = queryset.filter(due_date__gte=filters["due_from"])
    if filters.get("due_to") is not None:
        queryset = queryset.filter(due_date__lte=filters["due_to"])
    if filters.get("overdue"):
        if today is None:
            today = timezone.localdate()
        queryset = queryset.filter(due_date__lt=today)
    return queryset


def totals_of(queryset) -> dict:
    """絞り込みの後の全部の明細の合計 (数量・出荷済み・残)。annotate の列の合計は Django が副問い合わせにする。"""
    sums = queryset.aggregate(
        qty=Coalesce(Sum("qty"), Value(_ZERO), output_field=_QTY_FIELD),
        shipped=Coalesce(Sum("shipped_total"), Value(_ZERO), output_field=_QTY_FIELD),
        remaining=Coalesce(Sum("remaining_total"), Value(_ZERO), output_field=_QTY_FIELD),
    )
    return {"qty": sums["qty"], "shipped": sums["shipped"], "remaining": sums["remaining"]}


class BoardRow:
    """注残の盤の 1 行 (受注の明細 1 つ) の判断の値。値は 1 回だけ計算する。表に書かない。

    指示書と工程は line.workorders.all() と workorder.steps.all() だけで読む (prefetch があればその写し)。
    all() の後に filter・exclude・order_by を呼ぶと prefetch が効かず N+1 になるので、絞りと並べ替えは Python で行う。
    """

    def __init__(self, line, today=None):
        self.line = line
        self.today = today if today is not None else timezone.localdate()

    # --- 指示書 ---
    @cached_property
    def workorders(self) -> list:
        """取消でない指示書の並び (発行日 → pk の順)。workorders の部品が無ければ空。"""
        manager = getattr(self.line, "workorders", None)
        if manager is None:
            return []
        rows = [w for w in manager.all() if w.status != type(w).STATUS_CANCELLED]
        rows.sort(key=lambda w: (w.issued_on, w.pk))
        return rows

    @cached_property
    def issued_total(self) -> Decimal:
        """指示書の数量の合計 (無ければ 0)。"""
        return sum((w.qty for w in self.workorders), _ZERO)

    @cached_property
    def accepted_count(self) -> int:
        """製造部が受けた指示書の数。"""
        return sum(1 for w in self.workorders if getattr(w, "accepted_at", None) is not None)

    @cached_property
    def workorder_count(self) -> int:
        return len(self.workorders)

    @cached_property
    def workorder_label(self) -> str:
        """「指示書」の列の字。無ければ「未」。あれば「<数量の合計> <受けた・一部受けた・発行>」。"""
        if not self.workorders:
            return "未"
        if self.accepted_count == self.workorder_count:
            state = "受けた"
        elif self.accepted_count:
            state = "一部受けた"
        else:
            state = "発行"
        return f"{_num(self.issued_total)} {state}"

    @cached_property
    def latest_workorder(self):
        """最後に発行した指示書 (現品票の link 用)。無ければ None。"""
        return self.workorders[-1] if self.workorders else None

    # --- 工程と実績 ---
    @cached_property
    def _steps_by_workorder(self) -> list:
        """指示書ごとの工程の並び (順 = seq の順)。"""
        return [sorted(w.steps.all(), key=lambda s: (s.seq, s.pk)) for w in self.workorders]

    @cached_property
    def steps(self) -> list:
        """指示書の順に、工程を順 (seq) で並べた物。"""
        return [step for steps in self._steps_by_workorder for step in steps]

    @cached_property
    def _recorded(self) -> dict:
        """工程の pk → 実績の合計。prefetch の recorded_total が無ければ progress.services で読む (関数の試験のため)。"""
        totals = {}
        for step in self.steps:
            total = getattr(step, "recorded_total", None)
            if total is None:
                total = self._recorded_qty(step)
            totals[step.pk] = total if isinstance(total, Decimal) else Decimal(str(total))
        return totals

    @staticmethod
    def _recorded_qty(step):
        if not _has_progress(type(step)):
            return _ZERO
        from seizou.progress.services import recorded_qty

        return recorded_qty(step)

    def recorded_of(self, step) -> Decimal:
        """工程の実績の合計。"""
        return self._recorded.get(step.pk, _ZERO)

    @cached_property
    def current_step(self):
        """実績の合計が予定の数に満たない最初の工程 (無ければ None)。"""
        return next((step for step in self.steps if self.recorded_of(step) < step.planned_qty), None)

    @cached_property
    def completed_total(self) -> Decimal:
        """完成の数 = 指示書ごとの最後の工程の実績の合計の和 (工程の無い指示書は 0。指示書の数量で切らない = 現物優先)。"""
        return sum((self.recorded_of(steps[-1]) for steps in self._steps_by_workorder if steps), _ZERO)

    @cached_property
    def progress_label(self) -> str:
        """「進捗」の列の字。指示書が無ければ空。「<今の工程> <実績>/<予定>」か「完了」。"""
        if not self.workorders:
            return ""
        step = self.current_step
        if step is None:
            return "完了"
        return f"{step.process.name} {_num(self.recorded_of(step))}/{_num(step.planned_qty)}"

    # --- 計画と納期 ---
    @cached_property
    def plan_end(self):
        """工程の予定の終了日の最大 (無ければ None)。"""
        ends = [step.planned_end for step in self.steps if step.planned_end is not None]
        return max(ends) if ends else None

    @cached_property
    def plan_late(self) -> bool:
        """計画の終了日が納期より後か。"""
        due = self.line.due_date
        return self.plan_end is not None and due is not None and self.plan_end > due

    @cached_property
    def days_left(self):
        """納期までの日数 (過ぎていればマイナス)。納期が無ければ None。"""
        due = self.line.due_date
        if due is None:
            return None
        return (due - self.today).days

    @cached_property
    def is_overdue(self) -> bool:
        return self.days_left is not None and self.days_left < 0

    # --- 判断 ---
    @cached_property
    def remaining(self) -> Decimal:
        """残 (annotate_remaining の remaining_total があればそれ、無ければ services.remaining_qty)。"""
        total = getattr(self.line, "remaining_total", None)
        if total is None:
            total = services.remaining_qty(self.line)
        return total if isinstance(total, Decimal) else Decimal(str(total))

    @cached_property
    def judgement_code(self) -> str:
        """判断の印の code。上から順に最初に当たった物 (決まった規則だけ。自動の判断はしない)。

        1 取消: 受注が取消 / 2 出荷済み: 残が 0 以下 / 3 出荷できる: 完成の数が残以上 /
        4 遅れ: 納期が今日より前 / 5 指示書が要る: 指示書の数量の合計が残より少ない / 6 製造中: ほか。
        """
        if self.line.order.status == Order.STATUS_CANCELLED:
            return "cancelled"
        remaining = self.remaining
        if remaining <= 0:
            return "done"
        if self.completed_total >= remaining:
            return "ship"
        if self.is_overdue:
            return "late"
        if self.issued_total < remaining:
            return "need_workorder"
        return "making"

    @cached_property
    def judgement(self) -> str:
        """判断の印の字 (JUDGEMENTS から引く)。"""
        return _JUDGEMENT_LABELS[self.judgement_code]
