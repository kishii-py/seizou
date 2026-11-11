"""progress の公開する関数 (部品の公開する関数の設計の決め)。画面とほかの部品は、この関数だけを通して実績を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (services の書き方の設計の決め)。
- user は実績の「人」(recorded_by) に書く。無ければ空。
- 実績を入れた・直した・消した事は、変更の履歴 (core.services.record_history。作者の決めの伝票の「実績」) にも user で残す。
  書く前に core.history.saved_state で前を取り、書いた後に同じ transaction.atomic の中で残す。履歴は指示書の詳細の「履歴」に出る
  (Progress.history_owner)。実績の人 (recorded_by) は入れた人のまま変わらないので、直した人と消した人は変更の履歴の人だけに残る。
- 中心の決まりに当たったら RuleViolation(code, message)。message は RULES.md の番号の文と、次にすること。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 予定より多い実績のポカよけは検査の層 (seizou.core.checks) が services の前に見る。
  ここでは止めず、予定を超えた実数もそのまま記録する (現物優先)。
- 工程ごとの実績の合計は recorded_qty で出す (検査の層と同じ関数)。
  不良の数の合計は recorded_defect_qty、工程の完了の印の有る無しは step_completed。
  書く前に、指示書の工程の行を select_for_update でロックする (services の書き方の設計の決め)。
- 指示書 (WorkOrder・WorkOrderStep) は書かない。
- 作業した人 (本人の決め 11-11 09:0x): record_progress と update_progress は、社員 (staff) を渡されたら外部キーだけを書き、名前の写し (staff_name) と
  番号の写し (staff_code) は空にする。使用停止の社員も書く (止めるか知らせるかは、検査の層のポカよけ R28 が会社の設定 progress_inactive_staff で決める。
  本人の決め 11-11 13:1x)。社員が無ければ、渡された名前
  (社員の一覧に居ない人) を名前の写しにだけ書く。update_progress の 2 つの引数の既定は KEEP (作業した人を変えない)。
  写しに社員の名前と番号を書くのは、社員を消すときの copy_staff_before_delete だけ (masters.services.delete_staff が同じ atomic の中で呼ぶ)。
  作業した人の無い実績のポカよけ (R26) は検査の層が services の前に見る。ここでは止めない。
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from seizou.core import history
from seizou.core.exceptions import RuleViolation
from seizou.core.services import record_history, record_history_deleted

from .models import Progress, _qty_text

_R02_MESSAGE = "保存できません。実績の数と不良の数は 0 以上で、合わせて 0 より大きい数にします。数を直してください。"
#: 完了の印を付けて記録したときに、知らせの字の後ろに足す 1 文
_COMPLETED_NOTE = "この工程に完了の印を付けました。"

# 指示書の工程の表は、実績の外部キーの先として引く (workorders の models を import しない)。
_WorkOrderStep = Progress._meta.get_field("workorder_step").related_model

#: update_progress の staff・staff_name の既定 (作業した人を変えない)。今の実績の編集の form は 2 つを渡さないので、編集で作業した人を消さないため
KEEP = object()
#: 名前の写しの上限 (表の staff_name の字数)
_STAFF_NAME_MAX = Progress._meta.get_field("staff_name").max_length


def _staff_name_text(staff_name) -> str:
    """名前の写しに書く字 (前後の空白を落とす)。上限の字数を超えたら日本語の ValueError (先に form が止める。ここは最後の守り)。"""
    text = str(staff_name or "").strip()
    if len(text) > _STAFF_NAME_MAX:
        raise ValueError(f"保存できません。作業した人の名前が {_STAFF_NAME_MAX} 字を超えています。{_STAFF_NAME_MAX} 字までにしてください。")
    return text


def _dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _check_r02(qty, defect_qty):
    """実績の形を見る。良品の数 (qty) と不良の数 (defect_qty) は、どちらも 0 以上で、合わせて 0 より大きい。

    record_progress と update_progress が同じ確かめを使う (直すときも入れるときと同じ R02 を当てる)。
    訳: 良品も不良も無い空の実績と、負の数の実績を記録しないため (全部が不良の回は良品 0 で記録できる)。出所 = RULES.md R02 と作者の決め (11-07 07:08)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    if qty < 0 or defect_qty < 0 or not (qty + defect_qty) > 0:
        raise RuleViolation("R02", _R02_MESSAGE)


def _step_text(step) -> str:
    """知らせの字の工程の所 (「<指示書の番号> の工程 <順> <工程の名前>」)。"""
    return f"{step.workorder.wo_no} の工程 {step.seq} {step.process.name}"


def record_progress(
    workorder_step, qty, recorded_at, user, *, note="", defect_qty=0, completes_step=False, staff=None, staff_name=""
):
    """指示書の工程に実績 (良品の数) を qty だけ、不良の数を defect_qty だけ記録し、completes_step が真なら工程の完了の印を付ける。

    返す実績に、画面の知らせの字 saved_message (「<指示書の番号> の工程 <順> <工程の名前> に実績 <数> を記録しました。」。
    不良が 0 より大きいときは「実績 <数>・不良 <数>」、作業した人の見せる字が空でないときは「作業した人は <見せる字> です。」、
    印を付けたときはその後ろに「この工程に完了の印を付けました。」) を付ける (表に書かない)。
    印の付いた工程にも記録する (止めない。作者の決め)。
    作業した人: staff (社員) があれば外部キーだけを書き、2 つの写しは空のまま (渡された staff_name は使わない)。使用停止の社員も
    書く (止めるか知らせるかは、検査の層のポカよけ R28 が会社の設定 progress_inactive_staff で決める。本人の決め 11-11 13:1x)。
    staff が無ければ、staff_name の前後の空白を落として名前の写しに書く (社員の一覧に居ない人。空でもよい)。
    訳: 良品も不良も無い空の実績と、負の数の実績を記録しないため (全部が不良の回は良品 0 で記録できる)。出所 = RULES.md R02 と作者の決め (11-07 07:08)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    qty = _dec(qty)
    defect_qty = _dec(defect_qty)
    with transaction.atomic():
        locked = _WorkOrderStep.objects.select_for_update().get(pk=workorder_step.pk)
        _check_r02(qty, defect_qty)
        worker_name = "" if staff is not None else _staff_name_text(staff_name)
        created = Progress.objects.create(
            workorder_step=locked,
            qty=qty,
            defect_qty=defect_qty,
            completes_step=bool(completes_step),
            recorded_at=recorded_at,
            recorded_by=user if getattr(user, "pk", None) is not None else None,
            note=note,
            staff=staff,
            staff_name=worker_name,
        )
        record_history(created, user)
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
        # 何をどれだけ記録したかを書く (作者の決め)。不良は 0 より大きいときだけ、作業した人は見せる字があるときだけ、印は付けたときだけ足す。
        defect_text = f"・不良 {_qty_text(defect_qty)}" if defect_qty > 0 else ""
        worker = created.get_staff_name_display()
        worker_text = f"作業した人は {worker} です。" if worker else ""
        created.saved_message = (
            f"{_step_text(locked)} に実績 {_qty_text(qty)}{defect_text} を記録しました。"
            + worker_text
            + (_COMPLETED_NOTE if created.completes_step else "")
        )
        return created


def update_progress(
    progress, qty, recorded_at, user, *, defect_qty=0, completes_step=False, note="", staff=KEEP, staff_name=KEEP
):
    """実績の良品の数・不良の数・完了の印・日時・備考と作業した人を直す。工程 (workorder_step) と人 (recorded_by) は変えない (作者の決め)。

    間違えて付けた完了の印も、ここで外す。在庫は動かさない (実績は在庫を動かさない。入庫は stock の「製造の完了を入庫にする」)。
    返す実績に、画面の知らせの字 saved_message (「<指示書の番号> の工程 <順> <工程の名前> の実績を直しました。」) を付ける (表に書かない)。
    予定を超えた実績の知らせは検査の層 (R07 の OverProgressOnUpdate) が services の前に見る。ここでは止めない (現物優先)。
    作業した人: staff を渡さなければ (既定の KEEP) 変えない。staff が社員なら、使用停止の社員も外部キーを書き、2 つの写しを空にする
    (止めるか知らせるかは、検査の層のポカよけ R28 が会社の設定 progress_inactive_staff で決める。本人の決め 11-11 13:1x)。staff が None なら外部キーを空にし、
    staff_name の字を名前の写しに書き (staff_name が KEEP なら今の名前の写しのまま)、番号の写しを空にする。
    訳: 良品も不良も無い空の実績と、負の数の実績にしないため (直すときも入れるときと同じ R02 を当てる)。出所 = RULES.md R02 と作者の決め (11-07 07:08。作者の決め 11-07 07:04)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    qty = _dec(qty)
    defect_qty = _dec(defect_qty)
    with transaction.atomic():
        step = _WorkOrderStep.objects.select_for_update().get(pk=progress.workorder_step_id)
        locked = Progress.objects.select_for_update().get(pk=progress.pk)
        _check_r02(qty, defect_qty)
        fields = ["qty", "defect_qty", "completes_step", "recorded_at", "note"]
        if staff is not KEEP:
            if staff is not None:
                worker_name = ""
            else:
                worker_name = locked.staff_name if staff_name is KEEP else _staff_name_text(staff_name)
            fields += ["staff", "staff_name", "staff_code"]
        before = history.saved_state(locked)
        locked.qty = qty
        locked.defect_qty = defect_qty
        locked.completes_step = bool(completes_step)
        locked.recorded_at = recorded_at
        locked.note = note
        if staff is not KEEP:
            locked.staff = staff
            locked.staff_name = worker_name
            locked.staff_code = ""
        locked.save(update_fields=fields)
        record_history(locked, user, before=before)
        locked.saved_message = f"{_step_text(step)} の実績を直しました。"
        return locked


def delete_progress(progress, user):
    """実績を消す (作者の決め。別の工程や二重に入れた実績を消す)。既に無ければ日本語の ValueError。

    行は atomic の中で取り直して消す。在庫は動かさない (実績は在庫を動かさない)。消す前に、その時の値を変更の履歴に「消した」で user で残す。
    返す物 (消した実績。pk は None) に、画面の知らせの字 saved_message (「<指示書の番号> の工程 <順> <工程の名前> の実績 <数> を削除しました。」) を付ける。
    """
    with transaction.atomic():
        row = Progress.objects.select_for_update().filter(pk=progress.pk).first()
        if row is None:
            raise ValueError("削除できません。この実績は既に消してあります。")
        text = f"{_step_text(row.workorder_step)} の実績 {_qty_text(row.qty)} を削除しました。"
        record_history_deleted(row, user)
        row.delete()
        row.saved_message = text
        return row


def copy_staff_before_delete(staff, user):
    """社員を消す前に、その社員を指す実績の全部へ、消す時の名前と番号を写して外部キーを空にする。写した件数を返す。

    呼ぶのは masters.services.delete_staff だけで、社員を消す事と同じ transaction.atomic の中で呼ぶ。
    実績は select_for_update で取り、1 行ずつ、書く前の値を core.history.saved_state で取ってから保存し、変更の履歴を
    record_history(行, user, before=…) で残す (人 = 社員を消した人。指示書の詳細の「履歴」に出る)。QuerySet.update でまとめて書かない。
    実績の編集の権限は見ない (社員を消す権限の中で書く)。
    訳: 社員を消しても、実績に誰が作ったかを残すため (品質の問合せに、後から作った人を答えられるように)。消す時の名前と番号を写してから社員を指す欄を空にし、社員を消す事と同じ処理の中で行う。表の外部キーは PROTECT なので、この関数を通らずに社員を消す道は表が止め、写しの無いまま欄だけが空になる道は無い。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M02。社員を消すときに写す形は G03) と本人の決め 11-11 09:0x。
    変えてよい条件 = 本人が決め直したとき。
    """
    copied = 0
    if getattr(staff, "pk", None) is None:
        # 保存していない社員を指す実績は無い (staff_id=None で引くと、作業した人の無い実績の全部に当たる)
        return copied
    with transaction.atomic():
        for row in Progress.objects.select_for_update().filter(staff_id=staff.pk).order_by("pk"):
            before = history.saved_state(row)
            row.staff_name = staff.name
            row.staff_code = staff.code
            row.staff = None
            row.save(update_fields=["staff", "staff_name", "staff_code"])
            record_history(row, user, before=before)
            copied += 1
    return copied


def count_by_staff(staff) -> int:
    """その社員を指す実績の件数 (読むだけ。masters.services.staff_progress_count が呼ぶ)。保存していない社員は 0。"""
    if getattr(staff, "pk", None) is None:
        return 0
    return Progress.objects.filter(staff_id=staff.pk).count()


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
    """指示書の工程に、完了の印の付いた実績が 1 件でもあるか (作者の決め。数は見ない)。"""
    return Progress.objects.filter(workorder_step=workorder_step, completes_step=True).exists()
