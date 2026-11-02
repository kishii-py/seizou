"""ダッシュボードの読む関数 (seizou.core.dashboard) の約束: 納期アラートと月次の推移と図の座標。

日は today を渡して固定する (月の境で落ちないため)。今日の数字のカードと比べる 1 本だけ今日のまま。
受注と出荷は services で作る。名前は作り物だけ。
"""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.core import dashboard, views
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.shipping import services as shipping_services

TODAY = date(2026, 11, 3)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def floor_customer(user):
    """金額の端数を切り捨てる得意先 (出荷の金額が round_amount を通ったかを見るため)。"""
    return masters_services.create_partner(
        {"code": "C002", "name": "作り物の切捨の得意先", "role": "customer", "rounding": "floor"}, user
    )


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def make_order(user, customer, item, lines, *, order_date=TODAY):
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


def line_of(order, line_no):
    return order.lines.get(line_no=line_no)


class _NoParts:
    """部品が 1 つも入っていない会社の apps の代わり。"""

    def get_model(self, app_label, name):
        raise LookupError(f"{app_label}.{name}")


def _points(d):
    return [(float(x), float(y)) for x, y in re.findall(r"(-?\d+\.\d),(-?\d+\.\d)", d)]


@pytest.mark.django_db
def test_due_alerts_overdue_soon_and_later(user, customer, shaft):
    order = make_order(
        user,
        customer,
        shaft,
        [
            ("10", "100", date(2026, 11, 1)),
            ("10", "100", date(2026, 11, 10)),
            ("10", "100", date(2026, 11, 3)),
            ("10", "100", date(2026, 11, 11)),
            ("10", "100", None),
        ],
    )
    alerts = dashboard.due_alerts(today=TODAY)
    assert alerts["today"] == TODAY and alerts["days"] == 7 and alerts["limit"] == 10
    assert alerts["list_url"] == reverse("orders:orderline_list")

    assert alerts["overdue"]["count"] == 1
    late = alerts["overdue"]["rows"][0]
    assert late["due_date"] == date(2026, 11, 1) and late["days"] == 2
    assert late["order_no"] == order.order_no
    assert late["order_url"] == reverse("orders:order_detail", args=[order.pk])
    assert late["customer"] == customer and late["item"] == shaft
    assert late["qty"] == Decimal("10") and late["remaining"] == Decimal("10")

    assert alerts["soon"]["count"] == 2
    soon = alerts["soon"]["rows"]
    assert [row["due_date"] for row in soon] == [date(2026, 11, 3), date(2026, 11, 10)]
    assert [row["days"] for row in soon] == [0, 7]


@pytest.mark.django_db
def test_due_alerts_skip_shipped_and_cancelled(user, customer, shaft):
    order = make_order(
        user,
        customer,
        shaft,
        [("10", "100", date(2026, 11, 1)), ("10", "100", date(2026, 11, 2))],
    )
    shipping_services.ship(line_of(order, 1), Decimal("10"), date(2026, 10, 30), user)
    shipping_services.ship(line_of(order, 2), Decimal("4"), date(2026, 10, 30), user)
    cancelled = make_order(user, customer, shaft, [("5", "100", date(2026, 11, 1))])
    services.cancel_order(cancelled, user)

    alerts = dashboard.due_alerts(today=TODAY)
    assert alerts["overdue"]["count"] == 1
    row = alerts["overdue"]["rows"][0]
    assert row["order_no"] == order.order_no and row["due_date"] == date(2026, 11, 2)
    assert row["qty"] == Decimal("10") and row["remaining"] == Decimal("6")
    assert alerts["soon"]["count"] == 0 and alerts["soon"]["rows"] == []


@pytest.mark.django_db
def test_due_alerts_limit_and_order(user, customer, shaft):
    # 行番号の順と納期の順を逆にして、並びが納期で決まることを見る。
    dues = [date(2026, 10, 12) - timedelta(days=offset) for offset in range(12)]
    make_order(user, customer, shaft, [("1", "100", due) for due in dues])
    alerts = dashboard.due_alerts(today=TODAY)
    assert alerts["overdue"]["count"] == 12
    rows = alerts["overdue"]["rows"]
    assert len(rows) == 10
    assert [row["due_date"] for row in rows] == sorted(dues)[:10]
    assert rows[0]["days"] == (TODAY - date(2026, 10, 1)).days


@pytest.fixture
def trend_data(user, customer, floor_customer, shaft):
    """月次の推移の作り物: 期間の外の受注・取消の受注・端数の出る単価の出荷・後で取消になった受注の出荷。"""
    make_order(user, customer, shaft, [("1", "1000", None)], order_date=date(2026, 5, 31))
    june = make_order(
        user, customer, shaft, [("2", "1000", None), ("1", "500", None)], order_date=date(2026, 6, 1)
    )
    november = make_order(user, floor_customer, shaft, [("10", "33.33", None)], order_date=date(2026, 11, 2))
    cancelled = make_order(user, floor_customer, shaft, [("1", "9999", None)], order_date=date(2026, 11, 1))
    shipping_services.ship(line_of(june, 1), Decimal("2"), date(2026, 7, 15), user)
    shipping_services.ship(line_of(november, 1), Decimal("3"), date(2026, 11, 3), user)
    shipping_services.ship(line_of(cancelled, 1), Decimal("1"), date(2026, 11, 2), user)
    services.cancel_order(cancelled, user)


@pytest.mark.django_db
def test_monthly_trend_keys_and_sums(trend_data):
    trend = dashboard.monthly_trend(today=TODAY)
    months = {row["key"]: row for row in trend["months"]}
    assert [row["key"] for row in trend["months"]] == [
        "2026-06", "2026-07", "2026-08", "2026-09", "2026-10", "2026-11",
    ]
    assert [row["label"] for row in trend["months"]] == ["6 月", "7 月", "8 月", "9 月", "10 月", "11 月"]
    assert trend["has_shipping"] is True and trend["is_empty"] is False

    # 受注: 5/31 の受注は期間の外、取消の受注は入らない。金額は明細の amount (R03 で丸めた値) の合計。
    assert months["2026-06"]["order_count"] == 1 and months["2026-06"]["order_amount"] == Decimal("2500")
    assert months["2026-11"]["order_count"] == 1 and months["2026-11"]["order_amount"] == Decimal("333")
    for key in ("2026-07", "2026-08", "2026-09", "2026-10"):
        assert months[key]["order_count"] == 0 and months[key]["order_amount"] == 0

    # 出荷: 出荷日の月。金額は round_amount(得意先, 数量 × 単価) (3 × 33.33 = 99.99 を切り捨てて 99)。
    # 後で受注が取消になった出荷 (1 × 9999) も数える。
    assert months["2026-06"]["shipment_count"] == 0 and months["2026-06"]["shipment_amount"] == 0
    assert months["2026-07"]["shipment_count"] == 1 and months["2026-07"]["shipment_amount"] == Decimal("2000")
    assert months["2026-11"]["shipment_count"] == 2
    assert months["2026-11"]["shipment_amount"] == Decimal("99") + Decimal("9999")
    assert trend["max_amount"] == Decimal("10098")


@pytest.mark.django_db
def test_monthly_trend_chart_inside_frame(trend_data):
    trend = dashboard.monthly_trend(today=TODAY)
    chart = trend["chart"]
    width, height = chart["width"], chart["height"]
    assert (width, height) == (640, 240)

    assert chart["top"] >= trend["max_amount"]
    assert 4 <= len(chart["ticks"]) <= 5
    # 一番上が 1 万以上なので、目盛りの字は全部「万」の書き方。
    assert [tick["label"] for tick in chart["ticks"]] == ["0 万", "0.5 万", "1 万", "1.5 万"]
    for tick in chart["ticks"]:
        assert 0 <= float(tick["y"]) <= height
    for label in chart["labels"]:
        assert 0 <= float(label["x"]) <= width
    for key in ("left", "right", "tick_label_x"):
        assert 0 <= float(chart[key]) <= width
    assert 0 <= float(chart["label_y"]) <= height

    bars = chart["order_bars"] + chart["shipment_bars"]
    assert [bar["key"] for bar in chart["order_bars"]] == ["2026-06", "2026-11"]
    assert [bar["key"] for bar in chart["shipment_bars"]] == ["2026-07", "2026-11"]
    for bar in bars:
        assert bar["value"] > 0
        points = _points(bar["d"])
        assert points
        xs = [x for x, _y in points]
        assert all(0 <= x <= width and 0 <= y <= height for x, y in points)
        assert max(xs) - min(xs) <= 24


@pytest.mark.django_db
def test_monthly_trend_tick_labels_under_man(user, customer, shaft):
    make_order(user, customer, shaft, [("8", "1000", None)], order_date=TODAY)
    chart = dashboard.monthly_trend(today=TODAY)["chart"]
    assert chart["top"] == Decimal("8000")
    assert [tick["label"] for tick in chart["ticks"]] == ["0", "2,000", "4,000", "6,000", "8,000"]
    assert [tick["y"] for tick in chart["ticks"]] == ["200.0", "154.0", "108.0", "62.0", "16.0"]


@pytest.mark.django_db
def test_monthly_trend_empty(user):
    trend = dashboard.monthly_trend(today=TODAY)
    assert trend["is_empty"] is True
    assert len(trend["months"]) == 6
    assert trend["max_amount"] == 0
    assert trend["chart"]["order_bars"] == [] and trend["chart"]["shipment_bars"] == []
    assert trend["chart"]["top"] >= 1


@pytest.mark.django_db
def test_missing_part_returns_none(monkeypatch):
    monkeypatch.setattr(dashboard, "apps", _NoParts())
    assert dashboard.due_alerts(today=TODAY) is None
    assert dashboard.monthly_trend(today=TODAY) is None


@pytest.mark.django_db
def test_this_month_matches_stat_card(user, customer, shaft):
    today = timezone.localdate()
    make_order(user, customer, shaft, [("1", "100", None)], order_date=today)
    make_order(user, customer, shaft, [("2", "100", None)], order_date=today)
    cancelled = make_order(user, customer, shaft, [("3", "100", None)], order_date=today)
    services.cancel_order(cancelled, user)

    stat = next(stat for stat in views.dashboard_stats() if stat["label"] == "今月の受注")
    this_month = dashboard.monthly_trend()["months"][-1]
    assert this_month["key"] == today.strftime("%Y-%m")
    assert this_month["order_count"] == stat["value"] == 2
