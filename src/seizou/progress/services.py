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
- 不良の行 (ProgressDefect。見比べのまとめの M09): 人が打つのは行だけで、実績の不良の数 (defect_qty) はいつも行の数の合計をここで書く
  (合計を別に打たせない)。record_progress は、不良の行の並び (defects) を渡されたら行を作って合計を書き、渡されなければ今のまま defect_qty を使い、
  0 より大きければ区分の無い 1 行を作る。update_progress は、defects を渡されたら行を突き合わせ (pk のある行は直し、無い行は足し、並びに無い行は消す)、
  渡されなければ (既定の KEEP) 今の行の合計と defect_qty が違うときだけ、区分の無い 1 行だけの実績に限ってその行の数を合わせる。
  不良のある実績には、いつも 1 行以上の不良の行がある。行の変更の履歴は、update_progress に defects を渡したときと、原因と対策を書いたとき
  (update_defect_note) と、区分を消すとき (copy_defect_kind_before_delete) だけ行ごとに残す (入れた・合わせた・消した実績の不良の数は、実績の履歴に載る)。
  区分を選んでいない不良のポカよけ (R30) は検査の層が services の前に見る。ここでは止めない。
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from seizou.core import history
from seizou.core.exceptions import RuleViolation
from seizou.core.services import record_history, record_history_deleted

from .models import Progress, ProgressDefect, _qty_text

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


# ---------------------------------------------------------------- 不良の行 (見比べのまとめの M09)

#: 不良の行の 1 行の鍵 (record_progress の defects)。区分 (DefectKind か pk か None)・数・原因
_DEFECT_KEYS = frozenset({"kind", "qty", "cause_note"})
#: update_progress の defects の 1 行の鍵 (今の行を指す pk を足す)
_DEFECT_KEYS_ON_UPDATE = _DEFECT_KEYS | {"pk"}
_BOTH_DEFECTS_MESSAGE = (
    "保存できません。不良の数と不良の行を同時に渡さないでください。不良の数は不良の行の合計で決まります。"
    "不良の行だけを渡してください。"
)
_LINE_QTY_MESSAGE = "保存できません。不良の行の数は 0 より大きい数にします。数を直すか、その行を外してください。"
_SPLIT_DEFECTS_MESSAGE = "保存できません。この実績の不良は区分ごとの行に分けてあります。不良の数は不良の行で直してください。"
_FOREIGN_LINE_MESSAGE = "保存できません。この実績に無い不良の行が含まれています。実績の編集の画面を開き直してから直してください。"
_TWICE_LINE_MESSAGE = "保存できません。同じ不良の行が 2 回含まれています。実績の編集の画面を開き直してから直してください。"


def _defect_lines(defects, allowed):
    """不良の行の並びを確かめ、数を Decimal にした写しの並びを返す (渡された並びは書き換えない)。

    鍵の外の字があれば日本語の ValueError。数の鍵が無いか空なら 0 と見て、0 以下の行は日本語の ValueError
    (先に 2 段目の form が欄の誤りで止める。ここは最後の守り)。
    訳: 実績の不良の数と不良の行の合計が食い違う記録を作らないため (人が打つのは行だけにし、合計は services が書く。合計と行を別々に持つと、どちらが正しいかを言えなくなる)。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M09)。
    変えてよい条件 = 本人が決め直したとき。
    """
    lines = []
    for line in defects:
        unknown = sorted(set(line) - allowed)
        if unknown:
            raise ValueError(
                f"保存できません。不良の行に知らない項目 ({'・'.join(unknown)}) があります。"
                f"不良の行には {'・'.join(sorted(allowed))} だけを渡してください。"
            )
        values = dict(line)
        values["qty"] = _dec(line.get("qty") or 0)
        if not values["qty"] > 0:
            raise ValueError(_LINE_QTY_MESSAGE)
        lines.append(values)
    return lines


def _refuse_both_defects(defect_qty):
    """不良の行 (defects) を渡したのに、不良の数 (defect_qty) も 0 でない数で渡されたら日本語の ValueError。

    呼ぶ側の誤りを黙って片方に寄せないため、どちらも使わずに断る。
    訳: 実績の不良の数と不良の行の合計が食い違う記録を作らないため (人が打つのは行だけにし、合計は services が書く。合計と行を別々に持つと、どちらが正しいかを言えなくなる)。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M09)。
    変えてよい条件 = 本人が決め直したとき。
    """
    if defect_qty != 0:
        raise ValueError(_BOTH_DEFECTS_MESSAGE)


def _kind_pk(kind):
    """区分 (DefectKind か pk か None) の pk。None は None。"""
    return getattr(kind, "pk", kind)


def _total(lines) -> Decimal:
    return sum((line["qty"] for line in lines), Decimal("0"))


def _is_plain(row) -> bool:
    """区分の空の不良の行か (外部キーも写しの名前も空)。"""
    return row.kind_id is None and not row.kind_name


def _keep_defects(progress, defect_qty):
    """update_progress に不良の行を渡さないとき (KEEP)、今の行を defect_qty に合わせる。行の履歴は書かない。

    今の行の合計が defect_qty と同じなら何もしない。違うときは、今の行が 0 行か、区分の空 (外部キーも写しも空) の 1 行だけなら、
    その 1 行の数を defect_qty にする (行が無ければ作り、defect_qty が 0 なら消す)。区分のある行か 2 行以上あるときは日本語の ValueError
    (表の上で合計と行が食い違う記録を作らないための断りで、ポカよけではない)。
    訳: 実績の不良の数と不良の行の合計が食い違う記録を作らないため (人が打つのは行だけにし、合計は services が書く。合計と行を別々に持つと、どちらが正しいかを言えなくなる)。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M09)。
    変えてよい条件 = 本人が決め直したとき。
    """
    rows = list(ProgressDefect.objects.select_for_update().filter(progress_id=progress.pk).order_by("pk"))
    if sum((row.qty for row in rows), Decimal("0")) == defect_qty:
        return
    if len(rows) > 1 or (rows and not _is_plain(rows[0])):
        raise ValueError(_SPLIT_DEFECTS_MESSAGE)
    if not rows:
        ProgressDefect.objects.create(progress=progress, qty=defect_qty)
    elif defect_qty > 0:
        rows[0].qty = defect_qty
        rows[0].save(update_fields=["qty"])
    else:
        rows[0].delete()


def _match_defects(progress, lines, user):
    """update_progress に渡された不良の行の並び (lines) と今の行を突き合わせ、行ごとに変更の履歴を残す。

    pk のある行はその行の区分・数・原因を直す (対策は変えない。"kind" の鍵が無ければ区分も写しも変えず、"cause_note" の鍵が無ければ原因も変えない)。
    "kind" が None なら区分を空にして写しも空にし、区分を指す値なら外部キーを書いて写しを空にする。pk の無い行は足し、並びに無い今の行は消す。
    ほかの実績の行か、無い行の pk か、同じ pk が 2 回あれば、何も書かずに日本語の ValueError。
    履歴は、消した行・直した行と足した行 (並びの順) の順に書く (呼ぶ側は、実績の履歴をこの後に書く)。
    訳: 実績の不良の数と不良の行の合計が食い違う記録を作らないため (人が打つのは行だけにし、合計は services が書く。合計と行を別々に持つと、どちらが正しいかを言えなくなる)。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M09)。
    変えてよい条件 = 本人が決め直したとき。
    """
    existing = {row.pk: row for row in ProgressDefect.objects.select_for_update().filter(progress_id=progress.pk).order_by("pk")}
    keep = set()
    for line in lines:
        if line.get("pk") is None:
            continue
        if line["pk"] not in existing:
            raise ValueError(_FOREIGN_LINE_MESSAGE)
        if line["pk"] in keep:
            raise ValueError(_TWICE_LINE_MESSAGE)
        keep.add(line["pk"])
    # 消す行は消す前に 1 行ずつ「消した」を残す (queryset の delete は行ごとの save を通らないため)
    for pk, row in existing.items():
        if pk not in keep:
            record_history_deleted(row, user)
            row.delete()
    for line in lines:
        if line.get("pk") is None:
            created = ProgressDefect.objects.create(
                progress=progress,
                kind_id=_kind_pk(line.get("kind")),
                qty=line["qty"],
                cause_note=line.get("cause_note") or "",
            )
            record_history(created, user)
            continue
        row = existing[line["pk"]]
        before = history.saved_state(row)
        fields = ["qty"]
        row.qty = line["qty"]
        if "kind" in line:
            row.kind_id = _kind_pk(line["kind"])
            row.kind_name = ""
            fields += ["kind", "kind_name"]
        if "cause_note" in line:
            row.cause_note = line["cause_note"] or ""
            fields.append("cause_note")
        row.save(update_fields=fields)
        record_history(row, user, before=before)


def record_progress(
    workorder_step,
    qty,
    recorded_at,
    user,
    *,
    note="",
    defect_qty=0,
    completes_step=False,
    staff=None,
    staff_name="",
    defects=None,
):
    """指示書の工程に実績 (良品の数) を qty だけ、不良の数を defect_qty だけ記録し、completes_step が真なら工程の完了の印を付ける。

    返す実績に、画面の知らせの字 saved_message (「<指示書の番号> の工程 <順> <工程の名前> に実績 <数> を記録しました。」。
    不良が 0 より大きいときは「実績 <数>・不良 <数>」、作業した人の見せる字が空でないときは「作業した人は <見せる字> です。」、
    印を付けたときはその後ろに「この工程に完了の印を付けました。」) を付ける (表に書かない)。
    印の付いた工程にも記録する (止めない。作者の決め)。
    作業した人: staff (社員) があれば外部キーだけを書き、2 つの写しは空のまま (渡された staff_name は使わない)。使用停止の社員も
    書く (止めるか知らせるかは、検査の層のポカよけ R28 が会社の設定 progress_inactive_staff で決める。本人の決め 11-11 13:1x)。
    staff が無ければ、staff_name の前後の空白を落として名前の写しに書く (社員の一覧に居ない人。空でもよい)。
    不良の行: defects (1 行 = {"kind": 区分か None, "qty": 数, "cause_note": 字}) を渡したら、並びのとおりに不良の行を作り、実績の不良の数は
    行の数の合計にする (defect_qty も 0 でない数で渡されたら日本語の ValueError。行の数が 0 以下でも日本語の ValueError)。
    defects が None なら今のまま defect_qty を使い、0 より大きければ区分の空の行を 1 つ作る (原因は空)。R02 は良品の数と行の合計で見る。
    不良の行を作ったことの変更の履歴は、行ごとには書かない (実績の「作った」の履歴に不良の数が載る)。
    区分を選んでいない不良のポカよけ (R30) は検査の層が services の前に見る。ここでは止めない。
    訳: 良品も不良も無い空の実績と、負の数の実績を記録しないため (全部が不良の回は良品 0 で記録できる)。出所 = RULES.md R02 と作者の決め (11-07 07:08)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    訳: 実績の不良の数と不良の行の合計が食い違う記録を作らないため (人が打つのは行だけにし、合計は services が書く。合計と行を別々に持つと、どちらが正しいかを言えなくなる)。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M09)。
    変えてよい条件 = 本人が決め直したとき。
    """
    qty = _dec(qty)
    defect_qty = _dec(defect_qty)
    if defects is not None:
        _refuse_both_defects(defect_qty)
        lines = _defect_lines(defects, _DEFECT_KEYS)
        defect_qty = _total(lines)
    elif defect_qty > 0:
        lines = [{"kind": None, "qty": defect_qty}]
    else:
        lines = []
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
        for line in lines:
            ProgressDefect.objects.create(
                progress=created,
                kind_id=_kind_pk(line.get("kind")),
                qty=line["qty"],
                cause_note=line.get("cause_note") or "",
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
    progress,
    qty,
    recorded_at,
    user,
    *,
    defect_qty=0,
    completes_step=False,
    note="",
    staff=KEEP,
    staff_name=KEEP,
    defects=KEEP,
):
    """実績の良品の数・不良の数・完了の印・日時・備考と作業した人を直す。工程 (workorder_step) と人 (recorded_by) は変えない (作者の決め)。

    間違えて付けた完了の印も、ここで外す。在庫は動かさない (実績は在庫を動かさない。入庫は stock の「製造の完了を入庫にする」)。
    返す実績に、画面の知らせの字 saved_message (「<指示書の番号> の工程 <順> <工程の名前> の実績を直しました。」) を付ける (表に書かない)。
    予定を超えた実績の知らせは検査の層 (R07 の OverProgressOnUpdate) が services の前に見る。ここでは止めない (現物優先)。
    作業した人: staff を渡さなければ (既定の KEEP) 変えない。staff が社員なら、使用停止の社員も外部キーを書き、2 つの写しを空にする
    (止めるか知らせるかは、検査の層のポカよけ R28 が会社の設定 progress_inactive_staff で決める。本人の決め 11-11 13:1x)。staff が None なら外部キーを空にし、
    staff_name の字を名前の写しに書き (staff_name が KEEP なら今の名前の写しのまま)、番号の写しを空にする。
    不良の行: defects を渡さなければ (既定の KEEP。今の実績の編集の画面)、今の行の合計が defect_qty と同じなら行を変えない。違うときは、今の行が 0 行か
    区分の空 (外部キーも写しも空) の 1 行だけなら、その 1 行の数を defect_qty に合わせ (無ければ作り、defect_qty が 0 なら消す)、行の履歴は書かない
    (実績の「直した」の履歴に不良の数の前後が載る)。区分のある行か 2 行以上あるときは日本語の ValueError。
    defects (1 行 = {"pk": 今の行の pk (足す行は無し), "kind": 区分か None, "qty": 数, "cause_note": 字}) を渡したら、行を突き合わせて
    (pk のある行は区分・数・原因を直し、対策は変えない。pk の無い行は足し、並びに無い今の行は消す)、実績の不良の数は行の合計にする。
    pk のある行で "kind" の鍵が無ければ区分も写しも変えない。"kind" が None なら区分と写しを空にし、区分を指す値なら外部キーを書いて写しを空にする。
    "cause_note" の鍵が無ければ原因も変えない。defect_qty も 0 でない数で渡されたら日本語の ValueError (record_progress と同じ)。
    このときだけ、行を足した・直した・消した事を行ごとに変更の履歴に残し (人 = 直した人)、実績の履歴を最後に書く
    (指示書の詳細の履歴の一番新しい行が実績の行になるように)。行は実績より先に書く (行の履歴の前と後で、行の「実績」の字をそろえるため)。
    訳: 良品も不良も無い空の実績と、負の数の実績にしないため (直すときも入れるときと同じ R02 を当てる)。出所 = RULES.md R02 と作者の決め (11-07 07:08。作者の決め 11-07 07:04)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    訳: 実績の不良の数と不良の行の合計が食い違う記録を作らないため (人が打つのは行だけにし、合計は services が書く。合計と行を別々に持つと、どちらが正しいかを言えなくなる)。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M09)。
    変えてよい条件 = 本人が決め直したとき。
    """
    qty = _dec(qty)
    defect_qty = _dec(defect_qty)
    lines = None
    if defects is not KEEP:
        _refuse_both_defects(defect_qty)
        lines = _defect_lines(defects, _DEFECT_KEYS_ON_UPDATE)
        defect_qty = _total(lines)
    with transaction.atomic():
        step = _WorkOrderStep.objects.select_for_update().get(pk=progress.workorder_step_id)
        locked = Progress.objects.select_for_update().get(pk=progress.pk)
        _check_r02(qty, defect_qty)
        if lines is None:
            _keep_defects(locked, defect_qty)
        else:
            _match_defects(locked, lines, user)
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
    不良の行は表の CASCADE で一緒に消える。行の履歴は書かない (実績の「消した」の履歴に不良の数が載る)。
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


def update_defect_note(defect, cause_note, countermeasure, user):
    """不良の行の原因と対策を書く (区分と数は変えない)。2 段目の「原因と対策を書く」画面が呼ぶ。既に無ければ日本語の ValueError。

    行は transaction.atomic の中で select_for_update で取り直す。変更の履歴を user で残す (指示書の詳細の「履歴」に出る)。
    返す行に、画面の知らせの字 saved_message (「<指示書の番号> の工程 <順> <工程の名前> の不良 (<見せる字> <数>) の原因と対策を保存しました。」)
    を付ける (表に書かない)。
    """
    with transaction.atomic():
        row = ProgressDefect.objects.select_for_update().filter(pk=defect.pk).first()
        if row is None:
            raise ValueError("保存できません。この不良の行は既に消してあります。")
        before = history.saved_state(row)
        row.cause_note = cause_note or ""
        row.countermeasure = countermeasure or ""
        row.save(update_fields=["cause_note", "countermeasure"])
        record_history(row, user, before=before)
        row.saved_message = f"{_step_text(row.progress.workorder_step)} の不良 ({row}) の原因と対策を保存しました。"
        return row


def copy_defect_kind_before_delete(kind, user):
    """不良の区分を消す前に、その区分を指す不良の行の全部へ、消す時の区分の名前を写して外部キーを空にする。写した件数を返す。

    呼ぶのは masters.services.delete_defect_kind だけで、区分を消す事と同じ transaction.atomic の中で呼ぶ。
    行は select_for_update で取り、1 行ずつ、書く前の値を core.history.saved_state で取ってから保存し、変更の履歴を
    record_history(行, user, before=…) で残す (人 = 区分を消した人。指示書の詳細の「履歴」に出る)。QuerySet.update でまとめて書かない。
    保存していない区分は 0 (kind_id=None で引くと、区分の無い行の全部に当たるため)。
    訳: 区分を消しても、不良の行に何の不良だったかを残すため (品質の問合せに、後から区分の名前を答えられるように)。消す時の名前を写してから区分を指す欄を空にし、区分を消す事と同じ処理の中で行う。表の外部キーは PROTECT なので、この関数を通らずに区分を消す道は表が止める。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M09) と、社員を消すときの形 (本人の決め 11-11 09:0x) をそろえた物。
    変えてよい条件 = 本人が決め直したとき。
    """
    copied = 0
    if getattr(kind, "pk", None) is None:
        return copied
    with transaction.atomic():
        for row in ProgressDefect.objects.select_for_update().filter(kind_id=kind.pk).order_by("pk"):
            before = history.saved_state(row)
            row.kind_name = kind.name
            row.kind = None
            row.save(update_fields=["kind", "kind_name"])
            record_history(row, user, before=before)
            copied += 1
    return copied


def count_by_defect_kind(kind) -> int:
    """その不良の区分を指す不良の行の件数 (読むだけ。masters.services.defect_kind_line_count が呼ぶ)。保存していない区分は 0。"""
    if getattr(kind, "pk", None) is None:
        return 0
    return ProgressDefect.objects.filter(kind_id=kind.pk).count()


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
