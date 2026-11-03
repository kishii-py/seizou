"""注残の盤 (注文残の一覧。02 の 8 節)。判断の列の値 (orders.board.BoardRow)・納期の絞り込み・N+1 の無い読み方。

受注・出荷・指示書・実績・予定は services で作る。関数の試験は today を渡し、画面の試験は timezone.localdate() から数える。
作り物のデータだけを使う。
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.orders import board
from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine
from seizou.progress import services as progress_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
TODAY = date(2026, 11, 10)

COLUMNS = [
    "受注番号",
    "得意先",
    "部品",
    "納期",
    "納期まで",
    "数量",
    "出荷済み",
    "残",
    "指示書",
    "進捗",
    "計画の終了日",
    "判断",
    "受注日",
    "状態",
]


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
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


@pytest.fixture
def bend(user):
    return masters_services.create_process({"code": "BEND", "name": "曲げ", "sort_order": 2}, user)


def _line(user, customer, item, *, qty="10", due=None):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100"), "due_date": due}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _issue(user, line, *processes, qty="10"):
    return workorders_services.issue_workorder(
        line, Decimal(qty), [{"process": p, "planned_qty": Decimal(qty)} for p in processes], user
    )


def _record(user, step, qty):
    return progress_services.record_progress(step, Decimal(qty), timezone.now(), user)


def _fresh(line):
    """board は cached_property なので、指示書や実績を足した後は読み直す。"""
    return OrderLine.objects.get(pk=line.pk)


def _row_pks(response):
    return {row["obj"].pk for row in response.context["rows"]}


# --- BoardRow (関数の試験。today を渡す) ---


@pytest.mark.django_db
def test_board_row_values(user, customer, item, cut, bend):
    due = TODAY + timedelta(days=5)
    line = _line(user, customer, item, due=due)
    shipping_services.ship(line, Decimal("4"), ORDER_DATE, user)
    workorder = _issue(user, line, cut, bend)
    first, second = workorder.steps.order_by("seq")
    _record(user, first, "3")
    workorders_services.plan_step(first, TODAY, TODAY + timedelta(days=1), user)
    workorders_services.plan_step(second, TODAY + timedelta(days=2), TODAY + timedelta(days=3), user)
    workorders_services.accept_workorder(workorder, user)

    row = board.BoardRow(_fresh(line), TODAY)

    assert row.issued_total == Decimal("10")
    assert row.workorder_count == 1 and row.accepted_count == 1
    assert row.workorder_label == "10 受けた"
    assert row.progress_label == "切断 3/10"
    assert row.current_step == first
    assert row.steps == [first, second]
    assert row.completed_total == Decimal("0")
    assert row.plan_end == TODAY + timedelta(days=3)
    assert row.plan_late is False
    assert row.days_left == 5
    assert row.is_overdue is False
    assert row.latest_workorder == workorder
    assert row.remaining == Decimal("6")
    assert row.judgement_code == "making" and row.judgement == "製造中"


@pytest.mark.django_db
def test_board_row_labels_follow_accepted_and_plan(user, customer, item, cut):
    due = TODAY + timedelta(days=1)
    line = _line(user, customer, item, due=due)
    accepted = _issue(user, line, cut, qty="4.5")
    waiting = _issue(user, line, cut, qty="5.5")
    workorders_services.accept_workorder(accepted, user)
    workorders_services.plan_step(waiting.steps.get(), TODAY, TODAY + timedelta(days=3), user)

    row = board.BoardRow(_fresh(line), TODAY)
    assert row.workorder_label == "10 一部受けた"
    assert row.plan_late is True
    assert row.latest_workorder == waiting

    # 取消の指示書は数えない。
    workorders_services.cancel_workorder(waiting, user)
    row = board.BoardRow(_fresh(line), TODAY)
    assert row.workorder_label == "4.5 受けた"
    assert row.plan_end is None

    other = _line(user, customer, item, due=due)
    _issue(user, other, cut, qty="2")
    assert board.BoardRow(_fresh(other), TODAY).workorder_label == "2 発行"


def _judgement_case(case, user, customer, item, cut):
    """規則ごとの明細 1 つ (数量 10)。"""
    future = TODAY + timedelta(days=7)
    past = TODAY - timedelta(days=1)
    if case == "cancelled":
        line = _line(user, customer, item, due=past)
        _issue(user, line, cut)
        orders_services.cancel_order(line.order, user)
    elif case == "done":
        line = _line(user, customer, item, due=past)
        shipping_services.ship(line, Decimal("10"), ORDER_DATE, user)
    elif case == "ship":
        # 納期が過ぎていても、完成していれば「出荷できる」。
        line = _line(user, customer, item, due=past)
        workorder = _issue(user, line, cut)
        _record(user, workorder.steps.get(), "10")
    elif case == "late":
        line = _line(user, customer, item, due=past)
        workorder = _issue(user, line, cut)
        _record(user, workorder.steps.get(), "3")
    elif case == "need_workorder":
        line = _line(user, customer, item, due=future)
        _issue(user, line, cut, qty="4")
    else:
        line = _line(user, customer, item, due=future)
        workorder = _issue(user, line, cut)
        _record(user, workorder.steps.get(), "3")
    return line


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("case", "label"),
    [
        ("cancelled", "取消"),
        ("done", "出荷済み"),
        ("ship", "出荷できる"),
        ("late", "遅れ"),
        ("need_workorder", "指示書が要る"),
        ("making", "製造中"),
    ],
)
def test_judgement_rules(case, label, user, customer, item, cut):
    line = _judgement_case(case, user, customer, item, cut)

    row = board.BoardRow(_fresh(line), TODAY)

    assert row.judgement_code == case
    assert row.judgement == label
    assert dict(board.JUDGEMENTS)[case] == label


@pytest.mark.django_db
def test_judgement_late_without_workorder_and_ship_needs_last_step(user, customer, item, cut, bend):
    # 指示書が無くても、納期が過ぎていれば「遅れ」(指示書が要るより先)。
    late = _line(user, customer, item, due=TODAY - timedelta(days=1))
    assert board.BoardRow(_fresh(late), TODAY).judgement_code == "late"
    # 最初の工程だけが終わっても完成ではない (完成の数は最後の工程の実績)。
    line = _line(user, customer, item, due=TODAY + timedelta(days=3))
    workorder = _issue(user, line, cut, bend)
    _record(user, workorder.steps.get(seq=1), "10")
    row = board.BoardRow(_fresh(line), TODAY)
    assert row.completed_total == Decimal("0")
    assert row.progress_label == "曲げ 0/10"
    assert row.judgement_code == "making"
    _record(user, workorder.steps.get(seq=2), "10")
    row = board.BoardRow(_fresh(line), TODAY)
    assert row.progress_label == "完了"
    assert row.current_step is None
    assert row.judgement_code == "ship"


@pytest.mark.django_db
def test_board_without_workorders_is_empty_not_error(user, customer, item):
    line = _line(user, customer, item)

    row = board.BoardRow(_fresh(line), TODAY)

    assert row.workorders == []
    assert row.workorder_label == "未"
    assert row.progress_label == ""
    assert row.plan_end is None
    assert row.plan_late is False
    assert row.latest_workorder is None
    assert row.current_step is None
    assert row.days_left is None and row.is_overdue is False
    assert row.judgement_code == "need_workorder"


def test_num_drops_trailing_zeros():
    assert board._num(Decimal("10.00")) == "10"
    assert board._num(Decimal("4.50")) == "4.5"
    assert board._num(Decimal("100")) == "100"
    assert board._num(6) == "6"
    assert board._num(Decimal("0.25")) == "0.25"


def test_filters_read_dates_and_overdue():
    assert board.filters({"due_from": "2026-11-01", "due_to": "2026-11-30", "overdue": "1"}) == {
        "due_from": date(2026, 11, 1),
        "due_to": date(2026, 11, 30),
        "overdue": True,
    }
    assert board.filters({"due_from": "abc", "due_to": "2026-1-5", "overdue": "yes"}) == {
        "due_from": None,
        "due_to": None,
        "overdue": False,
    }
    assert board.filters({}) == {"due_from": None, "due_to": None, "overdue": False}


# --- 画面 (注文残の一覧) ---


@pytest.mark.django_db
def test_list_columns_and_csv(user, logged_in, customer, item, cut):
    today = timezone.localdate()
    late = _line(user, customer, item, due=today - timedelta(days=2))
    issued = _line(user, customer, item, due=today + timedelta(days=4))
    _issue(user, issued, cut, qty="4")

    response = logged_in.get(reverse("orders:orderline_list"))
    assert response.status_code == 200
    assert [col.label for col in response.context["columns"]] == COLUMNS
    cells = {row["obj"].pk: [cell["value"] for cell in row["cells"]] for row in response.context["rows"]}
    assert cells[late.pk][COLUMNS.index("納期まで")] == -2
    assert cells[late.pk][COLUMNS.index("指示書")] == "未"
    assert cells[late.pk][COLUMNS.index("判断")] == "遅れ"
    assert cells[issued.pk][COLUMNS.index("納期まで")] == 4
    assert cells[issued.pk][COLUMNS.index("指示書")] == "4 発行"
    assert cells[issued.pk][COLUMNS.index("進捗")] == "切断 0/4"
    assert cells[issued.pk][COLUMNS.index("判断")] == "指示書が要る"
    body = response.content.decode()
    for text in ("未", "遅れ", "4 発行", "指示書が要る", "切断 0/4"):
        assert text in body

    csv_body = logged_in.get(reverse("orders:orderline_list"), {"format": "csv"}).content.decode()
    header, *rows = csv_body.lstrip("﻿").splitlines()
    assert header == ",".join(COLUMNS)
    assert "操作" not in csv_body
    assert len(rows) == 2
    assert any(",-2," in row and ",遅れ," in row for row in rows)


@pytest.mark.django_db
def test_period_and_overdue_filters(user, logged_in, customer, item):
    today = timezone.localdate()
    yesterday = _line(user, customer, item, due=today - timedelta(days=1))
    same_day = _line(user, customer, item, due=today)
    next_week = _line(user, customer, item, due=today + timedelta(days=7))
    finished = _line(user, customer, item, due=today - timedelta(days=1))
    shipping_services.ship(finished, Decimal("10"), ORDER_DATE, user)

    url = reverse("orders:orderline_list")
    iso = today.isoformat()
    cases = [
        ({}, {yesterday, same_day, next_week}),
        ({"due_from": iso}, {same_day, next_week}),
        ({"due_to": iso}, {yesterday, same_day}),
        ({"due_from": iso, "due_to": iso}, {same_day}),
        ({"overdue": "1"}, {yesterday}),
        ({"due_from": "abc"}, {yesterday, same_day, next_week}),
        ({"due_to": "2026-1-5"}, {yesterday, same_day, next_week}),
        ({"all": "1", "overdue": "1"}, {yesterday, finished}),
        ({"all": "1", "due_from": iso}, {same_day, next_week}),
    ]
    for params, expected in cases:
        for headers in ({}, {"HTTP_HX_REQUEST": "true"}):
            response = logged_in.get(url, params, **headers)
            assert response.status_code == 200, params
            assert _row_pks(response) == {line.pk for line in expected}, (params, headers)


def _lines_with_workorders(user, customer, item, cut, bend, count):
    for number in range(count):
        line = _line(user, customer, item, due=timezone.localdate() + timedelta(days=number))
        workorder = _issue(user, line, cut, bend)
        _record(user, workorder.steps.get(seq=1), "2")
        workorders_services.plan_step(
            workorder.steps.get(seq=2), timezone.localdate(), timezone.localdate() + timedelta(days=1), user
        )
        if number % 2:
            workorders_services.accept_workorder(workorder, user)
        shipping_services.ship(line, Decimal("1"), ORDER_DATE, user)


def _count_queries(client):
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("orders:orderline_list"))
    assert response.status_code == 200
    return len(queries), response


@pytest.mark.django_db
def test_query_count_does_not_grow_with_rows(user, logged_in, customer, item, cut, bend):
    _lines_with_workorders(user, customer, item, cut, bend, 3)
    logged_in.get(reverse("orders:orderline_list"))  # 最初の 1 回の控え (session など) を外す
    small, response = _count_queries(logged_in)
    assert len(response.context["rows"]) == 3

    _lines_with_workorders(user, customer, item, cut, bend, 9)
    large, response = _count_queries(logged_in)
    assert len(response.context["rows"]) == 12
    assert all(row["obj"].board.workorder_label.startswith("10 ") for row in response.context["rows"])

    assert small == large, (small, large)


@pytest.mark.django_db
def test_existing_entrances_still_present(user, logged_in, customer, item):
    line = _line(user, customer, item, due=timezone.localdate())
    shipping_services.ship(line, Decimal("4"), ORDER_DATE, user)

    body = logged_in.get(reverse("orders:orderline_list")).content.decode()

    assert f'href="{reverse("shipping:shipment_create")}?order_line={line.pk}"' in body
    assert f'href="{reverse("workorders:workorder_create")}?order_line={line.pk}"' in body
