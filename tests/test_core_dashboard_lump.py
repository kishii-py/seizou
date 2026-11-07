"""ダッシュボードの月次の推移と出荷の推移の出荷の金額が、一式の明細を 1 回だけ数えること (決め PRICE-1c)。

一式 (単価の単位が式) の明細は、明細を出し終えた出荷の月に一式の全額が入り、それまでの出荷の月は 0。
部品の単位の明細は今と同じ数量 × 単価を丸めた値で足される。式は shipping.services.amount_of_shipment の 1 か所。
作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest

from seizou.core import dashboard
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

TODAY = date(2026, 11, 20)


@pytest.mark.django_db
def test_lump_amount_is_counted_once_in_the_month_of_the_completing_shipment(user):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [
            {"item": item, "qty": Decimal("300"), "unit_price": Decimal("50000"), "price_basis": "lump"},
            {"item": item, "qty": Decimal("10"), "unit_price": Decimal("120"), "price_basis": "unit"},
        ],
        user,
        order_date=date(2026, 10, 1),
    )
    lump, unit = order.lines.order_by("line_no")
    shipping_services.ship(lump, Decimal("100"), date(2026, 10, 10), user)
    shipping_services.ship(unit, Decimal("10"), date(2026, 10, 12), user)
    shipping_services.ship(lump, Decimal("200"), date(2026, 11, 5), user)

    monthly = {row["key"]: row for row in dashboard.monthly_trend(today=TODAY)["months"]}
    assert monthly["2026-10"]["shipment_count"] == 2
    assert monthly["2026-10"]["shipment_amount"] == Decimal("1200")
    assert monthly["2026-11"]["shipment_amount"] == Decimal("50000")

    trend = {row["key"]: row for row in dashboard.shipping_trend(today=TODAY)["months"]}
    assert trend["2026-10"]["shipment_amount"] == Decimal("1200")
    assert trend["2026-10"]["shipment_qty"] == Decimal("110")
    assert trend["2026-11"]["shipment_amount"] == Decimal("50000")
