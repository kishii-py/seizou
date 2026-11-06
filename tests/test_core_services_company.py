"""会社の行を読むだけの口 (core の services.company_or_none) と、会社の端数の方法を 1 回だけ読む形 (N176)。

company_or_none は読むだけで行を作らない。company は今までどおり、無ければ作る。
round_amount の rounding (キーワード) は、取引先の方法が空のときだけ使い、会社の行を読まない。
月次の推移と出荷の推移は、出荷の行の数によらず会社の行 (core_company) を 1 回以下しか読まず、金額は今までと同じ。
日は today を渡して固定する。名前は作り物だけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from seizou.core import dashboard, services
from seizou.core.models import Company
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

TODAY = date(2026, 11, 3)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


def _customer(user, code, rounding=""):
    return masters_services.create_partner(
        {"code": code, "name": f"作り物の得意先 {code}", "role": "customer", "rounding": rounding}, user
    )


def _company_queries(captured):
    return [query["sql"] for query in captured.captured_queries if "core_company" in query["sql"]]


@pytest.mark.django_db
def test_company_or_none_reads_without_creating():
    assert services.company_or_none() is None
    assert masters_services.company_rounding() == "round"
    assert not Company.objects.exists()


@pytest.mark.django_db
def test_company_or_none_returns_first_row():
    first = Company.objects.create(name="作り物の製作所")
    Company.objects.create(name="作り物の別の製作所")
    assert services.company_or_none() == first
    assert services.company() == first
    assert Company.objects.count() == 2


@pytest.mark.django_db
def test_company_still_creates_when_missing():
    row = services.company()
    assert row.name == "会社"
    assert Company.objects.count() == 1
    assert services.company_or_none() == row
    assert services.company() == row
    assert Company.objects.count() == 1


@pytest.mark.django_db
def test_round_amount_rounding_keyword(user):
    # 124.5 は四捨五入と切上で 125、切捨で 124。
    Company.objects.create(name="作り物の製作所", rounding="ceil")
    plain = _customer(user, "C001")
    rounded = _customer(user, "C002", "round")
    value = Decimal("124.5")
    assert masters_services.round_amount(rounded, value, rounding="floor") == Decimal("125")  # 取引先の方法が先
    with CaptureQueriesContext(connection) as captured:
        assert masters_services.round_amount(plain, value, rounding="floor") == Decimal("124")
    assert not _company_queries(captured)  # 渡したときは会社の行を読まない
    assert masters_services.round_amount(plain, value) == Decimal("125")  # 渡さなければ今までどおり会社の方法 (切上)
    assert masters_services.company_rounding() == "ceil"


@pytest.fixture
def seed_shipments(user):
    """種の出荷 4 件 (推移の期間 6 月〜11 月の中)。端数の方法の無い得意先に 3 件、切捨の得意先に 1 件。

    会社の方法は切上。単価に端数があるので、会社の方法で丸めた事が金額に出る (3 × 10.10 = 30.30 → 31)。
    返す物 = [(得意先, 出荷日, 数量 × 単価), ...]。
    """
    Company.objects.create(name="作り物の製作所", rounding="ceil")
    plain = _customer(user, "C001")
    floor = _customer(user, "C002", "floor")
    shaft = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    shipped = []
    for customer, order_date, shipped_at, qty, price in (
        (plain, date(2026, 6, 1), date(2026, 7, 15), "3", "10.10"),
        (plain, date(2026, 8, 1), date(2026, 8, 20), "2", "33.33"),
        (plain, date(2026, 11, 1), date(2026, 11, 2), "1", "0.50"),
        (floor, date(2026, 11, 2), date(2026, 11, 3), "3", "33.33"),
    ):
        order = orders_services.create_order(
            customer,
            [{"item": shaft, "qty": Decimal(qty), "unit_price": Decimal(price)}],
            user,
            order_date=order_date,
        )
        shipping_services.ship(order.lines.get(line_no=1), Decimal(qty), shipped_at, user)
        shipped.append((customer, shipped_at, Decimal(qty) * Decimal(price)))
    return shipped


@pytest.mark.django_db
@pytest.mark.parametrize("trend", [dashboard.monthly_trend, dashboard.shipping_trend])
def test_trends_read_company_rounding_once(seed_shipments, trend):
    expected = {}
    for customer, shipped_at, value in seed_shipments:
        key = f"{shipped_at.year:04d}-{shipped_at.month:02d}"
        expected[key] = expected.get(key, Decimal("0")) + masters_services.round_amount(customer, value)
    assert expected == {"2026-07": Decimal("31"), "2026-08": Decimal("67"), "2026-11": Decimal("100")}

    with CaptureQueriesContext(connection) as captured:
        result = trend(today=TODAY)
    assert sum(row["shipment_count"] for row in result["months"]) == 4
    assert {row["key"]: row["shipment_amount"] for row in result["months"] if row["shipment_count"]} == expected
    assert len(_company_queries(captured)) <= 1
