"""受注の明細の「完了」の印 (見比べのまとめの M03。本人の決め 11-11 08:4x・09:3x。RULES.md R25)。

印は人だけが付け外しする (orders.services.mark_completed・clear_completed と、出荷の「残りは出さずに完了にする」= shipping.services.ship の close_rest)。
出荷・出荷の直し・出荷の削除・数量の直しでは書き換えない。完了の明細は足りない分を残に数えないので、受注の状態・生産管理ボード・
今日やること・候補の口・請求の一式の表が、残の数え方の 1 か所に従う。印が全部偽なら、今の式 (数量 − 出荷済み) と同じ。
作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.core import dashboard
from seizou.core.exceptions import RuleViolation
from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import Order, OrderLine
from seizou.shipping import services as shipping_services
from tests.billing_helpers import shipped

ORDER_DATE = date(2026, 11, 2)
DAY = date(2026, 11, 5)
NEXT_DAY = date(2026, 11, 6)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def plate(user):
    return masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)


@pytest.fixture
def order(user, customer, shaft, plate):
    """明細 2 行 (数量 100 の軸と 10 の板) の受注。"""
    return services.create_order(
        customer,
        [
            {"item": shaft, "qty": Decimal("100"), "unit_price": Decimal("250")},
            {"item": plate, "qty": Decimal("10"), "unit_price": Decimal("400")},
        ],
        user,
        order_date=ORDER_DATE,
    )


def _lines(order):
    return list(order.lines.order_by("line_no"))


def _line(pk):
    return OrderLine.objects.get(pk=pk)


def _status(order):
    order.refresh_from_db()
    return order.status


# ---------------------------------------------------------------- 印は人だけが書く


@pytest.mark.django_db
def test_shipping_and_edits_never_set_the_mark(user, order):
    first, _second = _lines(order)
    shipment = shipping_services.ship(first, Decimal("100"), DAY, user)
    assert _line(first.pk).completed is False  # 数量に届いても印は書かない

    shipping_services.update_shipment(shipment, Decimal("90"), NEXT_DAY, user)
    assert _line(first.pk).completed is False
    shipping_services.delete_shipment(shipment, user)
    assert _line(first.pk).completed is False
    services.update_order(order, {}, [{"order_line": first, "qty": Decimal("80")}, {"order_line": _second}], user)
    assert _line(first.pk).completed is False


@pytest.mark.django_db
def test_shipping_and_edits_never_clear_the_mark(user, order):
    first, second = _lines(order)
    shipping_services.ship(first, Decimal("98"), DAY, user)
    services.mark_completed(first, user)

    extra = shipping_services.ship(_line(first.pk), Decimal("2"), DAY, user)
    assert _line(first.pk).completed is True
    shipping_services.update_shipment(extra, Decimal("1"), NEXT_DAY, user)
    assert _line(first.pk).completed is True
    shipping_services.delete_shipment(extra, user)
    assert _line(first.pk).completed is True
    services.update_order(order, {}, [{"order_line": first, "qty": Decimal("150")}, {"order_line": second}], user)
    assert _line(first.pk).completed is True


# ---------------------------------------------------------------- 人の操作


@pytest.mark.django_db
def test_mark_and_clear_messages(user, order):
    first, _second = _lines(order)
    shipping_services.ship(first, Decimal("98"), DAY, user)

    marked = services.mark_completed(first, user)
    assert marked.completed is True
    assert marked.saved_message == (
        f"{order.order_no}-1 を完了にしました。残り 2 は出さずに終えます。また出すときは、明細の ⋮ から完了を外してください。"
    )
    # 2 回呼んでも断らず、印は真のまま
    again = services.mark_completed(first, user)
    assert again.completed is True

    cleared = services.clear_completed(first, user)
    assert cleared.completed is False
    assert cleared.saved_message == f"{order.order_no}-1 の完了を外しました。残り 2 をまた出せます。"
    # 完了でない明細は何も書かずに返す
    assert services.clear_completed(first, user).completed is False


@pytest.mark.django_db
def test_messages_show_decimal_rest_and_no_rest(user, customer, shaft):
    order = services.create_order(
        customer, [{"item": shaft, "qty": Decimal("10.5"), "unit_price": Decimal("100")}], user, order_date=ORDER_DATE
    )
    line = order.lines.get()
    shipping_services.ship(line, Decimal("10"), DAY, user)
    marked = services.mark_completed(line, user)
    assert "残り 0.5 は出さずに終えます。" in marked.saved_message

    # 完了の後に数量まで出してから外すと、残りが無いので「また出せます」を付けない
    shipping_services.ship(_line(line.pk), Decimal("0.5"), DAY, user)
    cleared = services.clear_completed(line, user)
    assert cleared.saved_message == f"{order.order_no}-1 の完了を外しました。"


@pytest.mark.django_db
def test_cancelled_order_line_is_refused(user, order):
    first, _second = _lines(order)
    shipping_services.ship(first, Decimal("98"), DAY, user)
    services.mark_completed(first, user)
    services.cancel_order(order, user)

    with pytest.raises(ValueError, match="^保存できません。取り消した受注の明細の完了は外せません。"):
        services.clear_completed(first, user)
    assert _line(first.pk).completed is True
    with pytest.raises(ValueError, match="^保存できません。取り消した受注の明細は完了にできません。"):
        services.mark_completed(_second, user)
    assert _line(_second.pk).completed is False


@pytest.mark.django_db
def test_mark_on_line_shipped_to_qty_writes_nothing(user, order):
    first, _second = _lines(order)
    shipping_services.ship(first, Decimal("100"), DAY, user)
    count = ChangeLog.objects.count()

    result = services.mark_completed(first, user)
    assert result.completed is False
    assert result.saved_message == f"{order.order_no}-1 は数量まで出荷済みで、終わっています。"
    assert _line(first.pk).completed is False
    assert ChangeLog.objects.count() == count


@pytest.mark.django_db
def test_mark_and_clear_are_in_history(user, order):
    first, _second = _lines(order)
    shipping_services.ship(first, Decimal("98"), DAY, user)
    services.mark_completed(first, user)
    services.mark_completed(first, user)  # もう完了 = 増えない
    services.clear_completed(first, user)

    rows = ChangeLog.objects.filter(model="orders.orderline", object_pk=first.pk, action="updated").order_by("pk")
    assert [row.changes for row in rows] == [
        [{"field": "completed", "label": "完了", "type": "bool", "before": "false", "after": "true"}],
        [{"field": "completed", "label": "完了", "type": "bool", "before": "true", "after": "false"}],
    ]
    assert all(row.owner_model == "orders.order" and row.owner_pk == order.pk for row in rows)


@pytest.mark.django_db
def test_mark_does_not_make_open_edit_screen_stale(user, order):
    first, second = _lines(order)
    token = services.order_state_token(order)
    shipping_services.ship(first, Decimal("98"), DAY, user)
    services.mark_completed(first, user)
    assert services.order_state_token(order) == token

    saved = services.update_order(
        order, {"note": "完了の後の直し"}, [{"order_line": first}, {"order_line": second}], user, expected_state=token
    )
    assert saved.note == "完了の後の直し"
    assert _line(first.pk).completed is True


# ---------------------------------------------------------------- 出荷の「残りは出さずに完了にする」


@pytest.mark.django_db
def test_ship_close_rest_marks_the_short_line(user, order):
    first, _second = _lines(order)
    shipping_services.ship(first, Decimal("98"), DAY, user)

    result = shipping_services.ship(_line(first.pk), Decimal("1"), DAY, user, close_rest=True)
    row = _line(first.pk)
    assert row.completed is True
    assert services.remaining_qty(row) == Decimal("0")
    assert services.unfilled_qty(row) == Decimal("1")
    assert services.shipped_qty(row) == Decimal("99")
    assert result.saved_message == (
        f"出荷を保存しました。{order.order_no}-1 を完了にしました。残り 1 は出さずに終えます。"
        "また出すときは、明細の ⋮ から完了を外してください。"
    )


@pytest.mark.django_db
def test_ship_close_rest_reaching_qty_writes_no_mark(user, order):
    _first, second = _lines(order)
    result = shipping_services.ship(second, Decimal("10"), DAY, user, close_rest=True)

    assert _line(second.pk).completed is False
    assert services.remaining_qty(_line(second.pk)) == Decimal("0")
    assert getattr(result, "saved_message", None) is None


@pytest.mark.django_db
def test_close_rest_passes_when_partial_shipment_is_off(settings, user, order):
    first, second = _lines(order)
    settings.SEIZOU = {"partial_shipment": False}

    with pytest.raises(RuleViolation) as caught:
        shipping_services.ship(first, Decimal("98"), DAY, user)
    assert caught.value.code == "分納"
    assert services.shipped_qty(_line(first.pk)) == Decimal("0")

    shipping_services.ship(first, Decimal("98"), DAY, user, close_rest=True)
    row = _line(first.pk)
    assert row.completed is True
    assert services.shipped_qty(row) == Decimal("98")
    assert services.remaining_qty(row) == Decimal("0")
    # 残りの数と同じ数の出荷は今までどおり通る
    shipping_services.ship(second, Decimal("10"), DAY, user)
    assert _status(order) == Order.STATUS_SHIPPED


# ---------------------------------------------------------------- 数え方


@pytest.mark.django_db
def test_order_status_follows_completion(user, order):
    first, second = _lines(order)
    shipping_services.ship(first, Decimal("98"), DAY, user)
    services.mark_completed(first, user)
    assert _status(order) == Order.STATUS_PARTIAL  # 2 行目がまだ

    shipping_services.ship(second, Decimal("10"), DAY, user)
    assert _status(order) == Order.STATUS_SHIPPED

    services.clear_completed(first, user)
    assert _status(order) == Order.STATUS_PARTIAL


@pytest.mark.django_db
def test_completing_all_lines_without_shipment_ships_the_order(user, order):
    first, second = _lines(order)
    services.mark_completed(first, user)
    assert _status(order) == Order.STATUS_OPEN
    services.mark_completed(second, user)
    assert _status(order) == Order.STATUS_SHIPPED
    services.clear_completed(second, user)
    assert _status(order) == Order.STATUS_OPEN


@pytest.mark.django_db
def test_dashboard_counts_follow_completion(user, customer, shaft):
    order = services.create_order(
        customer, [{"item": shaft, "qty": Decimal("100"), "unit_price": Decimal("250")}], user, order_date=ORDER_DATE
    )
    line = order.lines.get()
    shipping_services.ship(line, Decimal("98"), DAY, user)

    def counts():
        return (
            dashboard.orders_to_ship().count(),
            dashboard._lines_without_workorder().count(),
            dashboard.lines_to_ship().count(),
        )

    assert counts() == (1, 1, 1)
    services.mark_completed(line, user)
    assert counts() == (0, 0, 0)
    services.clear_completed(line, user)
    assert counts() == (1, 1, 1)


@pytest.mark.django_db
def test_board_and_select_drop_the_completed_line(logged_in, user, order):
    first, second = _lines(order)
    shipping_services.ship(first, Decimal("98"), DAY, user)

    def board_pks():
        response = logged_in.get(reverse("orders:orderline_list"))
        assert response.status_code == 200
        return {row["obj"].pk for row in response.context["rows"]}

    def select_pks():
        response = logged_in.get(reverse("orders:orderline_select"))
        assert response.status_code == 200
        return {row["id"] for row in response.json()["items"]}

    assert board_pks() == select_pks() == {first.pk, second.pk}
    services.mark_completed(first, user)
    assert board_pks() == select_pks() == {second.pk}
    services.clear_completed(first, user)
    assert board_pks() == select_pks() == {first.pk, second.pk}


@pytest.mark.django_db
def test_short_completed_lump_goes_to_unbilled_lumps(user, customer, shaft):
    order = services.create_order(
        customer,
        [{"item": shaft, "qty": Decimal("300"), "unit_price": Decimal("50000"), "price_basis": "lump"}],
        user,
        order_date=ORDER_DATE,
    )
    line = order.lines.get()
    shipped(user, customer, line, "100")
    assert billing_services.unbilled_lumps() == []

    services.mark_completed(line, user)
    assert [row.pk for row in billing_services.unbilled_lumps()] == [line.pk]
    assert [row.pk for row in billing_services.unbilled_lumps(customer)] == [line.pk]

    services.clear_completed(line, user)
    assert billing_services.unbilled_lumps() == []


# ---------------------------------------------------------------- 今ある置き場の動きが変わらない


def _check_current_formula(order):
    """印が全部偽の明細で、remaining_total が今の式 (数量 − 出荷済み) と同じ。"""
    rows = list(services.annotate_remaining(OrderLine.objects.filter(order=order)))
    assert rows
    for row in rows:
        assert row.completed is False
        assert row.remaining_total == row.qty - row.shipped_total
        assert services.remaining_qty(row) == row.qty - services.shipped_qty(row)
        assert services.unfilled_qty(row) == Decimal("0")


@pytest.mark.django_db
def test_lines_without_mark_keep_the_current_formula(user, order):
    first, second = _lines(order)
    _check_current_formula(order)
    assert dashboard.orders_to_ship().count() == 1

    shipment = shipping_services.ship(first, Decimal("100"), DAY, user)
    _check_current_formula(order)
    assert _status(order) == Order.STATUS_PARTIAL

    over = shipping_services.ship(second, Decimal("12"), DAY, user)
    _check_current_formula(order)
    assert _status(order) == Order.STATUS_SHIPPED
    assert dashboard.orders_to_ship().count() == 0

    shipping_services.update_shipment(shipment, Decimal("60"), NEXT_DAY, user)
    _check_current_formula(order)
    assert _status(order) == Order.STATUS_PARTIAL
    assert dashboard.orders_to_ship().count() == 1

    services.update_order(order, {}, [{"order_line": first, "qty": Decimal("60")}, {"order_line": second}], user)
    _check_current_formula(order)
    assert _status(order) == Order.STATUS_SHIPPED

    shipping_services.update_shipment(over, Decimal("5"), NEXT_DAY, user)
    shipping_services.delete_shipment(over, user)
    _check_current_formula(order)
    assert _status(order) == Order.STATUS_PARTIAL
    assert dashboard.orders_to_ship().count() == 1
