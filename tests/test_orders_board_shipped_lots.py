"""生産管理ボードの判断は、出荷済みのうち指示書で作った分を引いて比べる (本人の決め 11-13 06:5x。525 回目の本体の直し)。

訳: 判断の「出荷できる」は「完成の数 ≧ 残」、「指示書が要る」は「指示書の数量の合計 < 残」で決めていた。完成の数と指示書の合計は
出荷しても減らないので、分けて作って出荷した後に食い違った (数量 10 の明細に 5 の指示書を出して完成させ、その 5 を出荷すると、
残りの 5 を作る指示書が無いのに「出荷できる」と出た)。出荷済みの数と完成の数の小さい方を、指示書で作って出荷した分として
両方から引く。受注の詳細の緑のボタン (BoardRow.next_action。決め 11-12 19:0x) も同じ式。
作り物の得意先と部品だけを使う。データは services で作る。納期は先の日にして、判断の「遅れ」に当たらないようにする。
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine
from seizou.shipping import services as shipping_services
from tests import test_orders_board
from tests.test_orders_board import _issue, _line, _record

user = test_orders_board.user
customer = test_orders_board.customer
item = test_orders_board.item
cut = test_orders_board.cut


def _ahead():
    return timezone.localdate() + timedelta(days=30)


def _lot(user, line, cut, qty, done=None):  # noqa: F811
    """数量 qty の指示書を 1 つ発行し、done があれば最後の工程にその数の実績を入れる。"""
    workorder = _issue(user, line, cut, qty=qty)
    if done is not None:
        _record(user, workorder.steps.get(seq=1), done, completes=Decimal(done) >= Decimal(qty))
    return workorder


def _ship(user, line, qty):  # noqa: F811
    shipping_services.ship(OrderLine.objects.get(pk=line.pk), Decimal(qty), timezone.localdate(), user)


def _judged(line):
    """引き直した明細の (判断の印の code, 次の操作)。board は cached_property なので、変えた後は引き直す。"""
    row = OrderLine.objects.get(pk=line.pk).board
    return row.judgement_code, row.next_action


@pytest.mark.django_db
def test_shipped_lot_no_longer_counts_as_ready(user, customer, item, cut):  # noqa: F811
    """数量 10 に 5 の指示書を出して完成させ、その 5 を出荷する: 残りの 5 は、作る指示書が無いので「指示書が要る」。"""
    line = _line(user, customer, item, due=_ahead())
    _lot(user, line, cut, "5", done="5")
    assert _judged(line) == ("need_workorder", "issue")  # 出荷の前から、残りの 5 の指示書が要る

    _ship(user, line, "5")

    assert _judged(line) == ("need_workorder", "issue")


@pytest.mark.django_db
def test_finished_and_unshipped_part_is_ready(user, customer, item, cut):  # noqa: F811
    """10 を完成させて 4 を出荷する: 残り 6 は完成していて出せるので「出荷できる」(今までと同じ)。"""
    line = _line(user, customer, item, due=_ahead())
    _lot(user, line, cut, "10", done="10")
    _ship(user, line, "4")

    assert _judged(line) == ("ship", "ship")


@pytest.mark.django_db
def test_rest_still_in_production_is_making(user, customer, item, cut):  # noqa: F811
    """10 の指示書のうち 5 が完成し、その 5 を出荷する: 残り 5 はまだ作っている途中なので「製造中」(出せる完成品は無い)。"""
    line = _line(user, customer, item, due=_ahead())
    _lot(user, line, cut, "10", done="5")
    _ship(user, line, "5")

    assert _judged(line) == ("making", "ship")


@pytest.mark.django_db
def test_shipping_more_than_the_lot_needs_a_workorder(user, customer, item, cut):  # noqa: F811
    """5 の指示書を完成させ、在庫の分も合わせて 8 を出荷する: 残り 2 を作る指示書が無いので「指示書が要る」。"""
    line = _line(user, customer, item, due=_ahead())
    _lot(user, line, cut, "5", done="5")
    _ship(user, line, "8")

    assert _judged(line) == ("need_workorder", "issue")


@pytest.mark.django_db
def test_shipment_before_any_completion_keeps_the_open_workorder(user, customer, item, cut):  # noqa: F811
    """5 の指示書がまだ完成していない間に 6 を出荷する (指示書の外の物を出した): 残り 4 は、出ている指示書 5 で足りるので「製造中」。"""
    line = _line(user, customer, item, due=_ahead())
    _lot(user, line, cut, "5")
    _ship(user, line, "6")

    assert _judged(line) == ("making", "ship")


@pytest.mark.django_db
def test_unshipped_lines_judge_as_before(user, customer, item, cut):  # noqa: F811
    """出荷の無い明細は、今までと同じ判断 (指示書が無い → 指示書が要る / 全部完成 → 出荷できる)。"""
    fresh = _line(user, customer, item, due=_ahead())
    assert _judged(fresh) == ("need_workorder", "issue")

    finished = _line(user, customer, item, due=_ahead())
    _lot(user, finished, cut, "10", done="10")
    assert _judged(finished) == ("ship", "ship")


@pytest.mark.django_db
def test_unissued_is_the_part_no_workorder_covers(user, customer, item, cut):  # noqa: F811
    """残のうち、作る指示書がまだ無い数 (BoardRow.unissued と orders.services.unissued_qty)。指示書の発行の入力の数量の初めの値が読む。

    判断の「指示書が要る」と同じ見方で、今出せる明細・残の全部に指示書が出ている明細・取消の受注は 0。
    """

    def unissued(line):
        fresh = OrderLine.objects.get(pk=line.pk)
        assert orders_services.unissued_qty(fresh) == fresh.board.unissued
        return fresh.board.unissued

    fresh = _line(user, customer, item, due=_ahead())
    assert unissued(fresh) == Decimal("10")

    shipped_lot = _line(user, customer, item, due=_ahead())
    _lot(user, shipped_lot, cut, "5", done="5")
    assert unissued(shipped_lot) == Decimal("5")
    _ship(user, shipped_lot, "5")
    assert unissued(shipped_lot) == Decimal("5")

    in_production = _line(user, customer, item, due=_ahead())
    _lot(user, in_production, cut, "10", done="5")
    _ship(user, in_production, "5")
    assert unissued(in_production) == Decimal("0")

    ready = _line(user, customer, item, due=_ahead())
    _lot(user, ready, cut, "10", done="10")
    _ship(user, ready, "4")
    assert unissued(ready) == Decimal("0")

    over_shipped = _line(user, customer, item, due=_ahead())
    _lot(user, over_shipped, cut, "5", done="5")
    _ship(user, over_shipped, "8")
    assert unissued(over_shipped) == Decimal("2")

    cancelled = _line(user, customer, item, due=_ahead())
    orders_services.cancel_order(cancelled.order, user)
    assert unissued(cancelled) == Decimal("0")

