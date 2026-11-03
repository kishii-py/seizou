"""在庫の services (完成品の在庫。在庫数の集計・製造の完了・払い出し・棚卸しの調整・完成の数)。

受注・出荷・指示書・実績・発注・受入は services で作る。日は固定の値を渡す。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.masters.models import Item
from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine
from seizou.progress import services as progress_services
from seizou.purchasing import services as purchasing_services
from seizou.shipping import services as shipping_services
from seizou.stock import services
from seizou.stock.models import KIND_ADJUSTED, KIND_FINISHED, KIND_ISSUED, KIND_RECEIVED, StockMove
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
DAY1 = date(2026, 11, 3)
DAY2 = date(2026, 11, 4)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "rounding": "round"}, user
    )


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user),
    ]


@pytest.fixture
def processes(user):
    return [
        masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user),
        masters_services.create_process({"code": "BEND", "name": "曲げ", "sort_order": 2}, user),
    ]


@pytest.fixture
def no_http(monkeypatch):
    """HTTP を呼んだら落とす (つながりの無い協力企業の発注は送らない)。"""

    def fail(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


def _line(user, customer, item, qty="10"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _issue(user, line, processes, qty="10"):
    return workorders_services.issue_workorder(
        line, Decimal(qty), [{"process": p, "planned_qty": Decimal(qty)} for p in processes], user
    )


def _record(user, step, qty):
    return progress_services.record_progress(step, Decimal(qty), timezone.now(), user)


def _finished(user, customer, item, processes, qty):
    """部品に指示書を発行し、製造の完了を qty だけ入庫にする。"""
    workorder = _issue(user, _line(user, customer, item), processes[:1])
    return services.record_finished(workorder, Decimal(qty), user, moved_on=DAY1)


@pytest.mark.django_db
def test_balance_is_none_without_moves(user, customer, items):
    plain, shipped_only = items
    line = _line(user, customer, shipped_only)
    shipping_services.ship(line, Decimal("4"), ORDER_DATE, user)

    assert services.balance(plain) is None
    assert services.balance(shipped_only) is None  # 出荷だけでは在庫を数えていない
    assert services.balances([plain, shipped_only]) == {plain.pk: None, shipped_only.pk: None}
    assert services.balances([plain.pk]) == {plain.pk: None}
    assert services.balances([]) == {}


@pytest.mark.django_db
def test_finished_and_issued_sum_with_sign(user, customer, items, processes, django_assert_max_num_queries):
    first, second = items
    _finished(user, customer, first, processes, "10")
    assert services.balance(first) == Decimal("10")

    move = services.record_issued(first, Decimal("3"), DAY2, user, note="検査用")
    assert move.kind == KIND_ISSUED
    assert move.qty == Decimal("-3")
    assert move.moved_by == user
    assert services.balance(first) == Decimal("7")

    with django_assert_max_num_queries(2):
        result = services.balances([first, second])
    assert result == {first.pk: Decimal("7"), second.pk: None}


@pytest.mark.django_db
def test_shipments_are_subtracted_from_balance(user, customer, items, processes):
    item = items[0]
    workorder = _issue(user, _line(user, customer, item), processes[:1])
    services.record_finished(workorder, Decimal("10"), user, moved_on=DAY1)

    shipping_services.ship(workorder.order_line, Decimal("4"), ORDER_DATE, user)
    assert services.balance(item) == Decimal("6")
    assert StockMove.objects.count() == 1  # 出荷は動きの表に書かない

    # 取消の受注の出荷も引く (起きた事)
    other = _line(user, customer, item, qty="5")
    shipping_services.ship(other, Decimal("2"), ORDER_DATE, user)
    orders_services.cancel_order(other.order, user)
    assert services.balance(item) == Decimal("4")
    assert StockMove.objects.count() == 1


@pytest.mark.django_db
def test_negative_balance_is_allowed(user, customer, items, processes):
    item = items[0]
    _finished(user, customer, item, processes, "2")

    services.record_issued(item, Decimal("5"), DAY2, user)

    assert services.balance(item) == Decimal("-3")


@pytest.mark.django_db
def test_adjust_records_difference(user, customer, items, processes):
    item, fresh = items
    _finished(user, customer, item, processes, "10")
    services.record_issued(item, Decimal("3"), DAY2, user)
    assert services.balance(item) == Decimal("7")

    down = services.adjust(item, Decimal("5"), DAY2, user, note="棚卸し")
    assert down.kind == KIND_ADJUSTED and down.qty == Decimal("-2")
    assert services.balance(item) == Decimal("5")

    up = services.adjust(item, Decimal("9"), DAY2, user)
    assert up.qty == Decimal("4")
    assert services.balance(item) == Decimal("9")

    start = services.adjust(fresh, Decimal("12"), DAY2, user)  # 動きの無い部品はこの 1 件で数え始める
    assert start.qty == Decimal("12")
    assert services.balance(fresh) == Decimal("12")

    count = StockMove.objects.count()
    with pytest.raises(ValueError, match="調整は要りません"):
        services.adjust(item, Decimal("9"), DAY2, user)
    with pytest.raises(ValueError, match="0 以上"):
        services.adjust(item, Decimal("-1"), DAY2, user)
    assert StockMove.objects.count() == count


@pytest.mark.django_db
def test_record_finished_refuses_cancelled_and_zero(user, customer, items, processes):
    item = items[0]
    cancelled = _issue(user, _line(user, customer, item), processes[:1])
    workorders_services.cancel_workorder(cancelled, user, reason="作り直し")
    with pytest.raises(ValueError, match="取り消した指示書"):
        services.record_finished(cancelled, Decimal("1"), user)

    workorder = _issue(user, _line(user, customer, item), processes[:1])
    with pytest.raises(ValueError, match="0 より大きい"):
        services.record_finished(workorder, Decimal("0"), user)
    assert StockMove.objects.count() == 0

    # 完成の数 (実績 0) より多くても通る (現物優先)
    move = services.record_finished(workorder, Decimal("3"), user)
    assert move.kind == KIND_FINISHED
    assert move.moved_on == timezone.localdate()
    assert services.balance(item) == Decimal("3")


@pytest.mark.django_db
def test_finished_qty_and_stocked_qty(user, customer, items, processes):
    item = items[0]
    workorder = _issue(user, _line(user, customer, item), processes)
    first, second = workorder.steps.order_by("seq")
    _record(user, first, "10")
    _record(user, second, "6")

    assert services.finished_qty(workorder) == Decimal("6")  # 最後の工程だけ
    assert services.stocked_qty(workorder) == Decimal("0")

    services.record_finished(workorder, Decimal("4"), user, moved_on=DAY1)
    assert services.stocked_qty(workorder) == Decimal("4")

    bare = _issue(user, _line(user, customer, item), processes[:1])
    bare.steps.all().delete()  # 工程の無い指示書 (試験のためだけに工程を外す)
    assert services.finished_qty(bare) == Decimal("0")


@pytest.mark.django_db
def test_annotate_balance_matches_balances(user, customer, items, processes):
    moved, plain = items
    both = masters_services.create_item({"part_no": "T-300", "name": "作り物の環", "unit": "個"}, user)
    _finished(user, customer, moved, processes, "5")

    # 動きも出荷も 2 件ずつある部品 (join で重なれば数が合わなくなる)
    workorder = _issue(user, _line(user, customer, both, qty="20"), processes[:1], qty="20")
    services.record_finished(workorder, Decimal("8"), user, moved_on=DAY1)
    services.record_finished(workorder, Decimal("7"), user, moved_on=DAY2)
    shipping_services.ship(workorder.order_line, Decimal("3"), ORDER_DATE, user)
    shipping_services.ship(workorder.order_line, Decimal("2"), ORDER_DATE, user)

    expected = services.balances([moved, plain, both])
    assert expected == {moved.pk: Decimal("5"), plain.pk: None, both.pk: Decimal("10")}
    rows = {item.pk: item for item in services.annotate_balance(Item.objects.all(), "pk")}
    for pk, value in expected.items():
        got = rows[pk].stock_balance
        assert (got is None and value is None) or Decimal(str(got)) == value, (pk, got, value)
    assert rows[moved.pk].last_moved_on == DAY1
    assert rows[both.pk].last_moved_on == DAY2
    assert rows[plain.pk].last_moved_on is None
    assert list(services.annotate_balance(Item.objects.all(), "pk").filter(stock_balance__isnull=False).order_by(
        "part_no"
    ).values_list("part_no", flat=True)) == ["T-100", "T-300"]

    # 受注の明細 (annotate_remaining の後) に item_id で当てても、残の数は変わらない
    plain_lines = orders_services.annotate_remaining(OrderLine.objects.all())
    remaining = {line.pk: line.remaining_total for line in plain_lines}
    annotated = services.annotate_balance(orders_services.annotate_remaining(OrderLine.objects.all()), "item_id")
    for line in annotated:
        assert line.remaining_total == remaining[line.pk]
        value = expected.get(line.item_id)
        got = line.stock_balance
        assert (got is None and value is None) or Decimal(str(got)) == value
    assert len(remaining) == annotated.count()


@pytest.mark.django_db
def test_source_is_generic_reference(user, customer, subcontractor, items, processes, no_http):
    item = items[0]
    workorder = _issue(user, _line(user, customer, item), processes[:1])
    finished = services.record_finished(workorder, Decimal("2"), user, moved_on=DAY1)
    assert finished.source == workorder
    assert finished.source_label() == str(workorder)

    po = purchasing_services.create_purchase_order(
        subcontractor,
        [{"item": item, "qty": Decimal("5"), "unit_price": Decimal("10")}],
        user,
        order_date=ORDER_DATE,
    )
    receipt = purchasing_services.receive(po.lines.get(), Decimal("5"), DAY2, user)
    received = StockMove.objects.get(kind=KIND_RECEIVED)
    assert received.source == receipt
    assert received.source_label() == str(receipt)
    assert received.qty == Decimal("5") and received.moved_on == DAY2

    issued = services.record_issued(item, Decimal("1"), DAY2, user)
    assert issued.source is None
    assert issued.source_label() == ""
    assert str(issued) == "2026-11-04 T-100 払い出し -1"


# --- 棚卸しで数え始めた部品 (N80。本人の初見テスト R3 の「実地の数 5 が −65 と出る」) ---


def _annotated():
    """部品の queryset に annotate_balance を当てた行 ({pk: 部品})。"""
    return {item.pk: item for item in services.annotate_balance(Item.objects.all(), "pk")}


def _same(got, value):
    return (got is None and value is None) or (got is not None and value is not None and Decimal(str(got)) == value)


@pytest.mark.django_db
def test_first_count_ignores_earlier_shipments(user, customer, items):
    item = items[0]
    line = _line(user, customer, item, qty="70")
    shipping_services.ship(line, Decimal("40"), ORDER_DATE, user)
    shipping_services.ship(line, Decimal("30"), ORDER_DATE, user)
    assert services.balance(item) is None  # 出荷だけでは在庫を数えていない

    move = services.adjust(item, Decimal("5"), DAY1, user, note="棚卸し")

    assert move.kind == KIND_ADJUSTED and move.qty == Decimal("5")
    assert services.balance(item) == Decimal("5")  # 5 − 70 = −65 でない
    row = _annotated()[item.pk]
    assert row.stock_counted_from == DAY1
    assert _same(row.stock_balance, Decimal("5"))


@pytest.mark.django_db
def test_count_twice_keeps_counted(user, customer, items):
    item = items[0]
    line = _line(user, customer, item, qty="70")
    shipping_services.ship(line, Decimal("40"), ORDER_DATE, user)
    shipping_services.ship(line, Decimal("30"), ORDER_DATE, user)
    services.adjust(item, Decimal("5"), DAY1, user)
    count = StockMove.objects.count()

    for day in (DAY1, DAY2):
        with pytest.raises(ValueError, match="調整は要りません"):
            services.adjust(item, Decimal("5"), day, user)

    assert StockMove.objects.count() == count
    assert services.balance(item) == Decimal("5")


@pytest.mark.django_db
def test_first_count_absorbs_same_day_shipments(user, customer, items):
    item = items[0]
    line = _line(user, customer, item, qty="70")
    shipping_services.ship(line, Decimal("40"), ORDER_DATE, user)
    shipping_services.ship(line, Decimal("2"), DAY1, user)  # 数える前に入れた、数え始めの日と同じ日の出荷

    move = services.adjust(item, Decimal("5"), DAY1, user)

    assert move.qty == Decimal("7")  # 実地の数 5 + その日と後の出荷 2
    assert services.balance(item) == Decimal("5")


@pytest.mark.django_db
def test_shipments_after_first_count_are_subtracted(user, customer, items):
    item = items[0]
    line = _line(user, customer, item, qty="70")
    shipping_services.ship(line, Decimal("40"), ORDER_DATE, user)
    services.adjust(item, Decimal("5"), DAY1, user)
    assert services.balance(item) == Decimal("5")

    shipping_services.ship(line, Decimal("2"), DAY2, user)
    assert services.balance(item) == Decimal("3")
    shipping_services.ship(line, Decimal("1"), DAY1, user)  # 数え始めの日と同じ日 (数えた後に入れた物) は引く
    assert services.balance(item) == Decimal("2")


@pytest.mark.django_db
def test_backdated_shipment_after_first_count_is_ignored(user, customer, items):
    item = items[0]
    line = _line(user, customer, item, qty="70")
    services.adjust(item, Decimal("5"), DAY2, user)
    assert services.balance(item) == Decimal("5")

    shipping_services.ship(line, Decimal("3"), DAY1, user)  # 数え始めの日より前の出荷日 = 実地の数に入っている
    assert services.balance(item) == Decimal("5")
    shipping_services.ship(line, Decimal("1"), DAY2, user)
    assert services.balance(item) == Decimal("4")


@pytest.mark.django_db
def test_count_after_moves_keeps_all_shipments(user, customer, items, processes):
    item = items[0]
    workorder = _issue(user, _line(user, customer, item), processes[:1])
    services.record_finished(workorder, Decimal("10"), user, moved_on=DAY1)
    shipping_services.ship(workorder.order_line, Decimal("4"), ORDER_DATE, user)
    assert services.balance(item) == Decimal("6")

    move = services.adjust(item, Decimal("5"), DAY2, user)

    assert move.qty == Decimal("-1")
    assert services.balance(item) == Decimal("5")
    assert _annotated()[item.pk].stock_counted_from is None  # 最初の行は製造の完了
    # 最初の行が製造の完了の部品は、今までどおり全部の出荷を引く (N42 のまま)
    shipping_services.ship(workorder.order_line, Decimal("1"), ORDER_DATE, user)
    assert services.balance(item) == Decimal("4")


@pytest.mark.django_db
def test_moves_after_count_still_add(user, customer, subcontractor, items, processes, no_http):
    item = items[0]
    line = _line(user, customer, item, qty="70")
    shipping_services.ship(line, Decimal("40"), ORDER_DATE, user)
    services.adjust(item, Decimal("5"), DAY1, user)

    issued = services.record_issued(item, Decimal("2"), DAY2, user)
    assert issued.qty == Decimal("-2")
    assert services.balance(item) == Decimal("3")

    workorder = _issue(user, _line(user, customer, item), processes[:1])
    finished = services.record_finished(workorder, Decimal("4"), user, moved_on=DAY2)
    assert finished.qty == Decimal("4")
    assert services.balance(item) == Decimal("7")

    po = purchasing_services.create_purchase_order(
        subcontractor,
        [{"item": item, "qty": Decimal("5"), "unit_price": Decimal("10")}],
        user,
        order_date=ORDER_DATE,
    )
    purchasing_services.receive(po.lines.get(), Decimal("5"), DAY2, user)
    assert StockMove.objects.get(kind=KIND_RECEIVED).qty == Decimal("5")
    assert services.balance(item) == Decimal("12")


@pytest.mark.django_db
def test_annotate_balance_matches_after_count(user, customer, items, processes, django_assert_max_num_queries):
    counted, recounted = items
    moved = masters_services.create_item({"part_no": "T-300", "name": "作り物の環", "unit": "個"}, user)
    shipped_only = masters_services.create_item({"part_no": "T-400", "name": "作り物の棒", "unit": "本"}, user)
    plain = masters_services.create_item({"part_no": "T-500", "name": "作り物の蓋", "unit": "個"}, user)

    # 数え始めた部品: 前の日・同じ日 (数える前と後)・後の日の出荷
    line = _line(user, customer, counted, qty="70")
    shipping_services.ship(line, Decimal("40"), ORDER_DATE, user)
    shipping_services.ship(line, Decimal("2"), DAY1, user)
    services.adjust(counted, Decimal("5"), DAY1, user)
    shipping_services.ship(line, Decimal("1"), DAY1, user)
    shipping_services.ship(line, Decimal("1"), DAY2, user)
    shipping_services.ship(line, Decimal("3"), ORDER_DATE, user)  # 数え始めの日より前の出荷日 (引かない)

    # 動きの後に数えた部品 (最初の行は製造の完了。全部の出荷を引く)
    workorder = _issue(user, _line(user, customer, recounted), processes[:1])
    services.record_finished(workorder, Decimal("10"), user, moved_on=DAY1)
    shipping_services.ship(workorder.order_line, Decimal("4"), ORDER_DATE, user)
    services.adjust(recounted, Decimal("5"), DAY2, user)
    shipping_services.ship(workorder.order_line, Decimal("1"), ORDER_DATE, user)

    # 動きだけの部品と、出荷だけの部品
    other = _issue(user, _line(user, customer, moved), processes[:1])
    services.record_finished(other, Decimal("9"), user, moved_on=DAY1)
    shipping_services.ship(other.order_line, Decimal("3"), ORDER_DATE, user)
    shipping_services.ship(_line(user, customer, shipped_only), Decimal("4"), ORDER_DATE, user)

    with django_assert_max_num_queries(2):
        expected = services.balances([counted, recounted, moved, shipped_only, plain])
    assert expected == {
        counted.pk: Decimal("3"),
        recounted.pk: Decimal("4"),
        moved.pk: Decimal("6"),
        shipped_only.pk: None,
        plain.pk: None,
    }

    rows = _annotated()
    for pk, value in expected.items():
        assert _same(rows[pk].stock_balance, value), (pk, rows[pk].stock_balance, value)
    assert rows[counted.pk].stock_counted_from == DAY1
    for pk in (recounted.pk, moved.pk, shipped_only.pk, plain.pk):
        assert rows[pk].stock_counted_from is None, pk
    assert rows[counted.pk].last_moved_on == DAY1

    # 受注の明細 (annotate_remaining の後) に item_id で当てても、残の数は変わらず、在庫数は同じ
    remaining = {row.pk: row.remaining_total for row in orders_services.annotate_remaining(OrderLine.objects.all())}
    annotated = services.annotate_balance(orders_services.annotate_remaining(OrderLine.objects.all()), "item_id")
    for row in annotated:
        assert row.remaining_total == remaining[row.pk]
        assert _same(row.stock_balance, expected.get(row.item_id)), (row.pk, row.stock_balance)
    assert len(remaining) == annotated.count()
