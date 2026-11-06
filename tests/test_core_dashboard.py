"""ダッシュボードの読む関数 (seizou.core.dashboard) の約束: 納期アラートと月次の推移と図の座標。

日は today を渡して固定する (月の境で落ちないため)。今日の数字のカードと比べる 1 本だけ今日のまま。
受注と出荷は services で作る。名前は作り物だけ。
"""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.core import dashboard, views
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.progress import services as progress_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services
from seizou.workorders.models import WorkOrder

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


# ---- 出荷と生産の推移 (N43) ----

TOKYO = ZoneInfo("Asia/Tokyo")


@pytest.fixture
def shipping_data(trend_data, user, customer, shaft):
    """月次の推移の作り物に、期間の外 (5/31) の出荷を 1 件足した物。"""
    may = make_order(user, customer, shaft, [("1", "1000", None)], order_date=date(2026, 5, 31))
    shipping_services.ship(line_of(may, 1), Decimal("1"), date(2026, 5, 31), user)


def _bars_inside(chart, bars):
    width, height = chart["width"], chart["height"]
    for bar in bars:
        assert bar["value"] > 0
        points = _points(bar["d"])
        assert points
        xs = [x for x, _y in points]
        assert all(0 <= x <= width and 0 <= y <= height for x, y in points)
        assert max(xs) - min(xs) <= 24


def _frame_inside(chart):
    width, height = chart["width"], chart["height"]
    assert (width, height) == (640, 240)
    assert 4 <= len(chart["ticks"]) <= 5
    for tick in chart["ticks"]:
        assert 0 <= float(tick["y"]) <= height
    for label in chart["labels"]:
        assert 0 <= float(label["x"]) <= width
    for key in ("left", "right", "tick_label_x"):
        assert 0 <= float(chart[key]) <= width
    assert 0 <= float(chart["label_y"]) <= height


def _label_places_inside(chart):
    """目盛りと月の字は svg の外の HTML の字で、svg の箱に対する割合 (0〜100) の位置に置く (候補 C50 の 2。C50-1 = a)。
    割合は座標の鍵 (目盛りの y・月の x・tick_label_x) と同じ所を指す (小数 1 桁の字なので 0.1 までずれてよい)。"""
    width, height = chart["width"], chart["height"]
    right = float(chart["tick_label_right"])
    assert 0 <= right <= 100
    assert abs(right - (width - float(chart["tick_label_x"])) * 100 / width) <= 0.1
    for tick in chart["ticks"]:
        assert 0 <= float(tick["top"]) <= 100
        assert abs(float(tick["top"]) - float(tick["y"]) * 100 / height) <= 0.1
    for label in chart["labels"]:
        assert 0 <= float(label["left"]) <= 100
        assert abs(float(label["left"]) - float(label["x"]) * 100 / width) <= 0.1


@pytest.mark.django_db
def test_shipping_trend_label_places(shipping_data):
    trend = dashboard.shipping_trend(today=TODAY)
    for chart in (trend["qty_chart"], trend["amount_chart"]):
        _label_places_inside(chart)


@pytest.mark.django_db
def test_production_trend_label_places(production_data):
    _label_places_inside(dashboard.production_trend(today=TODAY)["chart"])


@pytest.mark.django_db
def test_inlet_chart_rows_match_html_rows(customer, floor_customer):
    # 注文の入口の図 (候補 C50 の 1 と 2): 名前と丸と受注の箱は HTML で、svg は線だけ。svg の y は px で、HTML の 1 行の高さ
    # (row_height) ごとに並ぶ。線は svg の左の端 (丸の右の端) から右の端 (箱の左の端) の、高さの中央 (箱の中央) へ。
    inlets = dashboard.order_inlets(TODAY)
    rows = inlets["rows"]
    assert len(rows) == 2
    row_height = float(inlets["row_height"])
    assert 0 < row_height <= 100
    assert float(rows[1]["y"]) - float(rows[0]["y"]) == row_height
    height = inlets["height"]
    assert height >= row_height * len(rows)
    for row in rows:
        assert (float(row["x1"]), float(row["x2"])) == (0, inlets["width"])
        assert 0 <= float(row["y"]) <= height
        assert float(row["y2"]) == height / 2


def _bar_center(bar):
    xs = [x for x, _y in _points(bar["d"])]
    return (max(xs) + min(xs)) / 2


@pytest.mark.django_db
def test_shipping_trend_keys_and_sums(shipping_data):
    trend = dashboard.shipping_trend(today=TODAY)
    months = {row["key"]: row for row in trend["months"]}
    assert [row["key"] for row in trend["months"]] == [
        "2026-06", "2026-07", "2026-08", "2026-09", "2026-10", "2026-11",
    ]
    assert [row["label"] for row in trend["months"]] == ["6 月", "7 月", "8 月", "9 月", "10 月", "11 月"]
    assert trend["is_empty"] is False

    # 5/31 の出荷は期間の外。7 月は 2 × 1000。
    assert months["2026-07"]["shipment_count"] == 1
    assert months["2026-07"]["shipment_qty"] == Decimal("2")
    assert months["2026-07"]["shipment_amount"] == Decimal("2000")
    # 11 月: 3 × 33.33 = 99.99 を切り捨てて 99 と、後で受注が取消になった出荷 1 × 9999。
    assert months["2026-11"]["shipment_count"] == 2
    assert months["2026-11"]["shipment_qty"] == Decimal("4")
    assert months["2026-11"]["shipment_amount"] == Decimal("99") + Decimal("9999")
    for key in ("2026-06", "2026-08", "2026-09", "2026-10"):
        assert months[key]["shipment_count"] == 0
        assert months[key]["shipment_qty"] == 0 and months[key]["shipment_amount"] == 0
    assert trend["max_qty"] == Decimal("4")
    assert trend["max_amount"] == Decimal("10098")


@pytest.mark.django_db
def test_shipping_trend_charts_inside_frame(shipping_data):
    trend = dashboard.shipping_trend(today=TODAY)
    label_x = {
        row["key"]: float(label["x"]) for row, label in zip(trend["months"], trend["qty_chart"]["labels"])
    }
    for chart, bars_name, max_value in (
        (trend["qty_chart"], "qty_bars", trend["max_qty"]),
        (trend["amount_chart"], "amount_bars", trend["max_amount"]),
    ):
        _frame_inside(chart)
        assert chart["top"] >= max_value
        bars = chart[bars_name]
        assert [bar["key"] for bar in bars] == ["2026-07", "2026-11"]
        _bars_inside(chart, bars)
        # 1 月に 1 本で、月の帯の中央にある。
        for bar in bars:
            assert abs(_bar_center(bar) - label_x[bar["key"]]) <= 1
    assert [tick["label"] for tick in trend["qty_chart"]["ticks"]] == ["0", "1", "2", "3", "4"]
    assert [tick["label"] for tick in trend["amount_chart"]["ticks"]] == ["0 万", "0.5 万", "1 万", "1.5 万"]


@pytest.mark.django_db
def test_shipping_trend_empty_and_missing(user, monkeypatch):
    trend = dashboard.shipping_trend(today=TODAY)
    assert trend["is_empty"] is True
    assert len(trend["months"]) == 6
    assert trend["qty_chart"]["qty_bars"] == [] and trend["amount_chart"]["amount_bars"] == []

    monkeypatch.setattr(dashboard, "apps", _NoParts())
    assert dashboard.shipping_trend(today=TODAY) is None


@pytest.fixture
def cutting(user):
    return masters_services.create_process({"code": "P01", "name": "作り物の切削", "sort_order": 1}, user)


@pytest.fixture
def inspection(user):
    return masters_services.create_process({"code": "P02", "name": "作り物の検査", "sort_order": 2}, user)


def _move_issued_on(workorder, day):
    """services は発行日を今日にするので、試験の中だけで発行日を動かす。"""
    WorkOrder._default_manager.filter(pk=workorder.pk).update(issued_on=day)


@pytest.fixture
def production_data(user, customer, shaft, cutting, inspection):
    """指示書 A (2 工程。10 月に発行)・B (1 工程。11 月)・C (取消)・D (期間の外) と、その実績。"""
    order = make_order(user, customer, shaft, [("100", "100", None)])
    line = line_of(order, 1)

    workorder_a = workorders_services.issue_workorder(
        line,
        Decimal("10"),
        [{"process": cutting, "planned_qty": Decimal("10")}, {"process": inspection, "planned_qty": Decimal("10")}],
        user,
    )
    _move_issued_on(workorder_a, date(2026, 10, 20))
    cut_a, check_a = workorder_a.steps.order_by("seq")
    # 切削は最後の工程でないので数えない。検査の 2 件は数える (00:30 の地域の時刻は UTC では 10/31)。
    progress_services.record_progress(cut_a, Decimal("10"), datetime(2026, 11, 1, 10, 0, tzinfo=TOKYO), user)
    progress_services.record_progress(check_a, Decimal("4"), datetime(2026, 11, 2, 10, 0, tzinfo=TOKYO), user)
    progress_services.record_progress(check_a, Decimal("3"), datetime(2026, 11, 1, 0, 30, tzinfo=TOKYO), user)

    workorder_b = workorders_services.issue_workorder(
        line, Decimal("5"), [{"process": cutting, "planned_qty": Decimal("5")}], user
    )
    _move_issued_on(workorder_b, date(2026, 11, 3))
    progress_services.record_progress(
        workorder_b.steps.get(), Decimal("5"), datetime(2026, 11, 3, 9, 0, tzinfo=TOKYO), user
    )

    workorder_c = workorders_services.issue_workorder(
        line, Decimal("7"), [{"process": cutting, "planned_qty": Decimal("7")}], user
    )
    _move_issued_on(workorder_c, date(2026, 11, 3))
    workorders_services.cancel_workorder(workorder_c, user, reason="作り物の取消")

    workorder_d = workorders_services.issue_workorder(
        line, Decimal("2"), [{"process": cutting, "planned_qty": Decimal("2")}], user
    )
    _move_issued_on(workorder_d, date(2026, 5, 31))


@pytest.mark.django_db
def test_production_trend_issued_and_completed(production_data):
    trend = dashboard.production_trend(today=TODAY)
    months = {row["key"]: row for row in trend["months"]}
    assert [row["key"] for row in trend["months"]] == [
        "2026-06", "2026-07", "2026-08", "2026-09", "2026-10", "2026-11",
    ]
    assert trend["has_progress"] is True and trend["is_empty"] is False

    assert months["2026-10"]["issued_count"] == 1
    assert months["2026-10"]["issued_qty"] == Decimal("10")
    assert months["2026-10"]["completed_qty"] == 0
    # 11 月: 発行は B だけ (C は取消)。完成は A の検査 4 + 3 と、B の切削 5 (B の最後の工程)。
    assert months["2026-11"]["issued_count"] == 1
    assert months["2026-11"]["issued_qty"] == Decimal("5")
    assert months["2026-11"]["completed_qty"] == Decimal("12")
    for key in ("2026-06", "2026-07", "2026-08", "2026-09"):
        assert months[key]["issued_count"] == 0
        assert months[key]["issued_qty"] == 0 and months[key]["completed_qty"] == 0
    assert trend["max_qty"] == Decimal("12")

    chart = trend["chart"]
    assert [bar["key"] for bar in chart["issued_bars"]] == ["2026-10", "2026-11"]
    assert [bar["key"] for bar in chart["completed_bars"]] == ["2026-11"]


@pytest.mark.django_db
def test_production_trend_chart_inside_frame(production_data):
    trend = dashboard.production_trend(today=TODAY)
    chart = trend["chart"]
    _frame_inside(chart)
    assert chart["top"] >= trend["max_qty"]
    _bars_inside(chart, chart["issued_bars"] + chart["completed_bars"])
    # 同じ月の 2 本は重ならず、左が発行・右が完成。
    issued = next(bar for bar in chart["issued_bars"] if bar["key"] == "2026-11")
    completed = next(bar for bar in chart["completed_bars"] if bar["key"] == "2026-11")
    assert max(x for x, _y in _points(issued["d"])) < min(x for x, _y in _points(completed["d"]))
    # 一番上が 1 万未満なので、目盛りの字は 3 桁区切りの全部。
    assert [tick["label"] for tick in chart["ticks"]] == ["0", "5", "10", "15"]


class _NoProgress:
    """進捗の部品だけが入っていない会社の apps の代わり (ほかは本物に回す)。"""

    def get_model(self, app_label, name):
        if (app_label, name) == ("progress", "Progress"):
            raise LookupError(f"{app_label}.{name}")
        return django_apps.get_model(app_label, name)


@pytest.mark.django_db
def test_production_trend_empty_and_missing(user, monkeypatch):
    trend = dashboard.production_trend(today=TODAY)
    assert trend["is_empty"] is True
    assert len(trend["months"]) == 6
    assert trend["chart"]["issued_bars"] == [] and trend["chart"]["completed_bars"] == []

    monkeypatch.setattr(dashboard, "apps", _NoProgress())
    trend = dashboard.production_trend(today=TODAY)
    assert trend["has_progress"] is False
    assert all(row["completed_qty"] is None for row in trend["months"])
    assert trend["chart"]["completed_bars"] == []

    monkeypatch.setattr(dashboard, "apps", _NoParts())
    assert dashboard.production_trend(today=TODAY) is None


@pytest.mark.django_db
def test_without_amounts(shipping_data):
    trend = dashboard.shipping_trend(today=TODAY)
    hidden = dashboard.without_amounts(trend)
    assert hidden["amount_chart"] is None and hidden["max_amount"] is None
    assert all(row["shipment_amount"] is None for row in hidden["months"])
    assert hidden["qty_chart"] == trend["qty_chart"] and hidden["max_qty"] == trend["max_qty"]
    assert [row["shipment_qty"] for row in hidden["months"]] == [row["shipment_qty"] for row in trend["months"]]
    # 元の辞書は変わらない。
    assert trend["amount_chart"] is not None and trend["max_amount"] == Decimal("10098")
    assert trend["months"][-1]["shipment_amount"] == Decimal("10098")
    assert dashboard.without_amounts(None) is None
