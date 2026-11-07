"""ダッシュボードの「今月の◯◯」のカードの、先月の同じ日までとの差と今月の日ごとの細い線 (core.dashboard の same_day_last_month・sparkline・
*_month_compare と、views.dashboard_stats の compare、core/home.html の札 (core/_stat_delta.html) と線)。
本人の決め 11-07 18:2x〜18:4x「2もいい」。受け渡しの seizou-部品の6案-20261107/指示書.md の 3。

作り物のデータは services で作る (受注は tests/test_core_dashboard_cards.py、受入は _payable.py、入金は _payments.py の鎖と同じ形)。
dashboard の関数には固定の日を渡し、画面の試験は今日 (timezone.localdate) から数える。名前は作り物だけ。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.billing import services as billing_services
from seizou.core import dashboard, views
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from seizou.shipping import services as shipping_services

#: 数字のカードの題の <p> の開き (core/home.html。tests/test_core_dashboard_payable.py の card_of と同じ切り方)
TITLE_HEAD = '<p class="text-xs font-medium text-base-content/70">'
THIS_MONTH_LABELS = ("今月の受注", "今月の支払の見込み", "今月の入金")


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def no_http(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer", "closing_day": 20, "payment_month": "next", "payment_day": 25},
        user,
    )


@pytest.fixture
def suppliers(user):
    return [
        masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "rounding": "round"}, user),
        masters_services.create_partner({"code": "S002", "name": "作り物の別の協力企業", "role": "subcontractor", "rounding": "round"}, user),
    ]


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def make_order(user, customer, item, order_date, *, cancelled=False):
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("1"), "unit_price": Decimal("100")}], user, order_date=order_date
    )
    if cancelled:
        orders_services.cancel_order(order, user)
    return order


def purchase_line(partner, item, user, unit_price):
    order = purchasing_services.create_purchase_order(
        partner, [{"item": item, "qty": Decimal("100"), "unit_price": Decimal(unit_price)}], user, order_date=date(2026, 9, 1)
    )
    return order.lines.get()


def make_invoice(user, customer, item):
    """出荷 4 と 6 の請求書 (tests/test_core_dashboard_payments.py の invoice と同じ鎖)。"""
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}], user, order_date=date(2026, 9, 2)
    )
    line = order.lines.get()
    shipments = []
    for qty in ("4", "6"):
        shipment = shipping_services.ship(line, Decimal(qty), date(2026, 9, 5), user)
        shipping_services.create_delivery_note(customer, [shipment], user)
        shipment.refresh_from_db()
        shipments.append(shipment)
    return billing_services.create_invoice(customer, shipments, user, issued_on=date(2026, 9, 30))


def home(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    return response.content.decode()


def card_of(body, label):
    start = body.index(f"{TITLE_HEAD}{label}</p>")
    end = body.find(TITLE_HEAD, start + len(TITLE_HEAD))
    return body[start : end if end != -1 else body.index('data-tour="flow-guide"', start)]


def points_of(svg):
    return re.search(r'points="([^"]*)"', svg).group(1).split()


class _NoParts:
    def get_model(self, app_label, name):
        raise LookupError(f"{app_label}.{name}")


# ---- 先月の同じ日と細い線の座標 ----


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 11, 7), date(2026, 10, 7)),
        (date(2026, 11, 1), date(2026, 10, 1)),
        (date(2026, 1, 15), date(2025, 12, 15)),
        (date(2026, 3, 31), date(2026, 2, 28)),  # 先月に 31 日が無い = 末日
        (date(2028, 3, 31), date(2028, 2, 29)),  # 閏年
        (date(2026, 5, 31), date(2026, 4, 30)),
    ],
)
def test_same_day_last_month(today, expected):
    assert dashboard.same_day_last_month(today) == expected


def test_sparkline_scales_values_into_the_box():
    spark = dashboard.sparkline([1, 2, 4])
    assert (spark["width"], spark["height"]) == (100, 24)
    # 左から右へ等間隔 (2・50・98)。一番大きい値が上の端 (y = 2)、0 は下の端 (y = 22)、1 は 4 の 1/4 の高さ。
    assert spark["points"] == "2.0,17.0 50.0,12.0 98.0,2.0"
    assert dashboard.sparkline([0, 0, 0])["points"] == "2.0,22.0 50.0,22.0 98.0,22.0"
    # 日が 1 つ (月の 1 日) は、その値の高さで左から右への平らな線。値が無ければ下の端。
    assert dashboard.sparkline([3])["points"] == "2.0,2.0 98.0,2.0"
    assert dashboard.sparkline([])["points"] == "2.0,22.0 98.0,22.0"
    assert dashboard.sparkline([Decimal("0"), Decimal("450")])["points"] == "2.0,22.0 98.0,2.0"


# ---- 受注 (件数) ----


@pytest.mark.django_db
def test_orders_month_compare_counts_this_month_to_date_against_last_month_same_day(user, customer, shaft):
    for day in (date(2026, 11, 1), date(2026, 11, 1), date(2026, 11, 7), date(2026, 10, 3), date(2026, 10, 7)):
        make_order(user, customer, shaft, day)
    make_order(user, customer, shaft, date(2026, 11, 20))  # 今日より後 = 差と線に入らない
    make_order(user, customer, shaft, date(2026, 10, 20))  # 先月の同じ日より後 = 入らない
    make_order(user, customer, shaft, date(2026, 11, 2), cancelled=True)  # 取消 = 入らない

    compare = dashboard.orders_month_compare(date(2026, 11, 7))

    assert (compare["this"], compare["last"], compare["delta"], compare["delta_abs"]) == (3, 2, 1, 1)
    assert compare["direction"] == "up"
    assert compare["until"] == date(2026, 10, 7)
    assert compare["days"] == [2, 0, 0, 0, 0, 0, 1]
    assert len(compare["spark"]["points"].split()) == 7
    assert compare["spark"]["points"].split()[0] == "2.0,2.0" and compare["spark"]["points"].split()[-1] == "98.0,12.0"


@pytest.mark.django_db
def test_orders_month_compare_down_and_same(user, customer, shaft):
    for day in (date(2026, 9, 5), date(2026, 9, 5), date(2026, 9, 5), date(2026, 10, 2)):
        make_order(user, customer, shaft, day)

    down = dashboard.orders_month_compare(date(2026, 10, 7))
    assert (down["this"], down["last"], down["delta"], down["delta_abs"], down["direction"]) == (1, 3, -2, 2, "down")
    assert down["days"] == [0, 1, 0, 0, 0, 0, 0]

    make_order(user, customer, shaft, date(2026, 10, 7))
    make_order(user, customer, shaft, date(2026, 10, 4))
    same = dashboard.orders_month_compare(date(2026, 10, 7))
    assert (same["this"], same["last"], same["delta"], same["direction"]) == (3, 3, 0, "same")


@pytest.mark.django_db
def test_orders_month_compare_on_the_31st_compares_until_last_month_end(user, customer, shaft):
    for day in (date(2026, 2, 27), date(2026, 2, 28), date(2026, 3, 1)):
        make_order(user, customer, shaft, day)

    compare = dashboard.orders_month_compare(date(2026, 3, 31))

    assert compare["until"] == date(2026, 2, 28)
    assert (compare["this"], compare["last"], compare["direction"]) == (1, 2, "down")
    assert len(compare["days"]) == 31 and compare["days"][0] == 1


@pytest.mark.django_db
def test_month_compare_is_none_without_parts(monkeypatch):
    monkeypatch.setattr(dashboard, "apps", _NoParts())
    assert dashboard.orders_month_compare(date(2026, 11, 7)) is None
    assert dashboard.payable_month_compare(date(2026, 11, 7)) is None
    assert dashboard.payments_month_compare(date(2026, 11, 7)) is None


# ---- 受入と入金 (金額) ----


@pytest.mark.django_db
def test_payable_month_compare_sums_receipt_amounts_per_day(user, suppliers, shaft, no_http):
    first = purchase_line(suppliers[0], shaft, user, "45")
    second = purchase_line(suppliers[1], shaft, user, "60")
    purchasing_services.receive(first, Decimal("10"), date(2026, 11, 1), user)  # 450
    purchasing_services.receive(second, Decimal("40"), date(2026, 11, 7), user)  # 2400
    purchasing_services.receive(first, Decimal("20"), date(2026, 10, 7), user)  # 900 (先月の同じ日 = 入る)
    purchasing_services.receive(first, Decimal("30"), date(2026, 10, 8), user)  # 先月の同じ日より後 = 入らない

    compare = dashboard.payable_month_compare(date(2026, 11, 7))

    assert (compare["this"], compare["last"], compare["delta"]) == (Decimal("2850"), Decimal("900"), Decimal("1950"))
    assert compare["direction"] == "up"
    assert compare["days"] == [Decimal("450"), 0, 0, 0, 0, 0, Decimal("2400")]
    assert isinstance(compare["days"][1], Decimal)


@pytest.mark.django_db
def test_payments_month_compare_sums_recorded_payments_per_day(user, customer, shaft):
    invoice = make_invoice(user, customer, shaft)
    billing_services.record_payment(invoice, date(2026, 11, 1), Decimal("6500"), user)
    billing_services.record_payment(invoice, date(2026, 11, 7), Decimal("1000"), user, method="cash")
    billing_services.record_payment(invoice, date(2026, 10, 7), Decimal("3000"), user)
    billing_services.record_payment(invoice, date(2026, 10, 8), Decimal("500"), user)  # 先月の同じ日より後 = 入らない
    cancelled = billing_services.record_payment(invoice, date(2026, 11, 5), Decimal("500"), user)
    billing_services.cancel_payment(cancelled, user)  # 取消 = 入らない

    compare = dashboard.payments_month_compare(date(2026, 11, 7))

    assert (compare["this"], compare["last"], compare["delta"], compare["direction"]) == (Decimal("7500"), Decimal("3000"), Decimal("4500"), "up")
    assert compare["days"] == [Decimal("6500"), 0, 0, 0, 0, 0, Decimal("1000")]


# ---- カードの並び (dashboard_stats) ----


@pytest.mark.django_db
def test_stats_attach_compare_and_help_only_to_this_month_cards(user, customer, shaft, monkeypatch):
    monkeypatch.setattr(views.timezone, "localdate", lambda: date(2026, 11, 7))
    make_order(user, customer, shaft, date(2026, 11, 3))

    stats = {stat["label"]: stat for stat in views.dashboard_stats()}

    for label in THIS_MONTH_LABELS:
        assert stats[label]["compare"] is not None, label
        assert stats[label]["help"].endswith(views.COMPARE_HELP), label
        assert len(stats[label]["compare"]["days"]) == 7, label
    assert stats["今月の受注"]["compare"]["direction"] == "up"
    assert stats["今月の支払の見込み"]["compare"]["direction"] == "same"
    for label, stat in stats.items():
        if label not in THIS_MONTH_LABELS:
            assert "compare" not in stat, label
            assert views.COMPARE_HELP not in stat["help"], label


@pytest.mark.django_db
def test_stats_without_compare_keep_the_plain_help(monkeypatch):
    monkeypatch.setattr(views.timezone, "localdate", lambda: date(2026, 11, 7))
    monkeypatch.setattr(dashboard, "orders_month_compare", lambda today=None: None)

    (stat,) = [stat for stat in views.dashboard_stats() if stat["label"] == "今月の受注"]

    assert stat["compare"] is None
    assert stat["help"] == "受注日が今月の受注の件数です。取消は数えません。"


# ---- 画面 ----


@pytest.mark.django_db
def test_home_shows_delta_badge_and_sparkline_on_this_month_cards(user, logged_in, customer, shaft):
    today = timezone.localdate()
    make_order(user, customer, shaft, today)
    make_order(user, customer, shaft, today)
    make_order(user, customer, shaft, dashboard.same_day_last_month(today))

    body = home(logged_in)
    card = card_of(body, "今月の受注")

    # 数の形は今まで (tests/test_core_home_closed_links.py が見る) のまま。札は数の横、線は数の下。
    assert '<p class="mt-2"><span class="text-2xl font-bold tabular-nums sm:text-3xl">2</span> <span class="text-base-content/60">件</span> <span class="badge badge-soft badge-success badge-sm' in card
    assert 'data-stat-delta="up"' in card and "▲ +1 先月より" in card
    svg = re.search(r"<svg [^>]*data-stat-spark[^>]*>.*?</svg>", card, re.S).group(0)
    assert f'data-stat-spark="{today.day}"' in svg
    assert 'preserveAspectRatio="none"' in svg and 'vector-effect="non-scaling-stroke"' in svg
    assert len(points_of(svg)) == max(today.day, 2)
    assert "#" not in re.search(r'<polyline [^>]*class="([^"]*)"', svg).group(1)  # 色は theme の class だけ
    assert card.index("</p>") < card.index("<svg ")  # 札は数の段落の中、線はその後

    # 今月でないカードには札も線も無い。
    for label in ("部品", "取引先", "出荷の残りがある明細", "手入力せずに取り込んだ明細"):
        plain = card_of(body, label)
        assert "data-stat-delta" not in plain and "data-stat-spark" not in plain and "先月より" not in plain, label


@pytest.mark.django_db
def test_home_money_card_delta_uses_yen_and_hides_with_money(user, logged_in, suppliers, shaft, no_http, settings):
    today = timezone.localdate()
    first = purchase_line(suppliers[0], shaft, user, "60")
    second = purchase_line(suppliers[1], shaft, user, "45")
    purchasing_services.receive(first, Decimal("40"), today, user)  # 2400
    purchasing_services.receive(second, Decimal("100"), dashboard.same_day_last_month(today), user)  # 4500

    card = card_of(home(logged_in), "今月の支払の見込み")
    assert 'data-stat-delta="down"' in card and "▼ −¥2,100 先月より" in card
    assert '<span class="badge badge-soft badge-error badge-sm' in card
    assert "data-stat-spark" in card

    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}
    body = home(logged_in)
    assert "今月の支払の見込み" not in body and "¥2,100" not in body
    # 件数のカードの札は金額を見られない人にも出る。
    assert "data-stat-delta" in card_of(body, "今月の受注")


@pytest.mark.django_db
def test_home_same_delta_is_grey(logged_in):
    card = card_of(home(logged_in), "今月の受注")
    assert 'data-stat-delta="same"' in card and "± 0 先月より" in card
    assert '<span class="badge badge-ghost badge-sm' in card
