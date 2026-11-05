"""ダッシュボードの 2 枚のカード (N77): 「出荷の残りがある明細」と「今月の受注」の数と、押した先の一覧の件数。

受注と出荷は services で作る (tests/test_core_dashboard.py の make_order の形)。名前は作り物だけ。
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.core import views
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.shipping import services as shipping_services

LINES_LABEL = "出荷の残りがある明細"
MONTH_LABEL = "今月の受注"


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
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def make_order(user, customer, item, lines, *, order_date):
    """lines = [(数量, 単価, 納期), ...] の受注を作る。"""
    return services.create_order(
        customer,
        [
            {"item": item, "qty": Decimal(qty), "unit_price": Decimal(price), "due_date": due}
            for qty, price, due in lines
        ],
        user,
        order_date=order_date,
    )


def card(label):
    return next(stat for stat in views.dashboard_stats() if stat["label"] == label)


@pytest.mark.django_db
def test_lines_card_matches_orderline_list(logged_in, user, customer, shaft):
    today = timezone.localdate()
    order_a = make_order(user, customer, shaft, [("3", "100", None), ("2", "100", None)], order_date=today)
    order_b = make_order(user, customer, shaft, [("4", "100", None)], order_date=today)
    order_c = make_order(user, customer, shaft, [("5", "100", None)], order_date=today)
    # A の 1 行目だけを全部出荷する (A は一部出荷。出荷し終えた 1 行目は数えない)
    shipping_services.ship(order_a.lines.get(line_no=1), Decimal("3"), today, user)
    services.cancel_order(order_b, user)
    order_a.refresh_from_db()
    assert order_a.status == order_a.STATUS_PARTIAL

    stat = card(LINES_LABEL)
    assert stat["value"] == 2  # A の 2 行目と C
    assert stat["url"] == reverse("orders:orderline_list")
    assert stat["unit"] == "行"
    assert stat["note"] == "出荷が終わっていない受注の明細の数"

    listing = logged_in.get(stat["url"])
    assert listing.status_code == 200
    assert listing.context["paginator"].count == stat["value"]
    shown = {(row["obj"].order.order_no, row["obj"].line_no) for row in listing.context["rows"]}
    assert shown == {(order_a.order_no, 2), (order_c.order_no, 1)}


@pytest.mark.django_db
def test_this_month_card_links_month_filtered_orders(logged_in, user, customer, shaft):
    today = timezone.localdate()
    last_month = today.replace(day=1) - timedelta(days=1)
    kept = make_order(user, customer, shaft, [("1", "100", None)], order_date=today)
    shipped = make_order(user, customer, shaft, [("2", "100", None)], order_date=today)
    shipping_services.ship(shipped.lines.get(), Decimal("2"), today, user)
    cancelled = make_order(user, customer, shaft, [("3", "100", None)], order_date=today)
    services.cancel_order(cancelled, user)
    make_order(user, customer, shaft, [("4", "100", None)], order_date=last_month)

    stat = card(MONTH_LABEL)
    assert stat["value"] == 2  # 受注のままの 1 件と、全部を出荷した 1 件
    first = today.replace(day=1)
    last = (first + timedelta(days=31)).replace(day=1) - timedelta(days=1)
    assert stat["url"] == reverse("orders:order_list") + f"?f_open=0&f_ordered_from={first}&f_ordered_to={last}"

    listing = logged_in.get(stat["url"])
    assert listing.status_code == 200
    # 絞り込みの枠の受注日の期間は取消も出す。カードの数は取消を除く (N126 の本体への知らせの 2 の (a))。
    assert listing.context["paginator"].count == stat["value"] + 1
    assert {row["obj"].order_no for row in listing.context["rows"]} == {kept.order_no, shipped.order_no, cancelled.order_no}

    url = reverse("orders:order_list")
    everything = logged_in.get(url, {"f_open": "0"}).context["paginator"].count
    default = logged_in.get(url).context["paginator"].count
    assert everything == 4 and default == 2  # 全部 / 出荷の残りがある物 (今月の 1 件と先月の 1 件)
    for params, expected in (({"f_open": "0", "f_ordered_from": "2026-13-01"}, everything), ({"month": "abc"}, default)):
        response = logged_in.get(url, params)
        assert response.status_code == 200, params
        assert response.context["paginator"].count == expected, params


@pytest.mark.django_db
def test_month_param_reads_year_and_month_only(monkeypatch):
    # 受注の一覧の古い month の GET は無い (N126)。カードの link は今月の 1 日と末日を受注日の期間に書く (12 月と閏年の 2 月も)。
    for today, first, last in (
        (date(2026, 11, 15), "2026-11-01", "2026-11-30"),
        (date(2026, 12, 31), "2026-12-01", "2026-12-31"),
        (date(2028, 2, 10), "2028-02-01", "2028-02-29"),
    ):
        monkeypatch.setattr(views.timezone, "localdate", lambda today=today: today)
        (stat,) = [s for s in views.dashboard_stats() if s["label"] == MONTH_LABEL]
        assert stat["url"].endswith(f"?f_open=0&f_ordered_from={first}&f_ordered_to={last}"), today
