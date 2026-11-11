"""実績の不良を区分ごとの行 (progress の ProgressDefect) で持つ (見比べのまとめの M09)。

人が打つのは行だけで、実績の不良の数 (Progress.defect_qty) は services がいつも行の数の合計を書く。
不良のある実績には、いつも 1 行以上の不良の行がある (不良の数だけを渡された実績には、区分の無い 1 行を services が作る)。
不良の区分を消すときは、その区分を指す行の全部に消す時の名前を写してから外部キーを空にし、区分を消す事と同じ
transaction.atomic の中で行う (masters.services.delete_defect_kind → progress.services.copy_defect_kind_before_delete)。
作り物のデータは全部 services で作る (工程の行だけは R07 の試験と同じく表に直に作る。migration の関数の試験は実績も表に直に作る)。
"""

import importlib
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import ProtectedError, Sum
from django.test.utils import CaptureQueriesContext

from seizou.core.exceptions import RuleViolation
from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.masters.models import DefectKind, Process
from seizou.orders import services as orders_services
from seizou.progress import services
from seizou.progress.models import Progress, ProgressDefect
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
MIGRATION = "seizou.progress.migrations.0005_defect_lines_from_progress"
BOTH_HEAD = "保存できません。不良の数と不良の行を同時に渡さないでください。"
LINE_QTY_HEAD = "保存できません。不良の行の数は 0 より大きい数にします。"
SPLIT = "保存できません。この実績の不良は区分ごとの行に分けてあります。不良の数は不良の行で直してください。"
LINE_MODEL = "progress.progressdefect"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def fixer(db):
    return get_user_model().objects.create_user("fixer", password="pass-for-tests-only")


@pytest.fixture
def step(user):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    process = Process.objects.create(code="CUT", name="切断")
    workorder = workorders_services.issue_workorder(
        order.lines.get(), Decimal("10"), [{"process": process, "planned_qty": Decimal("10")}], user
    )
    return workorder.steps.get()


@pytest.fixture
def scratch(user):
    return masters_services.create_defect_kind({"name": "傷"}, user)


@pytest.fixture
def size(user):
    return masters_services.create_defect_kind({"name": "寸法"}, user)


def _record(step, user, qty="1", **kwargs):
    return services.record_progress(step, Decimal(qty), RECORDED_AT, user, **kwargs)


def _update(progress, user, qty="1", **kwargs):
    return services.update_progress(progress, Decimal(qty), RECORDED_AT, user, **kwargs)


def _lines(progress):
    return list(ProgressDefect.objects.filter(progress_id=progress.pk).order_by("pk"))


def _values(progress):
    """不良の行の (区分の pk, 写しの名前, 数, 原因, 対策) の並び。"""
    return [(line.kind_id, line.kind_name, line.qty, line.cause_note, line.countermeasure) for line in _lines(progress)]


def _defect_qty(progress):
    return Progress.objects.get(pk=progress.pk).defect_qty


def _step_text(step):
    return f"{step.workorder.wo_no} の工程 {step.seq} {step.process.name}"


def _line_logs():
    return list(ChangeLog.objects.filter(model=LINE_MODEL).order_by("pk"))


def _newest(step):
    """指示書の詳細の「履歴」の一番新しい行。"""
    return ChangeLog.objects.filter(owner_model="workorders.workorder", owner_pk=step.workorder_id).first()


def _assert_totals_match():
    lines = ProgressDefect.objects.aggregate(total=Sum("qty"))["total"] or Decimal("0")
    progress = Progress.objects.aggregate(total=Sum("defect_qty"))["total"] or Decimal("0")
    assert lines == progress


# ---------------------------------------------------------------- 入れる


def test_record_with_defects_makes_lines_in_order(user, step, scratch, size):
    progress = _record(
        step,
        user,
        defects=[{"kind": scratch, "qty": Decimal("3"), "cause_note": "刃の摩耗"}, {"kind": size, "qty": Decimal("2")}],
    )
    assert _defect_qty(progress) == Decimal("5")
    assert _values(progress) == [
        (scratch.pk, "", Decimal("3"), "刃の摩耗", ""),
        (size.pk, "", Decimal("2"), "", ""),
    ]
    assert progress.saved_message == f"{_step_text(step)} に実績 1・不良 5 を記録しました。"


def test_record_with_line_without_kind(user, step, scratch):
    progress = _record(step, user, defects=[{"kind": None, "qty": Decimal("2.5")}, {"kind": scratch, "qty": Decimal("1")}])
    plain, scratched = _lines(progress)
    assert (plain.kind_id, plain.kind_name) == (None, "")
    assert plain.get_kind_name_display() == "区分なし"
    assert str(plain) == "区分なし 2.5"
    assert scratched.get_kind_name_display() == "傷"
    assert str(scratched) == "傷 1"
    assert _defect_qty(progress) == Decimal("3.5")


def test_record_with_defect_qty_makes_one_plain_line(user, step):
    progress = _record(step, user, defect_qty=Decimal("4"))
    assert _values(progress) == [(None, "", Decimal("4"), "", "")]
    assert _defect_qty(progress) == Decimal("4")
    zero = _record(step, user)
    assert _values(zero) == []


def test_record_refuses_both_defect_qty_and_defects(user, step, scratch):
    with pytest.raises(ValueError) as caught:
        _record(step, user, defect_qty=Decimal("2"), defects=[{"kind": scratch, "qty": Decimal("2")}])
    assert str(caught.value).startswith(BOTH_HEAD)
    assert not Progress.objects.exists()
    assert not ProgressDefect.objects.exists()


@pytest.mark.parametrize("qty", ["0", "-1"])
def test_record_refuses_line_qty_not_positive(user, step, scratch, qty):
    with pytest.raises(ValueError) as caught:
        _record(step, user, defects=[{"kind": scratch, "qty": Decimal("2")}, {"kind": None, "qty": Decimal(qty)}])
    assert str(caught.value).startswith(LINE_QTY_HEAD)
    assert not Progress.objects.exists()
    assert not ProgressDefect.objects.exists()


def test_record_refuses_unknown_line_key(user, step):
    with pytest.raises(ValueError) as caught:
        _record(step, user, defects=[{"kind": None, "qty": Decimal("1"), "countermeasure": "作り物の対策"}])
    assert str(caught.value).startswith("保存できません。")
    assert "countermeasure" in str(caught.value)
    assert not Progress.objects.exists()


def test_record_defects_only_follows_r02(user, step, scratch):
    """良品 0 で行の合計が 0 より大きい実績は通る。良品 0 で行が無ければ R02。"""
    progress = _record(step, user, qty="0", defects=[{"kind": scratch, "qty": Decimal("3")}])
    stored = Progress.objects.get(pk=progress.pk)
    assert (stored.qty, stored.defect_qty) == (Decimal("0"), Decimal("3"))
    with pytest.raises(RuleViolation) as caught:
        _record(step, user, qty="0", defects=[])
    assert caught.value.code == "R02"
    assert Progress.objects.count() == 1


def test_record_writes_no_line_history(user, step, scratch, size):
    progress = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("3")}, {"kind": size, "qty": Decimal("2")}])
    assert _line_logs() == []
    newest = _newest(step)
    assert (newest.model, newest.action, newest.object_pk) == ("progress.progress", ChangeLog.ACTION_CREATED, progress.pk)
    assert {"field": "defect_qty", "label": "不良の数", "type": "number", "before": "", "after": "5"} in newest.changes


# ---------------------------------------------------------------- 直す (不良の行を渡さない。今の実績の編集の画面)


def test_update_without_defects_same_total_keeps_lines(user, fixer, step, scratch, size):
    progress = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("3")}, {"kind": size, "qty": Decimal("2")}])
    services.update_defect_note(_lines(progress)[0], "刃の摩耗", "刃を替えた", user)
    before = _values(progress)
    assert before[0][4] == "刃を替えた"
    count = len(_line_logs())

    _update(progress, fixer, qty="2", defect_qty=Decimal("5"))

    assert _values(progress) == before  # 行も対策もそのまま
    stored = Progress.objects.get(pk=progress.pk)
    assert (stored.qty, stored.defect_qty) == (Decimal("2"), Decimal("5"))
    assert len(_line_logs()) == count


def test_update_without_defects_resizes_single_plain_line(user, step):
    progress = _record(step, user, defect_qty=Decimal("4"))
    line = _lines(progress)[0]
    services.update_defect_note(line, "作り物の原因", "作り物の対策", user)

    _update(progress, user, defect_qty=Decimal("6"))
    assert _values(progress) == [(None, "", Decimal("6"), "作り物の原因", "作り物の対策")]
    assert _lines(progress)[0].pk == line.pk
    assert _defect_qty(progress) == Decimal("6")

    _update(progress, user, defect_qty=Decimal("0"))
    assert _values(progress) == []
    assert _defect_qty(progress) == Decimal("0")

    # 行の無い実績の不良の数を 0 より大きくすると、区分の無い 1 行を作る
    _update(progress, user, defect_qty=Decimal("2"))
    assert _values(progress) == [(None, "", Decimal("2"), "", "")]


def test_update_without_defects_refuses_split_lines(user, step, scratch):
    kinded = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("3")}])
    with pytest.raises(ValueError) as caught:
        _update(kinded, user, qty="2", defect_qty=Decimal("4"))
    assert str(caught.value) == SPLIT
    stored = Progress.objects.get(pk=kinded.pk)
    assert (stored.qty, stored.defect_qty) == (Decimal("1"), Decimal("3"))
    assert _values(kinded) == [(scratch.pk, "", Decimal("3"), "", "")]

    # 区分の無い行でも 2 行あれば、どちらを直すか決められないので断る
    two = _record(step, user, defects=[{"kind": None, "qty": Decimal("1")}, {"kind": None, "qty": Decimal("2")}])
    with pytest.raises(ValueError) as caught:
        _update(two, user, defect_qty=Decimal("4"))
    assert str(caught.value) == SPLIT
    assert _defect_qty(two) == Decimal("3")


def test_update_without_defects_resize_writes_no_line_history(user, step):
    progress = _record(step, user, defect_qty=Decimal("4"))
    _update(progress, user, defect_qty=Decimal("6"))
    assert _line_logs() == []
    newest = _newest(step)
    assert (newest.model, newest.action, newest.object_pk) == ("progress.progress", ChangeLog.ACTION_UPDATED, progress.pk)
    assert {"field": "defect_qty", "label": "不良の数", "type": "number", "before": "4", "after": "6"} in newest.changes


# ---------------------------------------------------------------- 直す (不良の行を渡す。2 段目の実績の編集の画面)


def test_update_with_defects_matches_lines(user, fixer, step, scratch, size):
    progress = _record(
        step, user, defects=[{"kind": scratch, "qty": Decimal("3"), "cause_note": "前の原因"}, {"kind": None, "qty": Decimal("2")}]
    )
    first, second = _lines(progress)
    services.update_defect_note(first, "前の原因", "前の対策", user)

    result = _update(
        progress,
        fixer,
        defects=[
            {"pk": first.pk, "kind": size, "qty": Decimal("4"), "cause_note": "直した原因"},
            {"kind": scratch, "qty": Decimal("1")},
        ],
    )

    lines = _lines(progress)
    assert [line.pk for line in lines][0] == first.pk
    assert not ProgressDefect.objects.filter(pk=second.pk).exists()  # 並びに無い行は消える
    assert _values(progress) == [
        (size.pk, "", Decimal("4"), "直した原因", "前の対策"),  # 対策は変えない
        (scratch.pk, "", Decimal("1"), "", ""),  # pk の無い行は足す
    ]
    assert _defect_qty(progress) == Decimal("5")
    assert result.saved_message == f"{_step_text(step)} の実績を直しました。"


def test_update_with_defects_writes_line_history_then_progress(user, fixer, step, scratch, size):
    progress = _record(
        step,
        user,
        defects=[{"kind": scratch, "qty": Decimal("3")}, {"kind": size, "qty": Decimal("2")}, {"kind": None, "qty": Decimal("1")}],
    )
    keep, change, drop = _lines(progress)
    count = ChangeLog.objects.count()

    _update(
        progress,
        fixer,
        qty="2",  # 良品の数も直す (直さない行に履歴を書かないことを見る)
        defects=[
            {"pk": keep.pk, "kind": scratch, "qty": Decimal("3")},
            {"pk": change.pk, "kind": size, "qty": Decimal("5")},
            {"kind": None, "qty": Decimal("2")},
        ],
    )

    added = _lines(progress)[-1]
    rows = list(ChangeLog.objects.order_by("pk"))[count:]
    assert [(row.model, row.action, row.object_pk) for row in rows] == [
        (LINE_MODEL, ChangeLog.ACTION_DELETED, drop.pk),
        (LINE_MODEL, ChangeLog.ACTION_UPDATED, change.pk),
        (LINE_MODEL, ChangeLog.ACTION_CREATED, added.pk),
        ("progress.progress", ChangeLog.ACTION_UPDATED, progress.pk),
    ]
    assert {row.actor for row in rows} == {fixer}
    assert {(row.owner_model, row.owner_pk) for row in rows} == {("workorders.workorder", step.workorder_id)}
    assert [(item["field"], item["before"], item["after"]) for item in rows[1].changes] == [("qty", "2", "5")]
    # 指示書の詳細の履歴の一番新しい行は、今と同じく実績の行
    newest = _newest(step)
    assert (newest.model, newest.object_pk) == ("progress.progress", progress.pk)
    assert _defect_qty(progress) == Decimal("10")


def test_update_with_defects_refuses_other_progress_line(user, step, scratch):
    mine = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("1")}])
    other = _record(step, user, defects=[{"kind": None, "qty": Decimal("2")}])
    other_line = _lines(other)[0]
    count = ChangeLog.objects.count()
    with pytest.raises(ValueError) as caught:
        _update(mine, user, defects=[{"pk": other_line.pk, "kind": scratch, "qty": Decimal("2")}])
    assert str(caught.value).startswith("保存できません。")
    assert _values(mine) == [(scratch.pk, "", Decimal("1"), "", "")]
    assert _values(other) == [(None, "", Decimal("2"), "", "")]
    assert _defect_qty(mine) == Decimal("1")
    assert ChangeLog.objects.count() == count


def test_update_refuses_both_defect_qty_and_defects(user, step, scratch):
    progress = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("1")}])
    line = _lines(progress)[0]
    with pytest.raises(ValueError) as caught:
        _update(progress, user, defect_qty=Decimal("3"), defects=[{"pk": line.pk, "kind": scratch, "qty": Decimal("3")}])
    assert str(caught.value).startswith(BOTH_HEAD)
    assert _values(progress) == [(scratch.pk, "", Decimal("1"), "", "")]


def test_update_with_defects_refuses_line_qty_not_positive(user, step, scratch):
    progress = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("1")}])
    line = _lines(progress)[0]
    with pytest.raises(ValueError) as caught:
        _update(progress, user, defects=[{"pk": line.pk, "kind": scratch, "qty": Decimal("0")}])
    assert str(caught.value).startswith(LINE_QTY_HEAD)
    assert _values(progress) == [(scratch.pk, "", Decimal("1"), "", "")]


# ---------------------------------------------------------------- 消す


def test_delete_progress_removes_lines_without_line_history(user, step, scratch):
    progress = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("2")}, {"kind": None, "qty": Decimal("1")}])
    services.delete_progress(progress, user)
    assert not ProgressDefect.objects.exists()
    assert _line_logs() == []
    newest = _newest(step)
    assert (newest.model, newest.action, newest.object_pk) == ("progress.progress", ChangeLog.ACTION_DELETED, progress.pk)


# ---------------------------------------------------------------- 原因と対策


def test_update_defect_note(user, fixer, step, scratch):
    progress = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("3"), "cause_note": "初めの原因"}])
    line = _lines(progress)[0]

    result = services.update_defect_note(line, "刃の摩耗", "刃を替えた", fixer)

    assert _values(progress) == [(scratch.pk, "", Decimal("3"), "刃の摩耗", "刃を替えた")]
    assert _defect_qty(progress) == Decimal("3")
    assert result.saved_message == f"{_step_text(step)} の不良 (傷 3) の原因と対策を保存しました。"
    logs = _line_logs()
    assert [(row.action, row.actor, row.object_pk) for row in logs] == [(ChangeLog.ACTION_UPDATED, fixer, line.pk)]
    assert (logs[0].owner_model, logs[0].owner_pk) == ("workorders.workorder", step.workorder_id)
    assert [item["field"] for item in logs[0].changes] == ["cause_note", "countermeasure"]


def test_update_defect_note_reads_only_the_defect_table(user, fixer, step, scratch):
    """原因と対策を書くときの行の取り直しは、親の実績の表を引かない (本体の直し。N407 の記録の要望 4)。

    Meta の並び (実績・pk) のままだと実績の表と結ぶ問い合わせになり、PostgreSQL では実績の行までロックして、
    update_progress (指示書の工程 → 実績 → 不良の行) とロックの順が食い違う。
    """
    progress = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("3")}])
    line = _lines(progress)[0]

    with CaptureQueriesContext(connection) as queries:
        services.update_defect_note(line, "刃の摩耗", "刃を替えた", fixer)

    table = ProgressDefect._meta.db_table
    selects = [q["sql"] for q in queries if q["sql"].lstrip().upper().startswith("SELECT") and f'FROM "{table}"' in q["sql"]]
    assert selects, [q["sql"] for q in queries]
    assert "JOIN" not in selects[0].upper(), selects[0]


# ---------------------------------------------------------------- 区分を消す


def test_delete_kind_copies_name_to_lines(user, fixer, step, scratch, size):
    first = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("2")}, {"kind": size, "qty": Decimal("1")}])
    second = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("4")}])
    masters_services.update_defect_kind(scratch, {"name": "傷 (直した)"}, user)
    before = {line.pk: line.get_kind_name_display() for line in ProgressDefect.objects.filter(kind=scratch)}
    assert set(before.values()) == {"傷 (直した)"}
    assert masters_services.defect_kind_line_count(scratch) == 2

    deleted = masters_services.delete_defect_kind(scratch, fixer)

    assert deleted.pk is None
    assert deleted.saved_message == "不良の区分「傷 (直した)」を削除しました。不良の行 2 件に、区分の名前を写して残しました。"
    assert not DefectKind.objects.filter(name="傷 (直した)").exists()
    for pk, text in before.items():
        line = ProgressDefect.objects.get(pk=pk)
        assert (line.kind_id, line.kind_name) == (None, "傷 (直した)")
        assert line.get_kind_name_display() == text
    # 別の区分の行はそのまま
    assert _values(first)[1] == (size.pk, "", Decimal("1"), "", "")
    # 写した 2 行それぞれに、区分を消した人の「直した」の履歴が残る (指示書の詳細の「履歴」に出る)
    for line_pk in before:
        rows = [row for row in _line_logs() if row.object_pk == line_pk]
        assert [(row.action, row.actor) for row in rows] == [(ChangeLog.ACTION_UPDATED, fixer)]
        assert rows[0].owner_model == "workorders.workorder"
    assert _defect_qty(first) == Decimal("3") and _defect_qty(second) == Decimal("4")


def test_delete_kind_rolls_back_when_it_stops(user, fixer, step, scratch, monkeypatch):
    _record(step, user, defects=[{"kind": scratch, "qty": Decimal("2")}])
    _record(step, user, defects=[{"kind": scratch, "qty": Decimal("1")}])

    def _stop(obj, user, **kwargs):
        raise RuntimeError("作り物の止まり")

    monkeypatch.setattr(masters_services, "record_history_deleted", _stop)
    with pytest.raises(RuntimeError):
        masters_services.delete_defect_kind(scratch, fixer)

    assert DefectKind.objects.filter(pk=scratch.pk).exists()
    for line in ProgressDefect.objects.all():
        assert (line.kind_id, line.kind_name) == (scratch.pk, "")
    assert _line_logs() == []


def test_delete_kind_without_services_is_protected(user, step, scratch):
    progress = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("2")}])
    with pytest.raises(ProtectedError):
        DefectKind.objects.get(pk=scratch.pk).delete()
    assert _values(progress) == [(scratch.pk, "", Decimal("2"), "", "")]


def test_copied_line_keeps_or_clears_copy_on_update(user, step, scratch, size):
    """区分を消した後の写しだけの行は、"kind" の鍵無しで渡すと写しが残り、"kind": None で渡すと写しが空になる。"""
    progress = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("2")}, {"kind": scratch, "qty": Decimal("1")}])
    masters_services.delete_defect_kind(scratch, user)
    kept, cleared = _lines(progress)

    _update(progress, user, defects=[{"pk": kept.pk, "qty": Decimal("3")}, {"pk": cleared.pk, "kind": None, "qty": Decimal("1")}])

    assert _values(progress) == [(None, "傷", Decimal("3"), "", ""), (None, "", Decimal("1"), "", "")]
    kept, cleared = _lines(progress)
    assert kept.get_kind_name_display() == "傷"
    assert cleared.get_kind_name_display() == "区分なし"
    assert _defect_qty(progress) == Decimal("4")

    # 区分を選び直すと外部キーを書き、写しを空にする
    _update(progress, user, defects=[{"pk": kept.pk, "kind": size, "qty": Decimal("3")}, {"pk": cleared.pk, "qty": Decimal("1")}])
    assert _values(progress) == [(size.pk, "", Decimal("3"), "", ""), (None, "", Decimal("1"), "", "")]


def test_copied_line_is_not_resized_without_defects(user, step, scratch):
    """写しだけの 1 行は区分の空の行ではないので、不良の数だけの直しで数を変えると断る (合計が同じなら通る)。"""
    progress = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("2")}])
    masters_services.delete_defect_kind(scratch, user)
    with pytest.raises(ValueError) as caught:
        _update(progress, user, defect_qty=Decimal("3"))
    assert str(caught.value) == SPLIT
    _update(progress, user, qty="3", defect_qty=Decimal("2"))
    assert _values(progress) == [(None, "傷", Decimal("2"), "", "")]


def test_count_by_defect_kind(user, step, scratch, size):
    _record(step, user, defects=[{"kind": scratch, "qty": Decimal("2")}, {"kind": size, "qty": Decimal("1")}])
    _record(step, user, defects=[{"kind": scratch, "qty": Decimal("1")}, {"kind": None, "qty": Decimal("1")}])
    assert services.count_by_defect_kind(scratch) == 2
    assert services.count_by_defect_kind(size) == 1
    assert services.count_by_defect_kind(DefectKind(name="作り物の保存していない区分")) == 0
    assert masters_services.defect_kind_line_count(scratch) == 2
    assert services.copy_defect_kind_before_delete(DefectKind(name="作り物の保存していない区分"), user) == 0


# ---------------------------------------------------------------- 合計がそろう事


def test_line_totals_match_progress_totals(user, step, scratch, size):
    mixed = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("3")}, {"kind": None, "qty": Decimal("2")}])
    plain = _record(step, user, defect_qty=Decimal("4"))
    good = _record(step, user, qty="2")
    small = _record(step, user, defects=[{"kind": size, "qty": Decimal("1.5")}])
    _assert_totals_match()

    first = _lines(mixed)[0]
    _update(mixed, user, defects=[{"pk": first.pk, "kind": size, "qty": Decimal("6")}, {"kind": scratch, "qty": Decimal("0.5")}])
    _assert_totals_match()
    _update(plain, user, defect_qty=Decimal("7"))
    _assert_totals_match()
    _update(good, user, qty="2", defect_qty=Decimal("2"))
    _assert_totals_match()
    services.delete_progress(small, user)
    _assert_totals_match()
    _update(plain, user, defect_qty=Decimal("0"))
    _assert_totals_match()
    masters_services.delete_defect_kind(scratch, user)
    _assert_totals_match()
    assert ProgressDefect.objects.aggregate(total=Sum("qty"))["total"] == Decimal("8.5")


# ---------------------------------------------------------------- 今ある実績に行を作る migration の関数


def test_make_lines_migration_function(user, step, scratch):
    """migration の executor で前後を回す形は、試験の DB が transaction の中にあり組みにくいので、関数を django.apps.apps で直に呼ぶ。"""
    module = importlib.import_module(MIGRATION)
    bare = Progress.objects.create(workorder_step=step, qty=Decimal("0"), defect_qty=Decimal("7"), recorded_at=RECORDED_AT)
    good_only = Progress.objects.create(workorder_step=step, qty=Decimal("1"), defect_qty=Decimal("0"), recorded_at=RECORDED_AT)
    lined = _record(step, user, defects=[{"kind": scratch, "qty": Decimal("2")}])

    module.make_lines(apps, None)

    assert _values(bare) == [(None, "", Decimal("7"), "", "")]
    assert _values(good_only) == []
    assert _values(lined) == [(scratch.pk, "", Decimal("2"), "", "")]
    assert _line_logs() == []  # 変更の履歴は書かない

    module.make_lines(apps, None)  # もう 1 回呼んでも増えない
    assert ProgressDefect.objects.count() == 2
    _assert_totals_match()
