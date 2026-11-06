"""入荷のポカよけ (設定 receiving_required)。入荷の引当が無い受注の明細の、指示書の発行と出荷。

設定 off (既定) は通る、warn は黄の知らせと「このまま保存」(confirmed=1 で保存)、stop は赤の知らせで止まる。
引当があれば stop でも通る。画面は Django の test client、一括出荷は検査の層を直に呼ぶ。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import checks
from seizou.core.exceptions import CheckViolation
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.receiving import services
from seizou.receiving.checks import MESSAGE
from seizou.receiving.models import SOURCE_CONSIGNED
from seizou.shipping.models import Shipment
from seizou.workorders.models import WorkOrder

ORDER_DATE = date(2026, 11, 2)
WORDS = "支給品・預かり品の入荷がありません"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "W-100", "name": "作り物のワーク", "unit": "個"}, user)


@pytest.fixture
def line(user, customer, item):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1)


def _receive(user, line, qty="10"):
    data = {
        "item": line.item,
        "partner": line.order.customer,
        "source": SOURCE_CONSIGNED,
        "qty": Decimal(qty),
        "received_on": ORDER_DATE,
    }
    return services.create_receiving(data, user, order_line=line)


def _issue(client, line, cut, **more):
    return client.post(reverse("workorders:workorder_create"), {"order_line": line.pk, "qty": "5", "processes": [cut.pk], **more})


def _ship(client, line, **more):
    return client.post(
        reverse("shipping:shipment_create"), {"order_line": line.pk, "qty": "5", "shipped_at": "2026-11-05", "note": "", **more}
    )


def test_checks_are_registered_for_issue_and_ship():
    for action in ("workorders.issue_workorder", "shipping.ship", "shipping.ship_lines"):
        assert "receiving_required" in [check.name for check in checks.registered(action)], action


def test_default_setting_is_off(settings):
    settings.SEIZOU = {}
    assert {check.current_mode() for action in ("workorders.issue_workorder", "shipping.ship") for check in checks.registered(action) if check.name == "receiving_required"} == {"off"}


def test_off_lets_issue_and_ship_through(settings, logged_in, line, cut):
    settings.SEIZOU = {"receiving_required": "off"}

    assert _issue(logged_in, line, cut).status_code == 302
    assert _ship(logged_in, line).status_code == 302
    assert WorkOrder.objects.count() == 1
    assert Shipment.objects.count() == 1


def test_warn_shows_yellow_then_confirm_saves_issue(settings, logged_in, line, cut):
    settings.SEIZOU = {"receiving_required": "warn"}

    first = _issue(logged_in, line, cut)
    body = first.content.decode()
    assert first.status_code == 200
    assert "alert-warning" in body and WORDS in body and "このまま保存" in body
    assert WorkOrder.objects.count() == 0

    second = _issue(logged_in, line, cut, confirmed="1")
    assert second.status_code == 302
    assert WorkOrder.objects.count() == 1


def test_warn_shows_yellow_then_confirm_saves_ship(settings, logged_in, line):
    settings.SEIZOU = {"receiving_required": "warn"}

    first = _ship(logged_in, line)
    body = first.content.decode()
    assert first.status_code == 200
    assert "alert-warning" in body and WORDS in body
    assert Shipment.objects.count() == 0

    assert _ship(logged_in, line, confirmed="1").status_code == 302
    assert Shipment.objects.count() == 1


def test_stop_is_red_for_issue_and_ship(settings, logged_in, line, cut):
    settings.SEIZOU = {"receiving_required": "stop"}

    issued = _issue(logged_in, line, cut, confirmed="1")
    body = issued.content.decode()
    assert issued.status_code == 200
    assert "alert-error" in body and "保存できません。" in body and WORDS in body
    assert WorkOrder.objects.count() == 0

    shipped = _ship(logged_in, line, confirmed="1")
    body = shipped.content.decode()
    assert shipped.status_code == 200
    assert "alert-error" in body and WORDS in body
    assert Shipment.objects.count() == 0


def test_stop_lets_allocated_line_through(settings, logged_in, user, line, cut):
    settings.SEIZOU = {"receiving_required": "stop"}
    _receive(user, line, "3")

    assert _issue(logged_in, line, cut).status_code == 302
    assert _ship(logged_in, line).status_code == 302
    assert WorkOrder.objects.count() == 1
    assert Shipment.objects.count() == 1


def test_ship_lines_names_lines_without_receiving(settings, user, customer, item, line):
    other = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("4"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    ).lines.get()
    _receive(user, other, "4")
    data = {"lines": [(line, Decimal("5")), (other, Decimal("4"))]}

    settings.SEIZOU = {"receiving_required": "stop"}
    with pytest.raises(CheckViolation) as caught:
        checks.run("shipping.ship_lines", data, user)
    assert f"{line.order.order_no} の 1 行目: {MESSAGE}" in caught.value.message
    assert other.order.order_no not in caught.value.message

    settings.SEIZOU = {"receiving_required": "warn"}
    warnings = [w for w in checks.run("shipping.ship_lines", data, user) if w.code == "receiving_required"]
    assert len(warnings) == 1

    settings.SEIZOU = {"receiving_required": "off"}
    assert [w for w in checks.run("shipping.ship_lines", data, user) if w.code == "receiving_required"] == []
