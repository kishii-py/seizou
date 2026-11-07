"""会社どうしの注文の明細の単価の単位 (決め PRICE-1b)。鍵 price_basis は seizou の項目 (中小企業共通 EDI に当たる値が無い)。

- 送る: 発注の明細の単価の単位を、注文のデータの明細の price_basis (unit か lump) に載せる。
- 受ける: 鍵の無い注文は部品の単位 (今と同じ意味)、unit と lump はその値、ほかの値は受け入れの時に断る。
- 桁の確かめ (_order_lines_problem): 式の行は「金額 (数量 × 単価)」の桁を見ない。
- 受信した注文の明細の表: 単価に「 / 式」「 / 個」を添える。単価の無い行には添えない。
相手への HTTP は requests.post を偽の物に差し替えて見る (外へは出ない)。取引先・品目・会社は作り物。
"""

import json
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string

from seizou.connect import services
from seizou.connect.models import Inbox
from seizou.masters import services as masters_services
from seizou.orders.models import Order
from seizou.purchasing import services as purchasing_services
from tests.test_connect_send import ENV_NAME, PARTNER_URL, FakePost
from tests.test_connect_signing import _new_secret


@pytest.fixture
def secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(ENV_NAME, value)
    return value


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の発注元", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def own_item(user):
    return masters_services.create_item({"part_no": "B-200", "name": "作り物の受ける側の部品", "unit": "個"}, user)


def _order_data(**line_changes):
    """手で作る注文のデータ (send_order が作る形。明細 1 行)。line_changes の値が None の鍵は外す。"""
    line = {
        "line_no": 1,
        "part_no": "T-100",
        "part_name": "作り物の軸",
        "qty": "300.00",
        "unit": "個",
        "unit_price": "50000.00",
        "due_date": "2026-11-20",
        "note": "",
    }
    line.update(line_changes)
    return {
        "id": str(uuid.uuid4()),
        "kind": "order",
        "sent_at": "2026-11-02T10:00:00+09:00",
        "order_no": "PO-9001",
        "order_date": "2026-11-02",
        "buyer": {"id": "a-company", "name": "作り物の発注する会社"},
        "supplier": {"code": "S001", "name": "作り物の受ける会社"},
        "note": "",
        "lines": [{key: value for key, value in line.items() if value is not None}],
    }


# --- 送る ---


@pytest.mark.django_db
def test_send_order_carries_price_basis(monkeypatch, user, secret, subcontractor, item):
    services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    purchasing_services.create_purchase_order(
        subcontractor,
        [
            {"item": item, "qty": Decimal("5"), "unit_price": Decimal("120000"), "price_basis": "lump"},
            {"item": item, "qty": Decimal("5"), "unit_price": Decimal("300")},
        ],
        user,
        order_date=date(2026, 11, 2),
    )

    sent = json.loads(fake.calls[0]["data"].decode("utf-8"))
    assert [line["price_basis"] for line in sent["lines"]] == ["lump", "unit"]


# --- 受ける ---


@pytest.mark.django_db
def test_accept_lump_order(user, customer, own_item):
    inbox = services.record_inbox(customer, _order_data(price_basis="lump"), user)
    order = services.accept(inbox, {"T-100": own_item}, user, send=False)
    line = order.lines.get()
    assert (line.price_basis, line.qty, line.amount) == ("lump", Decimal("300"), Decimal("50000"))


@pytest.mark.django_db
def test_accept_without_price_basis_is_unit(user, customer, own_item):
    inbox = services.record_inbox(customer, _order_data(qty="3.00", unit_price="250.00"), user)
    order = services.accept(inbox, {"T-100": own_item}, user, send=False)
    line = order.lines.get()
    assert (line.price_basis, line.amount) == ("unit", Decimal("750"))


@pytest.mark.django_db
def test_accept_refuses_unknown_price_basis(user, customer, own_item):
    inbox = services.record_inbox(customer, _order_data(price_basis="set"), user)
    with pytest.raises(ValueError) as caught:
        services.accept(inbox, {"T-100": own_item}, user, send=False)
    message = str(caught.value)
    assert "1 行目の単価の単位" in message
    assert "この注文は断り、送る側に送り直してもらってください。" in message
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_RECEIVED
    assert Order.objects.count() == 0


@pytest.mark.django_db
def test_lump_line_skips_amount_digits():
    big = {"qty": "1000000", "unit_price": "1000000"}
    problem = services._order_lines_problem({"lines": [big]})
    assert "金額 (数量 × 単価)" in problem
    assert services._order_lines_problem({"lines": [{**big, "price_basis": "unit"}]}) == problem
    assert services._order_lines_problem({"lines": [{**big, "price_basis": "lump"}]}) is None


# --- 受信した注文の明細の表 ---


def test_payload_lines_show_price_unit():
    def render(*lines):
        return render_to_string(
            "connect/_payload_lines.html",
            {"object": SimpleNamespace(payload={"lines": list(lines)}), "seizou": {"hide_money": False}},
        )

    base = {"line_no": 1, "part_no": "A-100", "part_name": "作り物の軸", "qty": "300", "unit": "個", "due_date": "2026-11-20"}
    shown = render({**base, "unit_price": "50000", "price_basis": "lump"})
    assert '>¥50,000<span class="text-base-content/70"> / 式</span>' in shown
    shown = render({**base, "unit_price": "250", "price_basis": "unit"})
    assert '>¥250<span class="text-base-content/70"> / 個</span>' in shown
    # 鍵の無い行は部品の単位。単位の空の行は「個」。
    assert " / 個</span>" in render({**base, "unit_price": "250"})
    assert " / 個</span>" in render({**base, "unit": "", "unit_price": "250"})
    # 単価の無い行 (null・鍵が無い・空の字) には「 / 」だけを出さない。
    for line in ({**base, "unit_price": None}, dict(base), {**base, "unit_price": ""}):
        assert " / " not in render(line), line
