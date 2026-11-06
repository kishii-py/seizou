"""注残の盤の「在庫」の列と、判断の「出荷できる」の在庫の側 (orders.board の stock_balance)。

受注・指示書・在庫の動きは services で作る。画面の試験は timezone.localdate() から数える。作り物のデータだけを使う。
"""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.orders import board
from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine
from seizou.stock import services as stock_services
from seizou.workorders import services as workorders_services
from tests.query_helpers import count_queries as _count_queries

ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


def _item(user, part_no):
    return masters_services.create_item({"part_no": part_no, "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, *, qty="6"):
    order = orders_services.create_order(
        customer,
        [
            {
                "item": item,
                "qty": Decimal(qty),
                "unit_price": Decimal("100"),
                "due_date": timezone.localdate() + timedelta(days=5),
            }
        ],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _issue(user, line, process, qty="6"):
    return workorders_services.issue_workorder(line, Decimal(qty), [{"process": process, "planned_qty": Decimal(qty)}], user)


def _tr(body, pk):
    match = re.search(rf'<tr id="row-{pk}".*?</tr>', body, re.S)
    assert match, f"row-{pk} が無い"
    return match.group(0)


def _cells(html):
    return re.findall(r"<td[^>]*>(.*?)</td>", html, re.S)


def _stock_cell(response, pk):
    labels = [col.label for col in response.context["columns"]]
    return _cells(_tr(response.content.decode(), pk))[labels.index("在庫")]


@pytest.mark.django_db
def test_board_has_stock_column_and_marks(user, logged_in, customer, cut):
    enough = _item(user, "T-100")
    short = _item(user, "T-200")
    minus = _item(user, "T-300")
    none = _item(user, "T-400")
    lines = {}
    for item, finished in ((enough, "10"), (short, "2"), (minus, "1"), (none, None)):
        line = _line(user, customer, item)
        workorder = _issue(user, line, cut)
        if finished is not None:
            stock_services.record_finished(workorder, Decimal(finished), user)
        lines[item.part_no] = line
    stock_services.record_issued(minus, Decimal("2"), timezone.localdate(), user)  # 在庫 −1

    response = logged_in.get(reverse("orders:orderline_list"))
    body = response.content.decode()

    assert "在庫" in [col.label for col in response.context["columns"]]
    enough_cell = _stock_cell(response, lines["T-100"].pk)
    assert re.sub(r"<[^>]+>", "", enough_cell).strip() in ("10", "10.00")
    assert "badge-success" in _tr(body, lines["T-100"].pk)  # 在庫 10 ≥ 残 6 で「出荷できる」
    assert "出荷できる" in _tr(body, lines["T-100"].pk)

    short_row = _tr(body, lines["T-200"].pk)
    none_row = _tr(body, lines["T-400"].pk)
    assert "badge-success" not in short_row  # 在庫 2 < 残 6
    assert "badge-primary" in short_row and "badge-primary" in none_row  # 在庫の無いときと同じ「製造中」

    assert "text-error" in _stock_cell(response, lines["T-300"].pk)
    assert "—" in _stock_cell(response, lines["T-400"].pk)

    csv_text = logged_in.get(reverse("orders:orderline_list"), {"format": "csv"}).content.decode("utf-8")
    assert "在庫" in csv_text.lstrip("﻿").splitlines()[0].split(",")


@pytest.mark.django_db
def test_board_row_reads_balance_without_annotation(user, customer, cut):
    item = _item(user, "T-100")
    line = _line(user, customer, item)
    workorder = _issue(user, line, cut)
    stock_services.record_finished(workorder, Decimal("4"), user)
    other = _line(user, customer, _item(user, "T-200"))

    fresh = OrderLine.objects.get(pk=line.pk)
    assert "stock_balance" not in fresh.__dict__
    assert board.BoardRow(fresh).stock_balance == stock_services.balance(item) == Decimal("4")
    assert board.BoardRow(OrderLine.objects.get(pk=other.pk)).stock_balance is None

    annotated = board.prefetch_for(orders_services.annotate_remaining(OrderLine.objects.filter(pk=line.pk))).get()
    assert Decimal(str(board.BoardRow(annotated).stock_balance)) == Decimal("4")


def _lines_with_stock(user, customer, cut, count, start):
    for number in range(start, start + count):
        item = _item(user, f"T-{number:03d}")
        workorder = _issue(user, _line(user, customer, item), cut)
        stock_services.record_finished(workorder, Decimal("3"), user)


@pytest.mark.django_db
def test_query_count_unchanged_with_stock_column(user, logged_in, customer, cut):
    _lines_with_stock(user, customer, cut, 3, 100)
    logged_in.get(reverse("orders:orderline_list"))  # 最初の 1 回の控え (session など) を外す
    small, response = _count_queries(logged_in)
    assert len(response.context["rows"]) == 3

    _lines_with_stock(user, customer, cut, 9, 200)
    large, response = _count_queries(logged_in)
    assert len(response.context["rows"]) == 12
    assert all(row["obj"].board.stock_balance == Decimal("3") for row in response.context["rows"])

    assert small == large, (small, large)
    print(f"問い合わせの数: 3 行 = {small}、12 行 = {large}")
