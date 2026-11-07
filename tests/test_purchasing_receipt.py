"""受入の services (協力企業からの受入・受入の数と残・支払の見込み・送らない発注)。

HTTP は no_http で止め、つながりの無い協力企業で発注を作る (送らない発注の試験だけ偽の requests.post を使う)。
取引先と品目は作り物。日は固定の値を渡す。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.connect import services as connect_services
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder, PurchaseOrderLine, Receipt
from seizou.stock import services as stock_services
from seizou.stock.models import KIND_RECEIVED, StockMove
from tests.test_connect_signing import _new_secret

ORDER_DATE = date(2026, 10, 28)
DAY = date(2026, 11, 2)
ENV_NAME = "SEIZOU_PARTNER_TESTSUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"


class FakeResponse:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


class FakePost:
    def __init__(self):
        self.calls = 0

    def __call__(self, url, data=None, headers=None, timeout=None, **kwargs):
        self.calls += 1
        return FakeResponse(200, {"status": "received"})


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "rounding": "round"}, user
    )


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user),
    ]


@pytest.fixture
def no_http(monkeypatch):
    """HTTP を呼んだら落とす。つながりが無い発注は送らないことを見る。"""

    def fail(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


def _order(partner, user, *lines):
    """lines の 1 つは (品目, 数量, 単価)。"""
    return services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(price)} for item, qty, price in lines],
        user,
        order_date=ORDER_DATE,
    )


@pytest.mark.django_db
def test_receive_creates_row_amount_and_stock_move(user, subcontractor, items, no_http):
    po = _order(subcontractor, user, (items[0], "10", "25.5"))
    line = po.lines.get()

    receipt = services.receive(line, Decimal("4"), DAY, user, note="一部")

    assert Receipt.objects.count() == 1
    assert receipt.amount == Decimal("102")  # 4 × 25.5 = 102 (四捨五入の取引先)
    assert receipt.received_by == user
    assert receipt.received_on == DAY and receipt.note == "一部"
    move = StockMove.objects.get()
    assert move.kind == KIND_RECEIVED and move.qty == Decimal("4")
    assert move.source == receipt
    assert move.note == "一部"
    assert stock_services.balance(items[0]) == Decimal("4")
    assert services.received_qty(line) == Decimal("4")
    assert services.remaining_qty(line) == Decimal("6")


@pytest.mark.django_db
def test_receive_marks_delivered_when_all_lines_full(user, subcontractor, items, no_http):
    po = _order(subcontractor, user, (items[0], "10", "5"), (items[1], "3", "7"))
    first, second = po.lines.order_by("line_no")

    services.receive(first, Decimal("10"), DAY, user)
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_DRAFT
    services.receive(second, Decimal("1"), DAY, user)
    services.receive(second, Decimal("2"), DAY, user)
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_DELIVERED

    services.receive(second, Decimal("1"), DAY, user)  # もう 1 度入れても「納品された」のまま
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_DELIVERED

    sent = _order(subcontractor, user, (items[0], "2", "5"))
    services.mark_sent(sent, user)
    services.receive(sent.lines.get(), Decimal("2"), DAY, user)
    assert PurchaseOrder.objects.get(pk=sent.pk).status == PurchaseOrder.STATUS_DELIVERED


@pytest.mark.django_db
def test_receive_refuses_cancelled_and_zero(user, subcontractor, items, no_http):
    cancelled = _order(subcontractor, user, (items[0], "10", "5"))
    services.cancel_purchase_order(cancelled, user)
    with pytest.raises(ValueError, match="取り消した発注"):
        services.receive(cancelled.lines.get(), Decimal("1"), DAY, user)

    po = _order(subcontractor, user, (items[0], "10", "5"))
    line = po.lines.get()
    with pytest.raises(ValueError, match="0 より大きい"):
        services.receive(line, Decimal("0"), DAY, user)
    assert Receipt.objects.count() == 0
    assert StockMove.objects.count() == 0

    services.receive(line, Decimal("12"), DAY, user)  # 残より多くても通る
    assert services.remaining_qty(line) == Decimal("-2")


@pytest.mark.django_db
@pytest.mark.parametrize(("rounding", "expected"), [("floor", "100"), ("ceil", "101")])
def test_amount_rounding_follows_partner(user, items, no_http, rounding, expected):
    partner = masters_services.create_partner(
        {"code": "S002", "name": "作り物の協力企業", "role": "subcontractor", "rounding": rounding}, user
    )
    po = _order(partner, user, (items[0], "10", "33.4"))

    receipt = services.receive(po.lines.get(), Decimal("3"), DAY, user)  # 3 × 33.4 = 100.2

    assert receipt.amount == Decimal(expected)


@pytest.mark.django_db
def test_annotate_received_and_order_received(user, subcontractor, items, no_http):
    po = _order(subcontractor, user, (items[0], "10", "5"), (items[1], "6", "7"))
    first, second = po.lines.order_by("line_no")
    services.receive(first, Decimal("3"), DAY, user)
    services.receive(first, Decimal("2"), DAY, user)
    services.receive(second, Decimal("1"), DAY, user)
    services.receive(second, Decimal("4"), DAY, user)
    other = _order(subcontractor, user, (items[0], "8", "5"))

    lines = {line.pk: line for line in services.annotate_received(PurchaseOrderLine.objects.all())}
    assert Decimal(str(lines[first.pk].received_total)) == Decimal("5")
    assert Decimal(str(lines[first.pk].remaining_total)) == Decimal("5")
    assert Decimal(str(lines[second.pk].received_total)) == Decimal("5")
    assert Decimal(str(lines[second.pk].remaining_total)) == Decimal("1")
    assert Decimal(str(lines[other.lines.get().pk].received_total)) == Decimal("0")

    orders = {order.pk: order for order in services.annotate_order_received(PurchaseOrder.objects.all())}
    assert Decimal(str(orders[po.pk].ordered_total)) == Decimal("16")  # 受入 2 件 × 明細 2 行でも重ならない
    assert Decimal(str(orders[po.pk].received_total)) == Decimal("10")
    assert Decimal(str(orders[po.pk].receive_remaining)) == Decimal("6")
    assert Decimal(str(orders[other.pk].ordered_total)) == Decimal("8")
    assert Decimal(str(orders[other.pk].received_total)) == Decimal("0")
    assert Decimal(str(orders[other.pk].receive_remaining)) == Decimal("8")
    assert [line.received_total for line in po.lines_with_received] == [Decimal("5"), Decimal("5")]


@pytest.mark.django_db
def test_payable_by_partner_two_months(user, subcontractor, items, no_http):
    second = masters_services.create_partner(
        {"code": "S000", "name": "作り物の別の協力企業", "role": "subcontractor", "rounding": "round"}, user
    )
    old_only = masters_services.create_partner(
        {"code": "S009", "name": "作り物の古い協力企業", "role": "subcontractor", "rounding": "round"}, user
    )
    today = date(2026, 11, 15)
    po = _order(subcontractor, user, (items[0], "100", "10"))
    po2 = _order(second, user, (items[0], "100", "3"))
    po_old = _order(old_only, user, (items[0], "100", "1"))
    services.receive(po.lines.get(), Decimal("2"), date(2026, 10, 31), user)  # 先月の末日 = 20
    services.receive(po.lines.get(), Decimal("5"), date(2026, 11, 1), user)  # 今月 = 50
    services.receive(po.lines.get(), Decimal("1"), date(2026, 11, 30), user)  # 今月 = 10
    services.receive(po2.lines.get(), Decimal("4"), date(2026, 10, 1), user)  # 先月 = 12
    services.receive(po.lines.get(), Decimal("9"), date(2026, 9, 30), user)  # 2 か月より前 = 入らない
    services.receive(po_old.lines.get(), Decimal("9"), date(2026, 9, 30), user)
    services.receive(po.lines.get(), Decimal("9"), date(2026, 12, 1), user)  # 来月 = 入らない

    result = services.payable_by_partner(today=today)

    assert result["months"] == [
        {"key": "2026-10", "label": "先月 (10 月)"},
        {"key": "2026-11", "label": "今月 (11 月)"},
    ]
    assert [row["partner"] for row in result["rows"]] == [second, subcontractor]  # 取引先コードの順
    assert result["rows"][0]["last_month"] == Decimal("12") and result["rows"][0]["this_month"] == Decimal("0")
    assert result["rows"][1]["last_month"] == Decimal("20") and result["rows"][1]["this_month"] == Decimal("60")
    assert result["totals"] == {"last_month": Decimal("32"), "this_month": Decimal("60")}

    january = services.payable_by_partner(today=date(2027, 1, 5))
    assert january["months"][0] == {"key": "2026-12", "label": "先月 (12 月)"}
    assert [row["partner"] for row in january["rows"]] == [subcontractor]
    assert services.payable_by_partner(today=date(2026, 6, 1))["rows"] == []


@pytest.mark.django_db
def test_create_purchase_order_send_false_skips_http(monkeypatch, user, subcontractor, items):
    monkeypatch.setenv(ENV_NAME, _new_secret())
    connect_services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    lines = [{"item": items[0], "qty": Decimal("2"), "unit_price": Decimal("10")}]

    quiet = services.create_purchase_order(subcontractor, lines, user, order_date=ORDER_DATE, send=False)
    assert fake.calls == 0
    assert quiet.status == PurchaseOrder.STATUS_DRAFT

    services.create_purchase_order(subcontractor, lines, user, order_date=ORDER_DATE)
    assert fake.calls == 1


@pytest.mark.django_db
def test_line_str_and_receipt_str(user, subcontractor, items, no_http):
    po = _order(subcontractor, user, (items[0], "10", "5"))
    line = po.lines.get()
    assert str(line) == f"{po.po_no}-1 T-100 作り物の軸"

    receipt = services.receive(line, Decimal("2.5"), DAY, user)
    text = str(receipt)
    assert text.startswith("2026-11-02 ")
    assert po.po_no in text
    assert text == f"2026-11-02 {po.po_no}-1 T-100 2.5"
    assert [r.pk for r in po.receipts.all()] == [receipt.pk]
