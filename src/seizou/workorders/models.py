"""指示書の表 (表の設計の決め)。

指示書は受注の明細を指し、1 つの明細に何枚でも出せる。工程は指示書ごとに順を持つ。
指示書の数量の合計は列に持たず、workorders.services の issued_qty で毎回出す。
文字数の上限は固定 (識別子 35・備考 500・コード 10)。
書くときは workorders.services を通す。
指示書の進みの状態 (未着手・着手済・完了・取消) は列に持たず、工程の実績から WorkOrder.progress_state で毎回出す
(本人の決め 11-07 00:36)。完了は工程の実績の完了の印から出す (作者の決め)。
工程ごとの進み (一覧の「工程の進み」の列の棒) も列に持たず、同じ実績のまとめから WorkOrder.step_progress で毎回出す (本人の決め 11-07 18:2x〜18:4x)。
"""

from decimal import Decimal
from typing import NamedTuple

from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models import Exists, OuterRef, Sum, Value
from django.db.models.functions import Coalesce

#: 実績の合計の型 (WorkOrderStep.planned_qty と同じ桁)
_QTY_FIELD = models.DecimalField(max_digits=12, decimal_places=2)
_ZERO = Decimal("0")


class StepTotals(NamedTuple):
    """工程 1 つの実績のまとめ (読むだけ)。recorded = 良品の合計・defect = 不良の合計・completed = 完了の印の付いた実績がある。"""

    recorded: Decimal = Decimal("0")
    defect: Decimal = Decimal("0")
    completed: bool = False


#: 実績の無い工程 (辞書に無い工程と、進捗の部品が無い置き場)。
NO_TOTALS = StepTotals()


class StepProgressRow(NamedTuple):
    """工程 1 つの進み (一覧の「工程の進み」の列の 1 行。読むだけ)。seq = 順・name = 工程の名前・planned = 予定の数・
    recorded = 良品の合計・pct = 棒の長さ (0〜100 の整数。実績 ÷ 予定を 100% で止めて切り捨て)・full = 予定の数に届いた (100%)。"""

    seq: int
    name: str
    planned: Decimal
    recorded: Decimal
    pct: int
    full: bool


class StepProgress(list):
    """指示書の工程ごとの進みの並び (StepProgressRow を工程の順に)。一覧の行 (workorders/_row.html) は for で回し、
    CSV と title は str (「切断 3/10・曲げ 0/10」。工程が無ければ空の字) を使う。"""

    def __str__(self):
        return "・".join(f"{row.name} {_qty_text(row.recorded)}/{_qty_text(row.planned)}" for row in self)


def _qty_text(value) -> str:
    """数量の字。末尾の 0 を落とす (10.00 → 10、2.50 → 2.5)。progress・stock・purchasing の models と同じ関数 (ほかの部品の model を import しない)。"""
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(dec.normalize(), "f") if dec != dec.to_integral_value() else str(dec.quantize(Decimal(1)))


def _dec(value) -> Decimal:
    """集計の値を Decimal で受ける (SQLite は集計の値を桁なしで返すことがある)。None は 0。"""
    if value is None:
        return _ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


class WorkOrder(models.Model):
    """指示書。指示書番号は core の連番が付ける。"""

    STATUS_ISSUED = "issued"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_ISSUED, "発行"),
        (STATUS_CANCELLED, "取消"),
    ]

    # 進みの状態 (表の列でなく、工程の実績から出す。progress_state)。字は作者の決めの 4 つ。
    PROGRESS_NOT_STARTED = "not_started"
    PROGRESS_STARTED = "started"
    PROGRESS_DONE = "done"
    PROGRESS_CANCELLED = "cancelled"
    PROGRESS_LABELS = {
        PROGRESS_NOT_STARTED: "未着手",
        PROGRESS_STARTED: "着手済",
        PROGRESS_DONE: "完了",
        PROGRESS_CANCELLED: "取消",
    }

    wo_no = models.CharField("指示書番号", max_length=35, unique=True)
    order_line = models.ForeignKey(
        "orders.OrderLine",
        verbose_name="受注の明細",
        on_delete=models.PROTECT,
        related_name="workorders",
    )
    qty = models.DecimalField("数量", max_digits=12, decimal_places=2)
    issued_on = models.DateField("発行日")
    status = models.CharField(
        "状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_ISSUED
    )
    # 書くのは services.cancel_workorder だけ。
    cancel_reason = models.CharField("取消の理由", max_length=500, blank=True)

    #: 詳細と小窓に出さない列 (core/views/ の hidden_detail_fields が物 1 件ずつ読む)。
    #: 状態は実績から出す字 (progress_state) を題の横の印で見せるので、基本情報と小窓の項目からは外す (作者の決め)。
    #: 取消の理由は、取り消した指示書だけ出す。発行の指示書では入らない欄で、「—」の行は何の欄か迷わせる (作者の決め)。
    #: 取り消した指示書は、理由が空でも行を出す (理由を書かずに取り消せるため)。
    @property
    def detail_hidden_fields(self):
        if self.status == self.STATUS_CANCELLED:
            return ("status",)
        return ("status", "cancel_reason")

    #: 変更の履歴 (core.history。作者の決めの伝票の「指示書」「実績」) を残し、詳細に「履歴」のタブを出す。
    #: 状態 (status) は詳細に出さないが、履歴には残す (取消の時に状態の前と後が残り、題の「取り消した」も状態から決まるため)
    keeps_history = True

    class Meta:
        verbose_name = "指示書"
        verbose_name_plural = "指示書"
        ordering = ["-issued_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="workorders_workorder_qty_positive",
            ),
        ]

    def __str__(self):
        return self.wo_no

    @classmethod
    def progress_state_of(cls, status, steps, totals) -> str:
        """指示書の進みの状態の値 (PROGRESS_LABELS の鍵)。決め方はここの 1 か所 (詳細・一覧・小窓・生産管理ボード・段の帯・生産計画の盤)。

        status は指示書の状態の値、steps は指示書の工程の並び (順は問わない)、totals は工程の pk → StepTotals
        (辞書に無い工程は 0・0・印なし。進捗の部品が無ければ空の辞書)。上から順に最初に当たった物:
        取消 = 指示書が取消 / 完了 = 最後の工程 (順が最大) に完了の印の付いた実績がある (数は見ない。途中の工程の印も見ない。
        生産計画の盤の棒の「完了」= plans.bar_state と同じ) / 着手済 = どれかの工程の良品と不良の合計が 0 より大きい
        (実績が 1 件でもある。良品 0・不良だけの実績も着手) / 未着手 = ほか (工程の無い指示書と、進捗の部品が無い置き場を含む)。着手の印は読まない。
        訳: 状態を記録せずに実績だけから出し、記録した状態と実績が食い違わないようにするため。完了の決め方は会社ごとに違うので、決まりで自動に決めず、人が実績の印で決めるため。
        出所 = 作者の決め (11-07 06:58・07:01) と作者の決め (11-07 00:36)「b / 開始の進捗が必要な場合はまた別途だね」と、作者の決めの答え「そもそもstatusは進捗から計算すればいいのでは？」。着手済を良品と不良の合計で見るのは作者の決め (11-07 07:08)。字の 4 つは作者の決め (11-07 00:2x)。
        変えてよい条件 = 本人が完了の決め方を変えたとき。
        """
        if status == cls.STATUS_CANCELLED:
            return cls.PROGRESS_CANCELLED
        steps = list(steps)
        if steps:
            last = max(steps, key=lambda step: (step.seq, step.pk))
            if totals.get(last.pk, NO_TOTALS).completed:
                return cls.PROGRESS_DONE
        for step in steps:
            step_totals = totals.get(step.pk, NO_TOTALS)
            if step_totals.recorded + step_totals.defect > 0:
                return cls.PROGRESS_STARTED
        return cls.PROGRESS_NOT_STARTED

    def _steps_and_totals(self):
        """(工程の並び, 工程の pk → StepTotals)。読むだけ。

        工程を実績のまとめ (recorded_total・defect_total・completed_mark) 付きで prefetch してあれば (指示書の一覧と生産管理ボード)、
        問い合わせをしない。無ければ工程を同じまとめ付きで 1 回の問い合わせで読む (with_recorded_total)。
        進捗の部品が無い置き場では、0・0・印なし。
        """
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("steps")
        if prefetched is not None and all(has_step_totals(step) for step in prefetched):
            steps = list(prefetched)
        else:
            # 工程の名前 (step_progress) も同じ 1 回で読む (select_related)。
            steps = list(with_recorded_total(self.steps.select_related("process").order_by()))
        return steps, {step.pk: step_totals_of(step) for step in steps}

    def progress_state(self) -> str:
        """進みの状態の値 (未着手・着手済・完了・取消の鍵)。決め方は progress_state_of。取消は工程を読まない。"""
        if self.status == self.STATUS_CANCELLED:
            return self.PROGRESS_CANCELLED
        steps, totals = self._steps_and_totals()
        return self.progress_state_of(self.status, steps, totals)

    def progress_state_label(self) -> str:
        """進みの状態の字 (一覧の「状態」の列と CSV。印は workorders_tags の workorder_badge)。"""
        return self.PROGRESS_LABELS[self.progress_state()]

    def step_progress(self) -> StepProgress:
        """工程ごとの進み (StepProgress = 工程の順の StepProgressRow の並び)。一覧の「工程の進み」の列 (workorders/_row.html の棒) と CSV が使う。読むだけ。

        数は進捗の盤 (progress.boards) と同じ: 予定の数 = planned_qty、実績 = 良品の合計 (recorded_total。不良は数えない)。
        棒の長さ (pct) は 実績 ÷ 予定 を 100% で止めて切り捨てた整数。予定の数に届いた工程は full (一覧は棒と数を緑にする)。
        工程を実績のまとめ付きで prefetch してあれば (指示書の一覧) 問い合わせをしない (_steps_and_totals)。取消の指示書も工程があれば出す
        (取消は「状態」の列の印で分かる)。工程の無い指示書は空の並び。
        訳: 指示書の一覧で、どの工程がどこまで進んだかを行の中で見られるようにするため (本人の決め 11-07 18:2x〜18:4x「4はありだね」)。
        """
        steps, totals = self._steps_and_totals()
        rows = StepProgress()
        for step in sorted(steps, key=lambda step: (step.seq, step.pk)):
            recorded = totals.get(step.pk, NO_TOTALS).recorded
            planned = _dec(step.planned_qty)
            ratio = recorded / planned if planned > 0 else _ZERO
            rows.append(
                StepProgressRow(
                    seq=step.seq,
                    name=step.process.name,
                    planned=planned,
                    recorded=recorded,
                    pct=int(min(ratio, 1) * 100),
                    full=planned > 0 and recorded >= planned,
                )
            )
        return rows

    @property
    def progress_records(self):
        """この指示書の工程に入った進捗の実績 (読むだけ。詳細の「進捗」のタブが使う)。

        progress を import せず、工程の逆の関係 (progress の外部キー) の先の表として引く。
        progress の部品が無いときは AttributeError (土台の hasattr が偽になり、タブを出さない)。
        """
        try:
            records = WorkOrderStep._meta.get_field("progress_records").related_model
        except FieldDoesNotExist:
            raise AttributeError("progress_records") from None
        return records._default_manager.filter(workorder_step__workorder=self)

    @property
    def subcontract_lines(self):
        """この指示書の工程を指す、取り消していない協力企業への発注の明細 (読むだけ。詳細の「協力企業」のタブと工程のタブの行が使う。N406b)。

        purchasing を import せず、工程の逆の関係 (purchasing の外部キー purchase_order_lines) の先の表として引く。取消の発注の明細と、
        取り消した明細 (明細状態コード 2) は入れない。並びは発注番号 → 行番号。行の字が読む発注と取引先と部品を一緒に読む。
        purchasing の部品が無いときは AttributeError (土台の hasattr が偽になり、タブを出さない = progress_records と同じ形)。
        """
        try:
            lines = WorkOrderStep._meta.get_field("purchase_order_lines").related_model
        except FieldDoesNotExist:
            raise AttributeError("subcontract_lines") from None
        from seizou.core import edi_codes

        orders = lines._meta.get_field("purchase_order").related_model
        return (
            lines._default_manager.filter(workorder_step__workorder=self)
            .exclude(line_status_code=edi_codes.LINE_DELETED)
            .exclude(purchase_order__status=orders.STATUS_CANCELLED)
            .select_related("purchase_order__partner", "item")
            .order_by("purchase_order__po_no", "line_no", "pk")
        )


class WorkOrderStep(models.Model):
    """指示書の工程。順は 1 から。予定の数は検査の層が実績の合計と比べる。"""

    workorder = models.ForeignKey(
        WorkOrder,
        verbose_name="指示書",
        on_delete=models.CASCADE,
        related_name="steps",
    )
    process = models.ForeignKey(
        "masters.Process",
        verbose_name="工程",
        on_delete=models.PROTECT,
        related_name="workorder_steps",
    )
    seq = models.PositiveIntegerField("順")
    planned_qty = models.DecimalField("予定の数", max_digits=12, decimal_places=2)
    # 生産計画の盤で置く日。空 = 未計画。書くのは services.plan_step だけ。
    planned_start = models.DateField("開始日", null=True, blank=True)
    planned_end = models.DateField("終了日", null=True, blank=True)

    #: 変更の履歴は指示書の詳細の「履歴」に出す (core.history。作者の決めの伝票の「指示書」)
    history_owner = "workorder"

    class Meta:
        verbose_name = "指示書の工程"
        verbose_name_plural = "指示書の工程"
        ordering = ["workorder", "seq"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(planned_qty__gt=0),
                name="workorders_workorderstep_planned_qty_positive",
            ),
            models.UniqueConstraint(
                fields=["workorder", "seq"],
                name="workorders_workorderstep_seq_unique",
            ),
        ]

    #: 工程 1 つの実績のまとめの型 (StepTotals)。ほかの部品は workorders を import せず、外部キーの先か apps.get_model で
    #: 引いた工程の model から読む (type(step).Totals・step_model.total_expressions()・step.annotated_totals())。
    Totals = StepTotals

    def __str__(self):
        return f"{self.workorder.wo_no} {self.seq} {self.process.name}"

    @classmethod
    def total_expressions(cls) -> dict:
        """工程の queryset に足す 3 つの式 (step_total_expressions)。生産管理ボードと進捗の盤が annotate に使う。"""
        return step_total_expressions()

    def annotated_totals(self):
        """total_expressions の 3 つの annotate がそろっていれば StepTotals、そろっていなければ None。読むだけ。"""
        if not has_step_totals(self):
            return None
        return step_totals_of(self)


def step_total_expressions() -> dict:
    """工程の queryset に足す 3 つの式 {recorded_total, defect_total, completed_mark}。読むだけ。

    recorded_total = 良品の合計・defect_total = 不良の合計 (実績が無ければ 0)・completed_mark = 完了の印の付いた実績があるか。
    progress を import せず、工程の逆の関係 (progress の外部キー) の先の表として引く。進捗の部品が無い置き場では 0・0・偽。
    指示書の状態 (with_recorded_total)・生産計画の盤 (plans)・生産管理ボード (orders.board)・進捗の盤 (progress.boards) が同じ式を使う。
    印は Exists の副問い合わせなので、2 つの Sum と掛け合わない (同じ 1 回の問い合わせで読める)。
    """
    try:
        records = WorkOrderStep._meta.get_field("progress_records").related_model
    except FieldDoesNotExist:
        return {
            "recorded_total": Value(_ZERO, output_field=_QTY_FIELD),
            "defect_total": Value(_ZERO, output_field=_QTY_FIELD),
            "completed_mark": Value(False, output_field=models.BooleanField()),
        }
    return {
        "recorded_total": Coalesce(Sum("progress_records__qty"), Value(_ZERO), output_field=_QTY_FIELD),
        "defect_total": Coalesce(Sum("progress_records__defect_qty"), Value(_ZERO), output_field=_QTY_FIELD),
        "completed_mark": Exists(
            records._default_manager.filter(workorder_step=OuterRef("pk"), completes_step=True)
        ),
    }


def has_step_totals(step) -> bool:
    """工程に step_total_expressions の 3 つの annotate が全部あるか。"""
    return all(hasattr(step, name) for name in ("recorded_total", "defect_total", "completed_mark"))


def step_totals_of(step) -> StepTotals:
    """annotate 済みの工程 1 つから StepTotals を作る (SQLite は集計の値を桁なしや int で返すので Decimal と bool にそろえる)。"""
    return StepTotals(_dec(step.recorded_total), _dec(step.defect_total), bool(step.completed_mark))


def with_recorded_total(steps):
    """指示書の工程の queryset に、工程ごとの良品の合計 (recorded_total)・不良の合計 (defect_total)・完了の印 (completed_mark) を足す。読むだけ。

    式は step_total_expressions。進捗の部品が無い置き場では 0・0・偽。
    WorkOrder.progress_state と、指示書の一覧の prefetch (views の WorkOrderListView) が使う。
    """
    return steps.annotate(**step_total_expressions())
