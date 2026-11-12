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
