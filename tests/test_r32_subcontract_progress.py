"""R32: 指示書の工程を指す発注の明細の受入は、同じ処理の中で、受け入れた数を良品の数にした実績をその工程に作る (中心の決まり)。

受入の後に明細が終わっていれば (R25)、その実績に工程の完了の印を付ける。この受入は在庫の入庫にしない。
指示書 (工程 3 つ・2 つ目のめっきが協力企業に出す工程) の 2 つ目の工程を指す明細 (数量 10・渡した日あり) で見る。
受入を伴わない「完了にする」は実績を作らず、工程に完了の印が無ければ済んだ知らせに 1 文を足す (見比べのまとめの M01)。
作り物のデータは services で作る (tests/test_purchasing_subcontract.py の build_workorder)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.progress import services as progress_services
from seizou.progress.models import Progress
from seizou.purchasing import services
from seizou.purchasing.models import Receipt
from seizou.stock.models import StockMove
from seizou.workorders import services as workorders_services
from tests.rules_helpers import rules_md_text
from tests.test_purchasing_subcontract import ORDER_DATE, build_workorder, line_of, subcontract_po

DAY = date(2026, 11, 5)
STEP_NOT_COMPLETED_NOTE = (
    "指示書の工程の完了の印は変えていません。工程を終えるときは、指示書の詳細の進捗のタブで実績に完了の印を付けてください。"
)


@pytest.fixture
def wo(user):
    return build_workorder(user)


def _records(step):
    return list(Progress.objects.filter(workorder_step=step).order_by("pk"))


def _step_text(wo):
    return f"{wo.workorder.wo_no} の工程 2 めっき"


def test_r32_row_in_rules_md():
    text = rules_md_text("R32")
    assert "指示書の工程を指す発注の明細の受入" in text
    assert "在庫の入庫にしない" in text


# ---------------------------------------------------------------- (a) (b) 受け入れた数で実績を作り、終わった受入で印を付ける


@pytest.mark.django_db
def test_r32_receipt_records_progress_without_stock(user, wo):
    """(a) 4 を受け入れると、その工程の実績が 1 件でき (良品 4・不良 0・作業した人は空)、受入が実績を指す。印は付かず、在庫は動かない。"""
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    moves = StockMove.objects.count()
    assert wo.workorder.progress_state() == wo.workorder.PROGRESS_NOT_STARTED

    result = services.receive(line, Decimal("4"), DAY, user)

    (record,) = _records(wo.step)
    assert (record.qty, record.defect_qty, record.completes_step) == (Decimal("4"), Decimal("0"), False)
    assert record.staff is None and record.staff_name == ""
    assert record.recorded_by == user
    assert record.note == f"協力企業 {wo.subcontractor.name} からの受入 ({po.po_no}-1)"
    assert timezone.localtime(record.recorded_at).date() == DAY
    assert record.defects.count() == 0
    assert Receipt.objects.get(pk=result.pk).progress == record
    assert StockMove.objects.count() == moves
    assert not Progress.objects.filter(workorder_step__in=[wo.steps[0], wo.steps[2]]).exists()
    assert result.saved_message == f"受入を保存しました。{_step_text(wo)} に実績 4 を記録しました。"
    assert not hasattr(result, "saved_level")
    assert wo.workorder.progress_state() == wo.workorder.PROGRESS_STARTED


@pytest.mark.django_db
def test_r32_receipt_that_finishes_line_marks_step(user, wo):
    """(b) 続けて 6 を受け入れると、2 件目の実績に工程の完了の印が付く。工程の実績の合計 = 受入の合計 = 10。"""
    _po, line = subcontract_po(user, wo.subcontractor, wo.step)
    services.receive(line, Decimal("4"), DAY, user)
    result = services.receive(line, Decimal("6"), DAY, user)

    first, second = _records(wo.step)
    assert (first.completes_step, second.completes_step) == (False, True)
    assert second.qty == Decimal("6")
    assert progress_services.recorded_qty(wo.step) == services.received_qty(line) == Decimal("10")
    assert progress_services.step_completed(wo.step)
    assert result.saved_message == (
        f"受入を保存しました。{_step_text(wo)} に実績 6 を記録しました。この工程に完了の印を付けました。"
    )
    # 途中の工程の印では指示書は完了にならない (今の決まりのまま。最後の工程の印で完了)
    assert wo.workorder.progress_state() == wo.workorder.PROGRESS_STARTED
    progress_services.record_progress(wo.steps[2], Decimal("10"), timezone.now(), user, completes_step=True)
    assert wo.workorder.progress_state() == wo.workorder.PROGRESS_DONE


@pytest.mark.django_db
def test_r32_close_rest_receipt_marks_once(user, wo):
    """(c) 7 を「残りは受け入れずに完了にする」で受け入れると、実績は 1 件で印が付き、明細は完了になる。印の無い旨の 1 文は出ない。"""
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    result = services.receive(line, Decimal("7"), DAY, user, close_rest=True)

    (record,) = _records(wo.step)
    assert (record.qty, record.completes_step) == (Decimal("7"), True)
    assert line_of(line.pk).completed is True
    assert Receipt.objects.get(pk=result.pk).progress == record
    assert result.saved_message.startswith(
        f"受入を保存しました。{_step_text(wo)} に実績 7 を記録しました。この工程に完了の印を付けました。{po.po_no}-1 を完了にしました。"
    )
    assert STEP_NOT_COMPLETED_NOTE not in result.saved_message


@pytest.mark.django_db
def test_r32_close_rest_that_reaches_qty_marks_once(user, wo):
    """(c) 数量に届く受入に「残りは受け入れずに完了にする」を付けても、実績は 1 件・印は 1 回 (完了の印は書かない)。"""
    _po, line = subcontract_po(user, wo.subcontractor, wo.step)
    result = services.receive(line, Decimal("10"), DAY, user, close_rest=True)
    (record,) = _records(wo.step)
    assert record.completes_step is True
    assert line_of(line.pk).completed is False
    assert result.saved_message == (
        f"受入を保存しました。{_step_text(wo)} に実績 10 を記録しました。この工程に完了の印を付けました。"
    )


# ---------------------------------------------------------------- (d) (e) 指示書の工程の無い明細と取消の発注は今のまま


@pytest.mark.django_db
def test_r32_plain_lines_do_not_record_progress(user, wo):
    """(d) 指示書の工程の無い明細の受入は実績を作らない。工程の無い明細は入庫、工程だけの明細は入庫しない (今のまま)。"""
    po = services.create_purchase_order(
        wo.subcontractor,
        [
            {"item": wo.item, "qty": Decimal("5"), "unit_price": Decimal("30")},
            {"item": wo.item, "qty": Decimal("5"), "unit_price": Decimal("30"), "process": wo.processes[1]},
        ],
        user,
        order_date=ORDER_DATE,
    )
    plain, with_process = po.lines.order_by("line_no")
    moves = StockMove.objects.count()

    first = services.receive(plain, Decimal("5"), DAY, user)
    assert StockMove.objects.count() == moves + 1
    second = services.receive(with_process, Decimal("5"), DAY, user)
    assert StockMove.objects.count() == moves + 1

    assert not Progress.objects.exists()
    assert Receipt.objects.get(pk=first.pk).progress is None
    assert Receipt.objects.get(pk=second.pk).progress is None
    assert not hasattr(first, "saved_message") and not hasattr(second, "saved_message")


@pytest.mark.django_db
def test_r32_cancelled_order_refuses_receipt_and_progress(user, wo):
    """(e) 取消の発注の受入は今のまま断り、実績も残らない。"""
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    services.cancel_purchase_order(po, user)
    with pytest.raises(ValueError, match="取り消した発注です"):
        services.receive(line, Decimal("4"), DAY, user)
    assert not Receipt.objects.exists()
    assert not Progress.objects.exists()


@pytest.mark.django_db
def test_r32_receipt_on_cancelled_workorder_or_marked_step_still_records(user, wo):
    """取消の指示書の工程と、工程の完了の印の付いた工程への受入は断らず、実績も作る (現物優先)。"""
    _po, line = subcontract_po(user, wo.subcontractor, wo.step)
    progress_services.record_progress(wo.step, Decimal("1"), timezone.now(), user, completes_step=True)
    services.receive(line, Decimal("2"), DAY, user)
    assert len(_records(wo.step)) == 2

    other = build_workorder(user, code="2")
    _po, other_line = subcontract_po(user, other.subcontractor, other.step)
    workorders_services.cancel_workorder(other.workorder, user)
    receipt = services.receive(other_line, Decimal("3"), DAY, user)
    (record,) = _records(other.step)
    assert Receipt.objects.get(pk=receipt.pk).progress == record


# ---------------------------------------------------------------- (f) 実績を消しても受入は残る


@pytest.mark.django_db
def test_r32_deleting_progress_keeps_receipt(user, wo):
    """(f) 受入から作った実績を消すと、受入は残り、受入の実績は空になる。"""
    _po, line = subcontract_po(user, wo.subcontractor, wo.step)
    receipt = services.receive(line, Decimal("4"), DAY, user)
    record = Receipt.objects.get(pk=receipt.pk).progress
    progress_services.delete_progress(record, user)
    kept = Receipt.objects.get(pk=receipt.pk)
    assert kept.progress is None
    assert kept.qty == Decimal("4")


# ---------------------------------------------------------------- (g) 受入を伴わない「完了にする」


@pytest.mark.django_db
def test_r32_mark_completed_does_not_record_progress(user, wo):
    """(g) 受入を伴わない「完了にする」は実績を作らず、工程に完了の印が無ければ済んだ知らせに 1 文が出る。"""
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    services.receive(line, Decimal("4"), DAY, user)
    count = Progress.objects.count()
    result = services.mark_completed(line, user)
    assert Progress.objects.count() == count
    assert not progress_services.step_completed(wo.step)
    assert result.saved_message.startswith(f"{po.po_no}-1 を完了にしました。")
    assert result.saved_message.endswith(STEP_NOT_COMPLETED_NOTE)

    cleared = services.clear_completed(line, user)
    assert Progress.objects.count() == count
    assert STEP_NOT_COMPLETED_NOTE not in cleared.saved_message


@pytest.mark.django_db
def test_r32_mark_completed_note_only_without_mark(user, wo):
    """(g) 工程に完了の印が付いていれば 1 文は出ない。指示書の工程を指さない明細にも出ない。"""
    _po, line = subcontract_po(user, wo.subcontractor, wo.step)
    progress_services.record_progress(wo.step, Decimal("1"), timezone.now(), user, completes_step=True)
    assert STEP_NOT_COMPLETED_NOTE not in services.mark_completed(line, user).saved_message

    plain = services.create_purchase_order(
        wo.subcontractor, [{"item": wo.item, "qty": Decimal("5"), "unit_price": Decimal("30")}], user, order_date=ORDER_DATE
    ).lines.get()
    assert STEP_NOT_COMPLETED_NOTE not in services.mark_completed(plain, user).saved_message


@pytest.mark.django_db
def test_r32_receipt_with_staff_company_keeps_staff_empty(user, wo):
    """社員の一覧に社員が居る会社でも、受入から作る実績の作業した人は空 (社員も名前も渡さない)。"""
    masters_services.create_staff({"code": "0012", "name": "作り物の太郎"}, user)
    _po, line = subcontract_po(user, wo.subcontractor, wo.step)
    services.receive(line, Decimal("4"), DAY, user)
    (record,) = _records(wo.step)
    assert record.staff is None and record.staff_name == "" and record.staff_code == ""
