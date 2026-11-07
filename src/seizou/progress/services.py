"""progress の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して実績を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は実績の「人」(recorded_by) に書く。無ければ空。
- 中心の決まりに当たったら RuleViolation(code, message)。message は RULES.md の番号の文と、次にすること。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 予定より多い実績のポカよけは検査の層 (seizou.core.checks) が services の前に見る。
  ここでは止めず、予定を超えた実数もそのまま記録する (現物優先)。
- 工程ごとの実績の合計は recorded_qty で出す (検査の層と同じ関数)。
  不良の数の合計は recorded_defect_qty、工程の完了の印の有る無しは step_completed。
  書く前に、指示書の工程の行を select_for_update でロックする (01 の 10.1)。
- 指示書 (WorkOrder・WorkOrderStep) は書かない。
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from seizou.core.exceptions import RuleViolation

from .models import Progress, _qty_text

_R02_MESSAGE = "保存できません。実績の数と不良の数は 0 以上で、合わせて 0 より大きい数にします。数を直してください。"
#: 完了の印を付けて記録したときに、知らせの字の後ろに足す 1 文
_COMPLETED_NOTE = "この工程に完了の印を付けました。"

# 指示書の工程の表は、実績の外部キーの先として引く (workorders の models を import しない)。
_WorkOrderStep = Progress._meta.get_field("workorder_step").related_model


def _dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _check_r02(qty, defect_qty):
    """実績の形を見る。良品の数 (qty) と不良の数 (defect_qty) は、どちらも 0 以上で、合わせて 0 より大きい。

    record_progress と update_progress が同じ確かめを使う (直すときも入れるときと同じ R02 を当てる)。
    訳: 良品も不良も無い空の実績と、負の数の実績を記録しないため (全部が不良の回は良品 0 で記録できる)。出所 = RULES.md R02 と作者の決め (C103-Q2 = a。11-07 07:08)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    if qty < 0 or defect_qty < 0 or not (qty + defect_qty) > 0:
        raise RuleViolation("R02", _R02_MESSAGE)


def _step_text(step) -> str:
    """知らせの字の工程の所 (「<指示書の番号> の工程 <順> <工程の名前>」)。"""
    return f"{step.workorder.wo_no} の工程 {step.seq} {step.process.name}"


def record_progress(workorder_step, qty, recorded_at, user, *, note="", defect_qty=0, completes_step=False):
    """指示書の工程に実績 (良品の数) を qty だけ、不良の数を defect_qty だけ記録し、completes_step が真なら工程の完了の印を付ける。

    返す実績に、画面の知らせの字 saved_message (「<指示書の番号> の工程 <順> <工程の名前> に実績 <数> を記録しました。」。
    不良が 0 より大きいときは「実績 <数>・不良 <数>」、印を付けたときは後ろに「この工程に完了の印を付けました。」) を付ける (表に書かない)。
    印の付いた工程にも記録する (止めない。PROG-1c = a)。
    訳: 良品も不良も無い空の実績と、負の数の実績を記録しないため (全部が不良の回は良品 0 で記録できる)。出所 = RULES.md R02 と作者の決め (C103-Q2 = a。11-07 07:08)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    qty = _dec(qty)
    defect_qty = _dec(defect_qty)
    with transaction.atomic():
        locked = _WorkOrderStep.objects.select_for_update().get(pk=workorder_step.pk)
        _check_r02(qty, defect_qty)
        created = Progress.objects.create(
            workorder_step=locked,
            qty=qty,
            defect_qty=defect_qty,
            completes_step=bool(completes_step),
            recorded_at=recorded_at,
            recorded_by=user if getattr(user, "pk", None) is not None else None,
            note=note,
        )
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
        # 何をどれだけ記録したかを書く (C44-1 = a)。不良は 0 より大きいときだけ、印は付けたときだけ足す。
        defect_text = f"・不良 {_qty_text(defect_qty)}" if defect_qty > 0 else ""
        created.saved_message = f"{_step_text(locked)} に実績 {_qty_text(qty)}{defect_text} を記録しました。" + (
            _COMPLETED_NOTE if created.completes_step else ""
        )
        return created


def update_progress(progress, qty, recorded_at, user, *, defect_qty=0, completes_step=False, note=""):
    """実績の良品の数・不良の数・完了の印・日時・備考を直す。工程 (workorder_step) と人 (recorded_by) は変えない (PROG-1d = b)。

    間違えて付けた完了の印も、ここで外す。在庫は動かさない (実績は在庫を動かさない。入庫は stock の「製造の完了を入庫にする」)。
    返す実績に、画面の知らせの字 saved_message (「<指示書の番号> の工程 <順> <工程の名前> の実績を直しました。」) を付ける (表に書かない)。
    予定を超えた実績の知らせは検査の層 (R07 の OverProgressOnUpdate) が services の前に見る。ここでは止めない (現物優先)。
    訳: 良品も不良も無い空の実績と、負の数の実績にしないため (直すときも入れるときと同じ R02 を当てる)。出所 = RULES.md R02 と作者の決め (C103-Q2 = a。11-07 07:08。PROG-1d = b。11-07 07:04)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    qty = _dec(qty)
    defect_qty = _dec(defect_qty)
    with transaction.atomic():
        step = _WorkOrderStep.objects.select_for_update().get(pk=progress.workorder_step_id)
        locked = Progress.objects.select_for_update().get(pk=progress.pk)
        _check_r02(qty, defect_qty)
        locked.qty = qty
        locked.defect_qty = defect_qty
        locked.completes_step = bool(completes_step)
        locked.recorded_at = recorded_at
        locked.note = note
        locked.save(update_fields=["qty", "defect_qty", "completes_step", "recorded_at", "note"])
        locked.saved_message = f"{_step_text(step)} の実績を直しました。"
        return locked


def delete_progress(progress, user):
    """実績を消す (PROG-1e = a。別の工程や二重に入れた実績を消す)。既に無ければ日本語の ValueError。

    行は atomic の中で取り直して消す。在庫は動かさない (実績は在庫を動かさない)。user は受けるだけ (記録の列が無い)。
    返す物 (消した実績。pk は None) に、画面の知らせの字 saved_message (「<指示書の番号> の工程 <順> <工程の名前> の実績 <数> を削除しました。」) を付ける。
    """
    with transaction.atomic():
        row = Progress.objects.select_for_update().filter(pk=progress.pk).first()
        if row is None:
            raise ValueError("削除できません。この実績は既に消してあります。")
        text = f"{_step_text(row.workorder_step)} の実績 {_qty_text(row.qty)} を削除しました。"
        row.delete()
        row.saved_message = text
        return row


def recorded_qty(workorder_step):
    """指示書の工程に対する実績の合計 (列に持たない)。"""
    return Progress.objects.filter(workorder_step=workorder_step).aggregate(
        total=Coalesce(
            Sum("qty"),
            Value(Decimal("0")),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    )["total"]


def recorded_defect_qty(workorder_step):
    """指示書の工程に対する不良の数の合計 (列に持たない。recorded_qty と同じ形)。"""
    return Progress.objects.filter(workorder_step=workorder_step).aggregate(
        total=Coalesce(
            Sum("defect_qty"),
            Value(Decimal("0")),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    )["total"]


def step_completed(workorder_step) -> bool:
    """指示書の工程に、完了の印の付いた実績が 1 件でもあるか (PROG-1。数は見ない)。"""
    return Progress.objects.filter(workorder_step=workorder_step, completes_step=True).exists()
