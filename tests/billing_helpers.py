"""請求の試験 (tests/test_billing_*・test_r09) が共通に使う道具。

納品書の出た出荷を、services を通して作る。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

from seizou.shipping import services as shipping_services

SHIPPED_AT = date(2026, 11, 5)


def shipped(user, customer, line, qty, shipped_at=SHIPPED_AT):
    """納品書の出た出荷 (出荷済み) を 1 つ作る。"""
    shipment = shipping_services.ship(line, Decimal(qty), shipped_at, user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    return shipment
