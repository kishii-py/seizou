"""注残の盤の「納期まで」を稼働日で数える (orders.board.BoardRow.days_left と core.calendar)。

「納期まで」は休日の表の日と定休日の曜日 (既定は土日) を除いた稼働日の数。遅れの判断は暦の日 (納期が今日より前)。
関数の試験は today を渡す (2026 年 11 月の決まった日。11-20 は金曜、11-23 は勤労感謝の日の月曜)。
休日は masters の services で書く。fixture は tests/test_orders_board.py の形を写した (import して使わない)。作り物のデータだけを使う。
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.core.screens import all_screens
from seizou.masters import services as masters_services
from seizou.masters.models import Holiday
from seizou.orders import board
from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine
from seizou.progress import services as progress_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
FRI = date(2026, 11, 20)
SAT = date(2026, 11, 21)
SUN = date(2026, 11, 22)
LABOR_DAY = date(2026, 11, 23)
TUE = date(2026, 11, 24)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


@pytest.fixture
def bend(user):
    return masters_services.create_process({"code": "BEND", "name": "曲げ", "sort_order": 2}, user)


@pytest.fixture
def labor_day(user):
    return masters_services.create_holiday({"date": LABOR_DAY, "name": "勤労感謝の日", "kind": Holiday.KIND_PUBLIC}, user)


def _line(user, customer, item, *, qty="10", due=None):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100"), "due_date": due}],
        user,
        order_date=ORDER_DATE,
    )
    return OrderLine.objects.get(pk=order.lines.get().pk)


def _issue(user, line, *processes, qty="10"):
    return workorders_services.issue_workorder(
        line, Decimal(qty), [{"process": p, "planned_qty": Decimal(qty)} for p in processes], user
    )


def _lines_with_workorders(user, customer, item, cut, bend, count):
    for number in range(count):
        line = _line(user, customer, item, due=timezone.localdate() + timedelta(days=number))
        workorder = _issue(user, line, cut, bend)
        progress_services.record_progress(workorder.steps.get(seq=1), Decimal("2"), timezone.now(), user)
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


def test_days_left_skips_holidays_and_weekends(user, customer, item, labor_day):
    line = _line(user, customer, item, due=TUE)

    # 11-20 (金) → 11-24 (火): 11-21 (土)・11-22 (日)・11-23 (勤労感謝の日) を除いて 1
    row = board.BoardRow(line, FRI)
    assert row.days_left == 1
    assert row.is_overdue is False
    # 休日の集合を渡すと表を読まない (空なら土日だけを除いて 2)
    assert board.BoardRow(line, FRI, holidays=frozenset()).days_left == 2
    # 盤の画面と同じ作り方 (OrderLine.board は today も holidays も渡さない) でも同じ数え方
    assert line.board.holidays is None


def test_overdue_on_a_day_off_is_minus_one_or_less(user, customer, item, labor_day):
    saturday_due = _line(user, customer, item, due=SAT)
    friday_due = _line(user, customer, item, due=FRI)

    # 日曜に見ると、土曜の納期は稼働日の数が 0 になるが、遅れの行は -1 と出す
    row = board.BoardRow(saturday_due, SUN)
    assert row.days_left == -1
    assert row.is_overdue is True
    assert row.judgement == "遅れ"
    # 金曜の納期を日曜に見ても -1 (11-21・22 は休み)
    assert board.BoardRow(friday_due, SUN).days_left == -1
    # 金曜の納期を 11-24 (火) に見ると -1 (11-23 は休日、11-24 は稼働日)
    assert board.BoardRow(friday_due, TUE).days_left == -1
    assert board.BoardRow(friday_due, TUE).is_overdue is True


def test_same_day_and_no_due_date(user, customer, item):
    same_day = board.BoardRow(_line(user, customer, item, due=FRI), FRI)
    assert same_day.days_left == 0
    assert same_day.is_overdue is False
    assert same_day.judgement != "遅れ"

    no_due = board.BoardRow(_line(user, customer, item), FRI)
    assert no_due.days_left is None
    assert no_due.is_overdue is False


def test_weekly_holidays_setting_counts_saturday(settings, user, customer, item, labor_day):
    settings.SEIZOU = {"weekly_holidays": [6]}  # 日曜だけ
    line = _line(user, customer, item, due=TUE)

    # 11-21 (土) を稼働日として数える: 11-21・11-24 の 2
    assert board.BoardRow(line, FRI).days_left == 2


def test_board_query_count_with_holidays(user, logged_in, customer, item, cut, bend):
    # 休日は最初の控えの GET より前に足す (create_holiday は控えを捨てるので、2 回の測りの間に足さない)
    today = timezone.localdate()
    for offset, name in ((1, "作り物の会社の休み 1"), (2, "作り物の会社の休み 2"), (3, "作り物の会社の休み 3")):
        masters_services.create_holiday({"date": today + timedelta(days=offset), "name": name, "kind": Holiday.KIND_COMPANY}, user)
    _lines_with_workorders(user, customer, item, cut, bend, 3)
    logged_in.get(reverse("orders:orderline_list"))  # 最初の 1 回の控え (session・設定・休日) を外す
    small, response = _count_queries(logged_in)
    assert len(response.context["rows"]) == 3

    _lines_with_workorders(user, customer, item, cut, bend, 9)
    large, response = _count_queries(logged_in)
    assert len(response.context["rows"]) == 12
    assert all(row["obj"].board.days_left is not None for row in response.context["rows"])

    assert small == large, (small, large)


def test_board_screen_help_mentions_workdays():
    # 画面の説明 (screen_help) は画面の目録・説明書・AI の list_screens が読む (盤の HTML の題の下には出さない作り)
    screen = next(s for s in all_screens() if s.url_name == "orders:orderline_list")
    assert "「納期まで」は、マスターの「休日」の日と定休日の曜日を除いた稼働日の数です。" in screen.help
