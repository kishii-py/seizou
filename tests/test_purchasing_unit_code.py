"""発注の明細の数量単位コードと、発注の入力の単位の欄 (数量単位の一覧から選ぶ欄。決め UNIT-1 = a。R5 の候補 C108 の その 3)。

数量の単位の一覧の 40 行は migration だけが入れるので、試験は transaction=True にしない。部品は services.create_item で作る。
作り物のデータだけを使う。取引先とのつながりは作らない (発注は送らない)。
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

import seizou.purchasing.migrations
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder, PurchaseOrderLine

ORDER_DATE = date(2026, 11, 2)
UNLISTED = "部品の単位「本」は単位の一覧にありません。"


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


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


def _pair(line):
    line.refresh_from_db()
    return line.unit, line.unit_code


def _purchase(subcontractor, item, user, **line):
    return services.create_purchase_order(
        subcontractor,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), **line}],
        user,
        order_date=ORDER_DATE,
        send=False,
    )


# ---------------------------------------------------------------- services


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("line", "expected"),
    [({}, ("kg", "KGM")), ({"unit": "箱"}, ("箱", "XBX")), ({"unit": "本"}, ("本", ""))],
)
def test_create_purchase_order_writes_unit_code(subcontractor, wire, user, line, expected):
    order = _purchase(subcontractor, wire, user, **line)
    assert _pair(order.lines.get()) == expected


@pytest.mark.django_db
def test_migration_fills_codes_without_changing_unit(subcontractor, shaft, user):
    [path] = Path(seizou.purchasing.migrations.__file__).parent.glob("*_purchaseorderline_unit_code.py")
    migration = importlib.import_module(f"seizou.purchasing.migrations.{path.stem}")
    order = _purchase(subcontractor, shaft, user)
    kg = PurchaseOrderLine.objects.create(
        purchase_order=order, line_no=2, item=shaft, qty=Decimal("1"), unit="KG", unit_price=Decimal("1"), amount=Decimal("1")
    )
    hon = PurchaseOrderLine.objects.create(
        purchase_order=order, line_no=3, item=shaft, qty=Decimal("1"), unit="本", unit_price=Decimal("1"), amount=Decimal("1")
    )

    migration.fill_line_codes(django.apps.apps, None)

    assert _pair(kg) == ("KG", "KGM")
    assert _pair(hon) == ("本", "")


# ---------------------------------------------------------------- 入力の画面


def _post(partner, *rows):
    data = {
        "partner": partner.pk,
        "order_date": ORDER_DATE.isoformat(),
        "note": "",
        "lines-TOTAL_FORMS": str(len(rows)),
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
    }
    for index, (item, unit) in enumerate(rows):
        data.update({f"lines-{index}-item": item.pk, f"lines-{index}-qty": "3", f"lines-{index}-unit_price": "100"})
        if unit is not None:
            data[f"lines-{index}-unit"] = unit
    return data


@pytest.mark.django_db
def test_create_screen_has_unit_select(logged_in):
    body = logged_in.get(reverse("purchasing:purchaseorder_create")).content.decode()
    url = reverse("masters:quantityunit_select")
    tag = re.search(r'<select name="lines-0-unit"[^>]*>', body)
    assert tag and f'data-select-url="{url}"' in tag.group(0)
    template = body[body.index('<template id="po-line-template">') :]
    assert '<select name="lines-__prefix__-unit"' in template


@pytest.mark.django_db
def test_create_screen_saves_chosen_or_copied_unit(logged_in, subcontractor, shaft, wire):
    response = logged_in.post(reverse("purchasing:purchaseorder_create"), _post(subcontractor, (shaft, None), (wire, "KGM")))
    assert response.status_code == 302
    order = PurchaseOrder.objects.get()
    assert _pair(order.lines.get(line_no=1)) == ("個", "H87")
    assert _pair(order.lines.get(line_no=2)) == ("kg", "KGM")


@pytest.mark.django_db
def test_unlisted_item_unit_asks_to_choose(logged_in, subcontractor, rod):
    url = reverse("purchasing:purchaseorder_create")
    response = logged_in.post(url, _post(subcontractor, (rod, "")))
    assert response.status_code == 200
    assert UNLISTED in response.content.decode()
    assert not PurchaseOrder.objects.exists()

    # 小窓の形 (注残の盤の「発注する」) でも同じ字が欄の下に出る
    response = logged_in.post(url, _post(subcontractor, (rod, "")), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert UNLISTED in response.content.decode()
    assert not PurchaseOrder.objects.exists()

    response = logged_in.post(url, _post(subcontractor, (rod, "XRD")))
    assert response.status_code == 302
    assert _pair(PurchaseOrder.objects.get().lines.get()) == ("本（棒）", "XRD")


@pytest.mark.django_db
def test_from_line_opens_with_order_line_unit_code(logged_in, customer, shaft, user):
    order = orders_services.create_order(
        customer,
        [
            {"item": shaft, "qty": Decimal("6"), "unit_price": Decimal("100")},
            {"item": shaft, "qty": Decimal("4"), "unit": "pcs", "unit_price": Decimal("100")},
        ],
        user,
        order_date=ORDER_DATE,
    )
    listed, unlisted = order.lines.order_by("line_no")
    url = reverse("purchasing:purchaseorder_create")

    response = logged_in.get(url, {"from_line": listed.pk})
    assert response.context["form"].lines.forms[0].initial["unit"] == "H87"
    assert '<option value="H87" selected>個</option>' in response.content.decode()

    response = logged_in.get(url, {"from_line": unlisted.pk})
    assert response.context["form"].lines.forms[0].initial["unit"] is None


@pytest.mark.django_db
def test_item_candidates_carry_unit_code_without_price(logged_in, shaft):
    row = logged_in.get(reverse("purchasing:item_select"), {"q": "T-100"}).json()["items"][0]
    assert row["unit_code"] == "H87"
    assert "standard_price" not in row
