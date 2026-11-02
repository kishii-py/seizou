"""R01: 出荷の数の合計が、受注の明細の数を超えたら (同梱のポカよけ。会社の設定 over_shipment)。

検査の層 (seizou.core.checks) を設定の 3 つの値 (stop・warn・off) で回し、出荷の入力の画面でも同じ動きを見る。
止める = 赤の知らせで保存しない。知らせる = 黄の知らせと「このまま保存」、confirmed=1 の 2 回目で保存。
通す = そのまま保存。画面は Django の test client で見る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import checks
from seizou.core.exceptions import CheckViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.models import Shipment

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def line(user):
    """数量 10 の明細 1 行の受注の明細。"""
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def shipped_four(user, line):
    """10 のうち 4 を出荷した明細。"""
    services.ship(line, Decimal("4"), SHIPPED_AT, user)
    return line


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def _post(line, qty, **extra):
    return {
        "order_line": line.pk,
        "qty": str(qty),
        "shipped_at": SHIPPED_AT.isoformat(),
        "note": "",
        **extra,
    }


# --- 検査の層 ---


@pytest.mark.django_db
def test_r01_stop_raises_check_violation(settings, user, shipped_four):
    settings.SEIZOU = {"over_shipment": "stop"}
    with pytest.raises(CheckViolation) as caught:
        checks.run("shipping.ship", {"order_line": shipped_four, "qty": 7}, user)
    assert caught.value.code == "R01"
    assert "11" in caught.value.message and "10" in caught.value.message
    # 合計がちょうど 10 なら止めない
    assert checks.run("shipping.ship", {"order_line": shipped_four, "qty": 6}, user) == []


@pytest.mark.django_db
def test_r01_warn_returns_warning(settings, user, shipped_four):
    settings.SEIZOU = {"over_shipment": "warn"}
    warnings = checks.run("shipping.ship", {"order_line": shipped_four, "qty": 7}, user)
    assert len(warnings) == 1
    assert warnings[0].code == "R01"
    # 合計がちょうど 10 なら知らせない
    assert checks.run("shipping.ship", {"order_line": shipped_four, "qty": 6}, user) == []


@pytest.mark.django_db
def test_r01_off_is_silent(settings, user, shipped_four):
    settings.SEIZOU = {"over_shipment": "off"}
    assert checks.run("shipping.ship", {"order_line": shipped_four, "qty": 7}, user) == []


# --- 出荷の入力の画面 (動画の場面 4) ---


@pytest.mark.django_db
def test_r01_stop_on_screen_is_red_and_not_saved(settings, logged_in, line):
    settings.SEIZOU = {"over_shipment": "stop"}
    response = logged_in.post(reverse("shipping:shipment_create"), _post(line, 11))
    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body and "R01" in body
    assert "このまま保存" not in body
    assert Shipment.objects.count() == 0
    # 入れた値は消えない
    assert response.context["form"]["qty"].value() == "11"

    # 「このまま保存」の印を付けて送っても、止めるの設定では保存しない
    again = logged_in.post(reverse("shipping:shipment_create"), _post(line, 11, confirmed="1"))
    assert again.status_code == 200
    assert Shipment.objects.count() == 0


@pytest.mark.django_db
def test_r01_warn_on_screen_then_confirm(settings, logged_in, line):
    settings.SEIZOU = {"over_shipment": "warn"}
    first = logged_in.post(reverse("shipping:shipment_create"), _post(line, 11))
    assert first.status_code == 200
    body = first.content.decode()
    assert "alert-warning" in body and "R01" in body and "このまま保存" in body
    assert 'name="confirmed" value="1"' in body
    assert "alert-error" not in body
    assert Shipment.objects.count() == 0

    second = logged_in.post(reverse("shipping:shipment_create"), _post(line, 11, confirmed="1"))
    shipment = Shipment.objects.get()
    assert second.status_code == 302
    assert second["Location"] == reverse("shipping:shipment_detail", args=[shipment.pk])
    assert shipment.qty == Decimal("11")
    assert orders_services.remaining_qty(line) == Decimal("-1")


@pytest.mark.django_db
def test_r01_off_on_screen_saves(settings, logged_in, line):
    settings.SEIZOU = {"over_shipment": "off"}
    response = logged_in.post(reverse("shipping:shipment_create"), _post(line, 11))
    assert response.status_code == 302
    assert Shipment.objects.get().qty == Decimal("11")
    assert orders_services.remaining_qty(line) == Decimal("-1")


# --- 出荷の編集 (shipping.update_shipment) の検査の層 ---


@pytest.fixture
def shipped_four_and_four(user, line):
    """10 のうち 4 と 4 を出荷した明細と、2 つ目の出荷。"""
    services.ship(line, Decimal("4"), SHIPPED_AT, user)
    second = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    return line, second


@pytest.mark.django_db
def test_r01_update_stop_counts_other_shipments(settings, user, shipped_four_and_four):
    settings.SEIZOU = {"over_shipment": "stop"}
    _line, second = shipped_four_and_four
    with pytest.raises(CheckViolation) as caught:
        checks.run("shipping.update_shipment", {"object": second, "qty": Decimal("7")}, user)
    assert caught.value.code == "R01"
    assert "11" in caught.value.message and "10" in caught.value.message
    # 2 つ目を 6 に直すと合計がちょうど 10 なので止めない
    assert checks.run("shipping.update_shipment", {"object": second, "qty": Decimal("6")}, user) == []


@pytest.mark.django_db
def test_r01_update_warn_returns_warning(settings, user, shipped_four_and_four):
    settings.SEIZOU = {"over_shipment": "warn"}
    _line, second = shipped_four_and_four
    warnings = checks.run("shipping.update_shipment", {"object": second, "qty": Decimal("7")}, user)
    assert len(warnings) == 1
    assert warnings[0].code == "R01"
    assert checks.run("shipping.update_shipment", {"object": second, "qty": Decimal("6")}, user) == []


# --- 出荷の編集の画面 ---


def _edit_post(qty, **extra):
    return {"qty": str(qty), "shipped_at": SHIPPED_AT.isoformat(), "note": "", **extra}


@pytest.mark.django_db
def test_r01_update_on_screen_stop_is_red(settings, logged_in, shipped_four_and_four):
    settings.SEIZOU = {"over_shipment": "stop"}
    _line, second = shipped_four_and_four
    url = reverse("shipping:shipment_update", args=[second.pk])

    response = logged_in.post(url, _edit_post(7))
    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body and "R01" in body
    assert "このまま保存" not in body
    second.refresh_from_db()
    assert second.qty == Decimal("4")

    # 「このまま保存」の印を付けて送っても、止めるの設定では保存しない
    again = logged_in.post(url, _edit_post(7, confirmed="1"))
    assert again.status_code == 200
    second.refresh_from_db()
    assert second.qty == Decimal("4")


@pytest.mark.django_db
def test_r01_update_on_screen_warn_then_confirm(settings, logged_in, shipped_four_and_four):
    settings.SEIZOU = {"over_shipment": "warn"}
    line, second = shipped_four_and_four
    url = reverse("shipping:shipment_update", args=[second.pk])

    first = logged_in.post(url, _edit_post(7))
    assert first.status_code == 200
    body = first.content.decode()
    assert "alert-warning" in body and "R01" in body and "このまま保存" in body
    assert "alert-error" not in body
    second.refresh_from_db()
    assert second.qty == Decimal("4")

    confirmed = logged_in.post(url, _edit_post(7, confirmed="1"))
    assert confirmed.status_code == 302
    assert confirmed["Location"] == reverse("shipping:shipment_detail", args=[second.pk])
    second.refresh_from_db()
    assert second.qty == Decimal("7")
    assert orders_services.remaining_qty(line) == Decimal("-1")
