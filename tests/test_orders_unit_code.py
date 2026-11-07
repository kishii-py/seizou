"""受注の明細の数量単位コードと、受注の入力の単位の欄 (数量単位の一覧から選ぶ欄。決め UNIT-1 = a。R5 の候補 C108 の その 2)。

数量の単位の一覧の 40 行は migration だけが入れるので、試験は transaction=True にしない。部品は services.create_item で作る。作り物のデータだけを使う。
"""

import importlib
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import django.apps
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

import seizou.orders.migrations
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import Order, OrderLine

ORDER_DATE = date(2026, 11, 2)
UNLISTED = "部品の単位「本」は単位の一覧にありません。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def wire(user):
    return masters_services.create_item({"part_no": "T-200", "name": "作り物の線", "unit": "kg"}, user)


@pytest.fixture
def rod(user):
    """単位が数量単位の一覧に無い字 (「本」) の部品。"""
    return masters_services.create_item({"part_no": "T-300", "name": "作り物の棒", "unit": "本"}, user)


def _order(customer, item, user, **line):
    return services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), **line}], user, order_date=ORDER_DATE
    )


def _pair(line):
    line.refresh_from_db()
    return line.unit, line.unit_code


# ---------------------------------------------------------------- services


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ({}, ("kg", "KGM")),
        ({"unit": "個"}, ("個", "H87")),
        ({"unit": "本"}, ("本", "")),
        ({"unit": "", "unit_code": "SET"}, ("セット", "SET")),
        ({"unit": "キロ", "unit_code": "KGM"}, ("キロ", "KGM")),
        ({"unit": "kg", "unit_code": "ZZZ"}, ("kg", "KGM")),
    ],
)
def test_create_order_writes_unit_code(customer, wire, user, line, expected):
    order = _order(customer, wire, user, **line)
    assert _pair(order.lines.get()) == expected


@pytest.mark.django_db
def test_update_order_rederives_code_only_when_unit_changes(customer, wire, user):
    order = _order(customer, wire, user, unit="キロ", unit_code="KGM")
    line = order.lines.get()

    # 受注の編集の画面の形: 同じ字と数量だけを渡す。相手が送ったコードを消さない
    services.update_order(order, {}, [{"order_line": line, "unit": "キロ", "qty": Decimal("12")}], user)
    assert _pair(line) == ("キロ", "KGM")
    # 単位を渡さない直しはコードが前のまま
    services.update_order(order, {}, [{"order_line": line, "qty": Decimal("13")}], user)
    assert _pair(line) == ("キロ", "KGM")

    services.update_order(order, {}, [{"order_line": line, "unit": "個"}], user)
    assert _pair(line) == ("個", "H87")
    services.update_order(order, {}, [{"order_line": line, "unit": "本"}], user)
    assert _pair(line) == ("本", "")


@pytest.mark.django_db
def test_migration_fills_codes_without_changing_unit(customer, shaft, user):
    [path] = Path(seizou.orders.migrations.__file__).parent.glob("*_orderline_unit_code.py")
    migration = importlib.import_module(f"seizou.orders.migrations.{path.stem}")
    order = _order(customer, shaft, user)
    kg = OrderLine.objects.create(
        order=order, line_no=2, item=shaft, qty=Decimal("1"), unit="KG", unit_price=Decimal("1"), amount=Decimal("1")
    )
    hon = OrderLine.objects.create(
        order=order, line_no=3, item=shaft, qty=Decimal("1"), unit="本", unit_price=Decimal("1"), amount=Decimal("1")
    )

    migration.fill_line_codes(django.apps.apps, None)

    assert _pair(kg) == ("KG", "KGM")
    assert _pair(hon) == ("本", "")


# ---------------------------------------------------------------- 入力の画面


def _create_post(customer, item, **line):
    return {
        "customer": customer.pk,
        "order_date": ORDER_DATE.isoformat(),
        "customer_order_no": "",
        "lines-TOTAL_FORMS": "1",
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-item": item.pk,
        "lines-0-qty": "10",
        "lines-0-unit": "",
        "lines-0-unit_price": "100",
        "lines-0-due_date": "",
        "lines-0-note": "",
        **{f"lines-0-{key}": value for key, value in line.items()},
    }


def _update_post(order, **line):
    row = order.lines.get()
    data = {
        "customer": order.customer_id,
        "order_date": order.order_date.isoformat(),
        "customer_order_no": order.customer_order_no,
        "note": order.note,
        "lines-TOTAL_FORMS": "1",
        "lines-INITIAL_FORMS": "1",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-order_line": row.pk,
        "lines-0-item": row.item_id,
        "lines-0-qty": str(row.qty),
        "lines-0-unit": row.unit_code,
        "lines-0-unit_price": str(row.unit_price),
        "lines-0-due_date": "",
        "lines-0-note": "",
    }
    data.update({f"lines-0-{key}": value for key, value in line.items()})
    return data


@pytest.mark.django_db
def test_create_screen_has_unit_select(logged_in):
    body = logged_in.get(reverse("orders:order_create")).content.decode()
    url = reverse("masters:quantityunit_select")
    for name in ("lines-0-unit", "lines-__prefix__-unit"):
        tag = re.search(rf'<select name="{name}"[^>]*>', body)
        assert tag, name
        assert f'data-select-url="{url}"' in tag.group(0)
    assert not re.search(r'<input[^>]*name="lines-0-unit"', body)
    cell = re.search(r'<td class="([^"]*)">\s*<select name="lines-0-unit"', body)
    assert cell and "min-w-20" in cell.group(1)


@pytest.mark.django_db
def test_new_line_saves_chosen_or_copied_unit(logged_in, customer, shaft):
    response = logged_in.post(reverse("orders:order_create"), _create_post(customer, shaft, unit="XBX"))
    assert response.status_code == 302
    assert _pair(Order.objects.get().lines.get()) == ("箱", "XBX")

    response = logged_in.post(reverse("orders:order_create"), _create_post(customer, shaft))
    assert response.status_code == 302
    assert _pair(Order.objects.latest("pk").lines.get()) == ("個", "H87")

    response = logged_in.post(reverse("orders:order_create"), _create_post(customer, shaft, unit="箱"))
    assert response.status_code == 200
    assert Order.objects.count() == 2


@pytest.mark.django_db
def test_new_line_with_unlisted_item_unit_asks_to_choose(logged_in, customer, rod):
    response = logged_in.post(reverse("orders:order_create"), _create_post(customer, rod))
    assert response.status_code == 200
    body = response.content.decode()
    assert UNLISTED in body
    assert "入れた値に誤りがあります。" in body
    assert not Order.objects.exists()

    response = logged_in.post(reverse("orders:order_create"), _create_post(customer, rod, unit="PI"))
    assert response.status_code == 302
    assert _pair(Order.objects.get().lines.get()) == ("本（パイプ）", "PI")


@pytest.mark.django_db
def test_hidden_unit_field_does_not_stop(logged_in, settings, customer, rod):
    settings.SEIZOU = {"hidden_fields": ["unit"]}
    data = _create_post(customer, rod)
    del data["lines-0-unit"]
    response = logged_in.post(reverse("orders:order_create"), data)
    assert response.status_code == 302
    assert _pair(Order.objects.get().lines.get()) == ("本", "")


@pytest.mark.django_db
def test_edit_keeps_partner_unit_when_same_code_chosen(logged_in, customer, wire, user):
    order = _order(customer, wire, user, unit="キロ", unit_code="KGM")
    url = reverse("orders:order_update", args=[order.pk])
    assert '<option value="KGM" selected>kg</option>' in logged_in.get(url).content.decode()

    response = logged_in.post(url, _update_post(order, unit="KGM", qty="12"))
    assert response.status_code == 302
    line = order.lines.get()
    assert _pair(line) == ("キロ", "KGM")
    assert line.qty == Decimal("12")

    response = logged_in.post(url, _update_post(order, unit="GRM"))
    assert response.status_code == 302
    assert _pair(line) == ("g", "GRM")


@pytest.mark.django_db
def test_edit_keeps_unlisted_unit_unless_chosen(logged_in, customer, shaft, user):
    order = _order(customer, shaft, user, unit="pcs")
    url = reverse("orders:order_update", args=[order.pk])
    assert "今の単位「pcs」は単位の一覧にありません。選ばなければ「pcs」のまま残ります。" in logged_in.get(url).content.decode()

    response = logged_in.post(url, _update_post(order, unit=""))
    assert response.status_code == 302
    assert _pair(order.lines.get()) == ("pcs", "")


@pytest.mark.django_db
def test_edit_clears_listed_unit_when_emptied(logged_in, customer, shaft, user):
    order = _order(customer, shaft, user)
    response = logged_in.post(reverse("orders:order_update", args=[order.pk]), _update_post(order, unit=""))
    assert response.status_code == 302
    assert _pair(order.lines.get()) == ("", "")


@pytest.mark.django_db
def test_deleted_row_does_not_stop_on_unit(logged_in, customer, shaft, rod, user):
    order = _order(customer, shaft, user)
    data = _update_post(order)
    data.update(
        {
            "lines-TOTAL_FORMS": "2",
            "lines-1-order_line": "",
            "lines-1-item": rod.pk,
            "lines-1-qty": "3",
            "lines-1-unit": "",
            "lines-1-unit_price": "100",
            "lines-1-due_date": "",
            "lines-1-note": "",
            "lines-1-DELETE": "on",
        }
    )
    response = logged_in.post(reverse("orders:order_update", args=[order.pk]), data)
    assert response.status_code == 302
    assert order.lines.count() == 1
