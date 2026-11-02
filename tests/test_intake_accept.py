"""intake の受注にする (accept_extracted) のテスト。受信箱の受け入れと同じ形 (R10 と同じ考え方)。

人が品目を選んで受注にするまで受注にならず、2 回押しても受注は 1 件。
本物の API は呼ばない (autouse の fixture が _ask_claude を「呼ばれたら AssertionError」に差し替える)。
読み取りの記録は、作り物の返事を返す関数に差し替えてから extract_order で作る。品番と会社名は作り物。
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from seizou.intake import services
from seizou.intake.models import Extraction
from seizou.masters.models import PartnerItem
from seizou.masters.services import create_item, create_partner, map_partner_item, resolve_partner_item
from seizou.orders.models import Order
from tests.test_intake_extract import FAKE_KEY, refuse_api, reply_data, upload


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(services, "_ask_claude", refuse_api)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return create_partner({"code": "IA01", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return create_item(
        {"part_no": "S-100", "name": "作り物のシャフト", "unit": "個", "standard_price": Decimal("150")},
        user,
    )


@pytest.fixture
def bracket(user):
    return create_item(
        {"part_no": "B-200", "name": "作り物のブラケット", "unit": "個", "standard_price": Decimal("240")},
        user,
    )


@pytest.fixture
def read_order(monkeypatch, customer, user):
    """作り物の返事 (data を渡せば、その辞書) で読み取りの記録を 1 つ作って返す。"""

    def _read(data=None):
        text = json.dumps(data if data is not None else reply_data(), ensure_ascii=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
        monkeypatch.setattr(services, "_ask_claude", lambda model, block: text)
        try:
            return services.extract_order(upload(), customer, user)
        finally:
            monkeypatch.setattr(services, "_ask_claude", refuse_api)

    return _read


def test_accept_creates_order(read_order, shaft, bracket, user, customer):
    extraction = read_order()

    order = services.accept_extracted(extraction, {"X-100": shaft, "X-200": bracket}, user)

    assert Order.objects.count() == 1
    assert order.source == Order.SOURCE_INTAKE
    assert order.customer == customer
    assert order.customer_order_no == "TEST-PO-001"
    assert order.order_date == date(2026, 11, 2)
    lines = list(order.lines.order_by("line_no"))
    assert [(line.item, line.customer_part_no) for line in lines] == [(shaft, "X-100"), (bracket, "X-200")]
    assert [line.qty for line in lines] == [Decimal("10"), Decimal("5")]
    assert [line.due_date for line in lines] == [date(2026, 11, 20), date(2026, 11, 27)]
    assert lines[0].unit_price == Decimal("120")
    assert lines[0].unit == "個"
    extraction.refresh_from_db()
    assert extraction.status == Extraction.STATUS_ORDERED
    assert extraction.order == order


def test_accept_twice_is_refused(read_order, shaft, bracket, user):
    extraction = read_order()
    mapping = {"X-100": shaft, "X-200": bracket}
    order = services.accept_extracted(extraction, mapping, user)

    with pytest.raises(ValueError) as caught:
        services.accept_extracted(extraction, mapping, user)

    assert order.order_no in str(caught.value)
    assert Order.objects.count() == 1


def test_accept_uses_partner_item_table(read_order, shaft, bracket, user, customer):
    map_partner_item(customer, shaft, "X-100", user)
    map_partner_item(customer, bracket, "X-200", user)
    extraction = read_order()

    order = services.accept_extracted(extraction, {}, user)

    assert [line.item for line in order.lines.order_by("line_no")] == [shaft, bracket]
    assert PartnerItem.objects.count() == 2


def test_accept_adds_mapping(read_order, shaft, bracket, user, customer):
    extraction = read_order()
    assert PartnerItem.objects.count() == 0

    services.accept_extracted(extraction, {"X-100": shaft, "X-200": bracket}, user)

    assert PartnerItem.objects.count() == 2
    assert resolve_partner_item(customer, "X-100") == shaft
    assert PartnerItem.objects.get(partner_part_no="X-200").partner_item_name == "作り物のブラケット"


def test_accept_without_item_is_value_error(read_order, shaft, user):
    extraction = read_order()

    with pytest.raises(ValueError) as caught:
        services.accept_extracted(extraction, {"X-100": shaft}, user)

    assert "X-200" in str(caught.value)
    assert Order.objects.count() == 0
    # 先に足した X-100 の対応も、同じトランザクションで戻る。
    assert PartnerItem.objects.count() == 0
    extraction.refresh_from_db()
    assert extraction.status == Extraction.STATUS_EXTRACTED


def test_accept_price_falls_back_to_standard_price(read_order, shaft, bracket, user):
    extraction = read_order()

    order = services.accept_extracted(extraction, {"X-100": shaft, "X-200": bracket}, user)

    line = order.lines.get(customer_part_no="X-200")
    assert line.unit_price == bracket.standard_price


def test_accept_without_price_is_value_error(read_order, shaft, user):
    no_price = create_item({"part_no": "N-300", "name": "作り物の単価なし", "unit": "個"}, user)
    extraction = read_order()

    with pytest.raises(ValueError) as caught:
        services.accept_extracted(extraction, {"X-100": shaft, "X-200": no_price}, user)

    assert "X-200" in str(caught.value) and "単価" in str(caught.value)
    assert Order.objects.count() == 0


def test_accept_without_order_date_uses_read_date(read_order, shaft, bracket, user):
    data = reply_data()
    data["order_date"] = None
    extraction = read_order(data)

    order = services.accept_extracted(extraction, {"X-100": shaft, "X-200": bracket}, user)

    assert order.order_date == timezone.localtime(extraction.read_at).date()
