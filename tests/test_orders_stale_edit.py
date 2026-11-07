"""受注の編集で、画面を開いた後にほかの人か取引先の変更で直った受注を、古い画面の保存で黙って元に戻さないこと (N350。指摘 money-1)。

services.order_state_token (開いた時の受注と明細の字) と、update_order の expected_state を見る。
得意先・部品・協力企業は作り物。得意先の fixture は tests/test_connect_revision_accept.py の物を使う。
取引先から受け入れる変更を通す試験では、相手の会社への HTTP は requests.post を偽の物に差し替える (同じファイルの fixture)。
"""

import re
from datetime import date
from decimal import Decimal

import pytest

from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import Order
from seizou.shipping import services as shipping_services
from tests.test_connect_revision_accept import customer  # noqa: F401 (fixture)

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
HEX64 = re.compile(r"[0-9a-f]{64}")


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-900", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def order(user, customer, shaft):  # noqa: F811
    """明細 2 行 (数 10・単価 1180 と、数 5・単価 300) の受注。"""
    return services.create_order(
        customer,
        [
            {"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("1180")},
            {"item": shaft, "qty": Decimal("5"), "unit_price": Decimal("300")},
        ],
        user,
        order_date=ORDER_DATE,
    )


def _lines(order):
    return list(order.lines.order_by("line_no"))


def _change_qty(order, user, qty):
    """ほかの人の保存の役: 明細 1 の数だけを qty に直す (expected_state を渡さない)。"""
    first, second = _lines(order)
    services.update_order(order, {}, [{"id": first.pk, "qty": Decimal(qty)}, {"id": second.pk}], user)


# ---------------------------------------------------------------- (a) 字の作り方


@pytest.mark.django_db
def test_token_changes_with_qty_but_not_with_shipping_or_no_bill(user, order):
    token = services.order_state_token(order)
    assert HEX64.fullmatch(token)
    first, second = _lines(order)
    # 渡した明細の並びに依らない (画面は行番号の順で読んだ並びを渡す)
    assert services.order_state_token(order, [second, first]) == token

    # 出荷で受注の状態が変わっただけでは変わらない
    shipping_services.ship(first, Decimal("4"), SHIPPED_AT, user)
    order.refresh_from_db()
    assert order.status == Order.STATUS_PARTIAL
    assert services.order_state_token(order) == token
    # 請求しない印を付けただけでは変わらない
    services.mark_no_bill(first, user)
    assert services.order_state_token(order) == token

    _change_qty(order, user, "12")
    order.refresh_from_db()
    assert services.order_state_token(order) != token


@pytest.mark.django_db
def test_token_is_keyed_and_does_not_show_prices(settings, order):
    """字は鍵のある hmac (sha256 の 16 進 64 字)。単価の字を含まず、鍵が違えば字も違う (隠しの欄の値から単価を割り出せない)。"""
    token = services.order_state_token(order)
    assert HEX64.fullmatch(token)
    assert "1180.00" not in token and "1,180" not in token
    settings.SECRET_KEY = "seizou-tests-only-another-plain-key"
    assert services.order_state_token(order) != token


# ---------------------------------------------------------------- (b)〜(d) update_order の expected_state


@pytest.mark.django_db
def test_stale_token_refuses_without_writing(user, order):
    opened = services.order_state_token(order)
    _change_qty(order, user, "12")
    count = ChangeLog.objects.count()
    first, second = _lines(order)

    # 古い画面の保存の役: 数は開いた時の 10 のまま送り、備考だけ直す
    with pytest.raises(ValueError) as caught:
        services.update_order(
            order,
            {"note": "古い画面で書いた備考"},
            [{"id": first.pk, "qty": Decimal("10")}, {"id": second.pk}],
            user,
            expected_state=opened,
        )
    assert str(caught.value) == services.STALE_ORDER_MESSAGE
    first.refresh_from_db()
    order.refresh_from_db()
    assert first.qty == Decimal("12")
    assert order.note == ""
    assert ChangeLog.objects.count() == count


@pytest.mark.django_db
def test_without_token_saves_as_before(user, order):
    """expected_state を渡さない呼び (取引先の変更の受け入れと同じ形) と空の字は、今のまま通る。"""
    services.order_state_token(order)
    _change_qty(order, user, "12")
    first, second = _lines(order)

    services.update_order(order, {"note": "渡さない"}, [{"id": first.pk}, {"id": second.pk}], user)
    order.refresh_from_db()
    assert order.note == "渡さない"
    services.update_order(order, {"note": "空の字"}, [{"id": first.pk}, {"id": second.pk}], user, expected_state="")
    order.refresh_from_db()
    assert order.note == "空の字"


@pytest.mark.django_db
def test_matching_token_saves(user, order):
    """開いてから誰も直していなければ、字が合って保存できる。"""
    opened = services.order_state_token(order)
    first, second = _lines(order)
    services.update_order(
        order,
        {"note": "開いてから誰も直していない"},
        [{"id": first.pk, "qty": Decimal("11")}, {"id": second.pk}],
        user,
        expected_state=opened,
    )
    first.refresh_from_db()
    order.refresh_from_db()
    assert first.qty == Decimal("11")
    assert order.note == "開いてから誰も直していない"
