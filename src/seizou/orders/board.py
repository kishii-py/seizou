"""注残の盤 (注文残の一覧。一覧の画面の設計の決め) の列の値と合計を作る、読むだけの module。表に書かない。

- 1 行の判断の値は BoardRow。OrderLine.board (読むだけの cached_property) が作り、一覧の columns が board__<名前> で読む。
- 指示書・工程・実績の合計は prefetch_for で 1 ページ分まとめて読む。annotate_remaining の後に多の join を足さない
  (出荷の Sum と同じ問い合わせに指示書の join を足すと、残が重なって数えられるため)。BoardRow は .all() だけを使う。
- 部品 (workorders・progress) が無い会社では、指示書と進捗の値は空 (「未」・空の字・None) で、盤は開く。
- 在庫 (stock) は annotate_balance の Subquery で読む (join を足さない)。無ければ None。
- 引当の合計 (receiving。「預かり」の列) は receiving.services.annotate_allocated の Subquery で読む (join を足さない)。
  無ければ None。「預かり待ち」の札は、引当の合計が受注の数より少なく、会社の設定 receiving_required が off (通す) で
  ないときだけ (本体の決め 11-06 20:1x。通すの会社は支給材の無い注文が多く、全部の行に札が付くため)。開発の記録 docs/nights/20261106/N184.md。
- 出荷 (shipping) と納品書は、行のメニューの「納品書を作る」の対象を決めるため、prefetch_for が別の問い合わせで読む (join を足さない)。
- 協力企業に出した工程の出ている数とまだ渡していない数 (purchasing) は、prefetch_for が工程の問い合わせに
  purchasing.services.annotate_subcontract の Subquery で足し (問い合わせを増やさない)、「進捗」の字の後ろに「・協力企業 n」「・渡す前 n」
  として出す (BoardRow.progress_label。見比べのまとめの M01 の 3 段目)。無ければ足さない。判断の印と列と絞りは変えない。
- ほかの部品の model は import せず、apps.get_model と外部キーの先で関数の中で引く。
- 納期まで (days_left) は稼働日の数 (core.calendar)。遅れの判断は暦の日。枡の字は days_left_label
  (「n 日遅れ」「今日」「あと n 日」。ダッシュボードの納期アラートと同じ型。直し 11-07)。
- 盤の絞り (出荷の残りがある物だけ・納期・納期が過ぎた物だけ) は views.OrderLineListView.filters の宣言 (決め 11-04 17:3x)。
"""

from __future__ import annotations

from decimal import Decimal
from functools import cached_property

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Count, DecimalField, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from seizou.core import calendar, conf

from . import services
from .models import Order, OrderLine

#: 判断の印の code と字 (印の字はこの 1 か所。テンプレートは code で色を選ぶ)。規則は BoardRow.judgement_code
JUDGEMENTS = (
    ("cancelled", "取消"),
    ("completed", "完了"),
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


def _dec(value) -> Decimal:
    """集計の値を Decimal で受ける (SQLite は集計の値を桁なしで返すことがある)。None は 0。"""
    if value is None:
        return _ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _workorder_models():
    """(WorkOrder, WorkOrderStep)。workorders の部品が無ければ (None, None)。"""
    try:
        return apps.get_model("workorders", "WorkOrder"), apps.get_model("workorders", "WorkOrderStep")
    except LookupError:
        return None, None


def _shipment_model():
    """出荷の model (Shipment)。shipping の部品が無ければ None。"""
    try:
        return apps.get_model("shipping", "Shipment")
    except LookupError:
        return None


def _has_progress(step_model) -> bool:
    """指示書の工程に実績の逆の関係 (progress_records) があるか (progress の部品が入っているか)。"""
    try:
        step_model._meta.get_field("progress_records")
    except FieldDoesNotExist:
        return False
    return True


def annotate_acceptance(queryset):
    """検収の数の合計 (acceptance_total) を副問い合わせで足す。connect の部品が無い (逆の関係 acceptances が無い) ときは 0。

    orders は connect を import しない (逆の関係の model を OrderLine の descriptor から取る)。副問い合わせなので、
    annotate_remaining の出荷の Sum (join) と掛け合わない。決め 11-03 (EDI の 4 段目 = 検収。開発の記録 docs/nights/20261103/N54.md が落とした盤の列)。
    """
    relation = getattr(OrderLine, "acceptances", None)
    if relation is None:
        return queryset.annotate(acceptance_total=Value(_ZERO, output_field=_QTY_FIELD))
    model = relation.rel.related_model
    total = (
        model._default_manager.filter(order_line=OuterRef("pk"))
        .order_by()
        .values("order_line")
        .annotate(total=Sum("qty"))
        .values("total")[:1]
    )
    return queryset.annotate(
        acceptance_total=Coalesce(Subquery(total, output_field=_QTY_FIELD), Value(_ZERO), output_field=_QTY_FIELD)
    )


def prefetch_for(queryset):
    """指示書 (取消を除く) と工程と、工程ごとの実績の合計 (recorded_total) と不良の合計 (defect_total)・完了の印 (completed_mark)
    を 1 ページ分まとめて読む prefetch を足す。

    行の数に関わらず、指示書と工程の 2 回の問い合わせで済む。workorders の部品が無ければ queryset をそのまま返す。
    出荷 (納品書つき) も 1 回の問い合わせで読む (BoardRow.unnoted_shipment のため。shipping の部品が無ければ読まない)。
    引当の合計 (allocated_total) は receiving の部品があれば Subquery で足す (問い合わせを増やさない。開発の記録 docs/nights/20261106/N184.md)。
    """
    queryset = annotate_acceptance(queryset)
    if apps.is_installed("seizou.receiving"):
        # 引当の合計は Subquery なので、annotate_remaining の後に当てても残は変わらず、問い合わせの数も増えない。
        from seizou.receiving.services import annotate_allocated

        queryset = annotate_allocated(queryset)
    shipment_model = _shipment_model()
    if shipment_model is not None:
        # prefetch は別の問い合わせなので、annotate_remaining の出荷の Sum と掛け合わない (開発の記録 docs/nights/20261104/N130.md)。
        shipments = shipment_model.objects.select_related("delivery_note").order_by("shipped_at", "pk")
        queryset = queryset.prefetch_related(Prefetch("shipments", queryset=shipments))
    workorder_model, step_model = _workorder_models()
    if workorder_model is None:
        return queryset
    workorders = (
        workorder_model.objects.exclude(status=workorder_model.STATUS_CANCELLED)
        .order_by("issued_on", "pk")
        .prefetch_related(Prefetch("steps", queryset=steps_for_board(step_model)))
    )
    if apps.is_installed("seizou.stock"):
        # 在庫数は Subquery なので、annotate_remaining の後に当てても残は変わらず、問い合わせの数も増えない。
        from seizou.stock.services import annotate_balance

        queryset = annotate_balance(queryset, "item_id")
    return queryset.prefetch_related(Prefetch("workorders", queryset=workorders))


def steps_for_board(step_model):
    """BoardRow が読む工程の queryset (工程の名前つき・順の昇順)。生産管理ボード (prefetch_for) と受注の詳細 (timeline.detail_lookups) が使う。

    指示書の状態・進捗の盤と同じ式 (良品の合計・不良の合計・完了の印。progress の部品が無ければ 0・0・偽の Value) を足す。
    印は Exists の副問い合わせなので、工程の問い合わせは 1 回のまま。BoardRow は足した値を読み、工程ごとに問い合わせない。
    集計の annotate を足すと model の既定の並び (Meta.ordering) が外れるので、順 (seq) の並びを書いて付ける。
    """
    steps = step_model.objects.select_related("process").order_by("seq").annotate(**step_model.total_expressions())
    if apps.is_installed("seizou.purchasing"):
        # 協力企業に出した工程の出ている数とまだ渡していない数 (BoardRow.progress_label)。3 つとも Subquery なので、実績の Sum と
        # 受注の明細の残の Sum と掛け合わず、工程の問い合わせは 1 回のまま (見比べのまとめの M01 の 3 段目)。
        from seizou.purchasing.services import annotate_subcontract

        steps = annotate_subcontract(steps)
    return steps


def totals_of(queryset) -> dict:
    """絞り込みの後の全部の明細の合計 (数量・出荷済み・残・検収)。annotate の列の合計は Django が副問い合わせにする。

    検収 (accepted) は annotate_acceptance を通した queryset だけ足す (通していなければ 0)。
    """
    aggregates = {
        "qty": Coalesce(Sum("qty"), Value(_ZERO), output_field=_QTY_FIELD),
        "shipped": Coalesce(Sum("shipped_total"), Value(_ZERO), output_field=_QTY_FIELD),
        "remaining": Coalesce(Sum("remaining_total"), Value(_ZERO), output_field=_QTY_FIELD),
    }
    if "acceptance_total" in queryset.query.annotations:
        aggregates["accepted"] = Coalesce(Sum("acceptance_total"), Value(_ZERO), output_field=_QTY_FIELD)
    sums = queryset.aggregate(**aggregates)
    return {
        "qty": sums["qty"],
        "shipped": sums["shipped"],
        "remaining": sums["remaining"],
        "accepted": sums.get("accepted", _ZERO),
    }


class BoardRow:
    """注残の盤の 1 行 (受注の明細 1 つ) の判断の値。値は 1 回だけ計算する。表に書かない。

    指示書と工程は line.workorders.all() と workorder.steps.all() だけで読む (prefetch があればその写し)。
    all() の後に filter・exclude・order_by を呼ぶと prefetch が効かず N+1 になるので、絞りと並べ替えは Python で行う。
    """

    def __init__(self, line, today=None, *, holidays=None):
        self.line = line
        self.today = today if today is not None else timezone.localdate()
        self.holidays = holidays

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
    def workorder_states(self) -> list[str]:
        """指示書ごとの進みの状態の値 (workorders の WorkOrder.progress_state_of。指示書の詳細の題の横と同じ決め方)。

        工程ごとの実績のまとめは prefetch の annotate (_totals) を渡すので、問い合わせを増やさない。着手の印は読まない。
        """
        return [
            type(w).progress_state_of(w.status, steps, self._totals)
            for w, steps in zip(self.workorders, self._steps_by_workorder)
        ]

    @cached_property
    def all_done(self) -> bool:
        """指示書が 1 枚以上あり、全部の指示書の状態が完了 (最後の工程に完了の印。作者の決め)。「進捗」の「完了」と段の帯の「4 完了」が読む。"""
        if not self.workorders:
            return False
        return all(state == type(w).PROGRESS_DONE for w, state in zip(self.workorders, self.workorder_states))

    @cached_property
    def started_count(self) -> int:
        """実績が 1 件でもある (着手済か完了の) 指示書の数。段の帯の「着手」が読む (作者の決め)。"""
        return sum(
            1
            for w, state in zip(self.workorders, self.workorder_states)
            if state in (type(w).PROGRESS_STARTED, type(w).PROGRESS_DONE)
        )

    @cached_property
    def workorder_count(self) -> int:
        return len(self.workorders)

    @cached_property
    def workorder_label(self) -> str:
        """「指示書」の列の字。無ければ「未」。あれば「<数量の合計> <状態>」。

        状態は指示書の進みの状態の字 (WorkOrder.PROGRESS_LABELS) から選ぶ: 全部の指示書が完了なら「完了」、
        どれにも実績が無ければ「未着手」、ほかは「着手済」(指示書が 1 枚ならその指示書の状態と同じ字。
        製造部が指示書を受け取った合図の印は作らない = 本人の決め)。
        """
        if not self.workorders:
            return "未"
        model = type(self.workorders[0])
        states = self.workorder_states
        if all(state == model.PROGRESS_DONE for state in states):
            state = model.PROGRESS_DONE
        elif self.started_count == 0:
            state = model.PROGRESS_NOT_STARTED
        else:
            state = model.PROGRESS_STARTED
        return f"{_num(self.issued_total)} {model.PROGRESS_LABELS[state]}"

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
    def _totals(self) -> dict:
        """工程の pk → StepTotals (良品の合計・不良の合計・完了の印)。

        prefetch の annotate (recorded_total・defect_total・completed_mark) があれば問い合わせをしない。無ければ工程ごとに
        1 回の問い合わせで 3 つを読む (受注の詳細の段の帯は prefetch_for を通らずにこの道を読む。関数の試験も)。
        """
        totals = {}
        for step in self.steps:
            annotated = step.annotated_totals()
            totals[step.pk] = annotated if annotated is not None else self._read_totals(step)
        return totals

    @staticmethod
    def _read_totals(step):
        """工程 1 つの StepTotals を 1 回の問い合わせで読む。progress を import せず、工程の逆の関係の先として引く。"""
        totals_type = type(step).Totals
        if not _has_progress(type(step)):
            return totals_type()
        sums = step.progress_records.order_by().aggregate(
            recorded=Sum("qty"), defect=Sum("defect_qty"), marks=Count("pk", filter=Q(completes_step=True))
        )
        return totals_type(_dec(sums["recorded"]), _dec(sums["defect"]), bool(sums["marks"]))

    def recorded_of(self, step) -> Decimal:
        """工程の実績の合計 (良品の合計)。"""
        totals = self._totals.get(step.pk)
        return _ZERO if totals is None else totals.recorded

    @cached_property
    def current_step(self):
        """完了でない指示書の、完了の印の無い最初の工程 (無ければ None)。指示書が全部完了なら None (作者の決め)。"""
        model = type(self.workorders[0]) if self.workorders else None
        for state, steps in zip(self.workorder_states, self._steps_by_workorder):
            if state == model.PROGRESS_DONE:
                continue
            for step in steps:
                totals = self._totals.get(step.pk)
                if totals is None or not totals.completed:
                    return step
        return None

    @cached_property
    def completed_total(self) -> Decimal:
        """完成の数 = 指示書ごとの最後の工程の実績の合計の和 (工程の無い指示書は 0。指示書の数量で切らない = 現物優先)。"""
        return sum((self.recorded_of(steps[-1]) for steps in self._steps_by_workorder if steps), _ZERO)

    @cached_property
    def progress_label(self) -> str:
        """「進捗」の列の字。指示書が無ければ空。全部の指示書が完了なら「完了」、ほかは「<今の工程> <実績>/<予定>」。

        どちらでもなければ空 (工程の無い指示書だけの行)。
        今の工程を協力企業に出していれば、後ろに「・協力企業 <出ている数>」と「・渡す前 <まだ渡していない数>」を足す (0 より大きい数だけ。
        順は協力企業 → 渡す前。見比べのまとめの M01 の 3 段目)。数は purchasing.services.annotate_subcontract の値 (出した明細の受入の残を
        渡した日の有る無しで分けた合計)。prefetch_for が工程に足した値があれば問い合わせをせず、無ければ (受注の詳細の段の帯・関数の試験)
        今の工程 1 つだけを同じ関数で 1 回読む。purchasing の部品が無ければ足さない (今までの字のまま)。CSV の「進捗」の列も同じ字。
        """
        if not self.workorders:
            return ""
        if self.all_done:
            return "完了"
        step = self.current_step
        if step is None:
            return ""
        label = f"{step.process.name} {_num(self.recorded_of(step))}/{_num(step.planned_qty)}"
        if not apps.is_installed("seizou.purchasing"):
            return label
        names = ("subcontract_out_total", "subcontract_waiting_total")
        if all(hasattr(step, name) for name in names):
            out, waiting = (getattr(step, name) for name in names)
        else:
            from seizou.purchasing.services import annotate_subcontract

            row = annotate_subcontract(type(step)._default_manager.filter(pk=step.pk)).values(*names).get()
            out, waiting = (row[name] for name in names)
        out, waiting = _dec(out), _dec(waiting)
        if out > 0:
            label += f"・協力企業 {_num(out)}"
        if waiting > 0:
            label += f"・渡す前 {_num(waiting)}"
        return label

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
        """納期までの稼働日の数 (core.calendar.workdays_between。休日と定休日を除く)。納期が過ぎていれば -1 以下。納期が無ければ None。"""
        due = self.line.due_date
        if due is None or self.remaining <= 0:
            # 納期が無い行と、出荷の済んだ行 (残 0) は数を出さない (済んだ行に遅れの赤が付いて見えた。目視の所見 11-05)
            return None
        days = calendar.workdays_between(self.today, due, holidays=self.holidays)
        # 休みの日に盤を見ると、前の休みの日の納期は稼働日の数が 0 になる。遅れの行を 0 に見せない
        return min(days, -1) if due < self.today else days

    @cached_property
    def days_left_label(self) -> str:
        """「納期まで」の枡の字。ダッシュボードの納期アラート (core/home.html) と同じ型で、数は days_left (稼働日)。

        納期が過ぎていれば「n 日遅れ」、納期が今日なら「今日」、先なら「あと n 日」。days_left が None (納期が無い行・残 0 の行) は
        空の字 (枡の「—」はテンプレート)。「今日」は暦の日 (納期 = 今日) で決める。納期が先でも間に稼働日が無い日 (土曜に見る
        日曜の納期・金曜に見る土曜の納期) は「あと 0 日」。稼働日の 0 を「今日」と書くと、暦の日で数えるダッシュボードの
        「あと 1 日」と食い違うため (直し 11-07。0 の字は本人が見て直してよい)。
        """
        days = self.days_left
        if days is None:
            return ""
        if days < 0:
            return f"{-days} 日遅れ"
        if self.line.due_date == self.today:
            return "今日"
        return f"あと {days} 日"

    @cached_property
    def is_overdue(self) -> bool:
        """納期が今日より前か (暦の日で決める。稼働日の数でない)。"""
        due = self.line.due_date
        return due is not None and due < self.today

    # --- 在庫 ---
    @cached_property
    def stock_balance(self):
        """在庫数 (読むだけ)。stock の部品が無ければ None。annotate の stock_balance があればそれ、無ければ stock.services.balance (関数の試験のため)。"""
        if not apps.is_installed("seizou.stock"):
            return None
        if "stock_balance" in self.line.__dict__:
            value = self.line.__dict__["stock_balance"]
            if value is None or isinstance(value, Decimal):
                return value
            return Decimal(str(value))
        from seizou.stock.services import balance

        return balance(self.line.item)

    # --- 検収 ---
    @cached_property
    def acceptance_total(self) -> Decimal:
        """検収の数の合計 (読むだけ)。annotate の acceptance_total があればそれ、無ければ services.acceptance_summary (関数の試験のため)。"""
        if "acceptance_total" in self.line.__dict__:
            value = self.line.__dict__["acceptance_total"]
            return _ZERO if value is None else Decimal(str(value))
        return services.acceptance_summary(self.line)["total"]

    # --- 預かり (入荷の引当) ---
    @cached_property
    def allocated(self):
        """引当の合計 (読むだけ。「預かり」の列)。receiving の部品が無ければ None。

        annotate の allocated_total があればそれ、無ければ receiving.services.allocated_total (関数の試験のため)。
        """
        if not apps.is_installed("seizou.receiving"):
            return None
        if "allocated_total" in self.line.__dict__:
            value = self.line.__dict__["allocated_total"]
            return _ZERO if value is None else Decimal(str(value))
        from seizou.receiving.services import allocated_total

        return allocated_total(self.line)

    @cached_property
    def receiving_waiting(self) -> bool:
        """「預かり待ち」の札を出すか。引当の合計が受注の数より少なく、会社の設定 receiving_required が off でないとき。"""
        allocated = self.allocated
        if allocated is None or allocated >= self.line.qty:
            return False
        return conf.setting("receiving_required") != "off"

    # --- 納品書 ---
    @cached_property
    def unnoted_shipment(self):
        """納品書の無い出荷 (納品書が取消の物を含む) のうち、出荷日 → pk の順で最後の物。無ければ None (開発の記録 docs/nights/20261104/N130.md)。

        行のメニューの「納品書を作る」が対象にする。決まりは納品書の入力の候補 (shipping の DeliveryNoteForm) と同じ。
        shipping の部品が無ければ None。prefetch があればその写しを読む (.all() だけ。納品書は select_related の写し)。
        """
        manager = getattr(self.line, "shipments", None)
        if manager is None:
            return None
        rows = [
            s
            for s in manager.all()
            if s.delivery_note_id is None or s.delivery_note.status == type(s.delivery_note).STATUS_CANCELLED
        ]
        return max(rows, key=lambda s: (s.shipped_at, s.pk), default=None)

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

        1 取消: 受注が取消 / 2 完了: 完了の印のある明細で、出荷済みの数が数量より少ない (足りないまま完了にした。N400b。RULES.md R25) /
        3 出荷済み: 残が 0 以下 (数量に届いた完了の明細もここ) / 4 出荷できる: 完成の数が残以上、か、在庫が残以上 /
        5 遅れ: 納期が今日より前 / 6 指示書が要る: 指示書の数量の合計が残より少ない / 7 製造中: ほか。
        出荷済みの数は annotate_remaining の shipped_total (盤の一覧の写し) があればそれ、無ければ services.shipped_qty (関数の試験のため)。
        """
        if self.line.order.status == Order.STATUS_CANCELLED:
            return "cancelled"
        if self.line.completed:
            shipped = getattr(self.line, "shipped_total", None)
            if _dec(services.shipped_qty(self.line) if shipped is None else shipped) < self.line.qty:
                return "completed"
        remaining = self.remaining
        if remaining <= 0:
            return "done"
        if self.completed_total >= remaining or (self.stock_balance is not None and self.stock_balance >= remaining):
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
