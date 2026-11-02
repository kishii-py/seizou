"""初見テストと E2E の 2 回目 (11-03) の所見の直しを確かめる。

1. 字を打って探す欄 (Tom Select) に daisyUI の select の class を付けない (候補の一覧が欄の枠に隠れて見えなかった)。
2. R01・R06・R07 の知らせに残りの数を出し、数の桁をそろえる (「(10.00)」でなく「(10)」)。
3. 整数の欄 (締日など) を字の欄 (inputmode=numeric) にし、「2e」が空で保存されずに誤りになる。
4. 受注と発注の入力にも Enter の守り (data-enter-guard) を付ける。
5. 左のメニューを画面の高さに固定し、入口が多いときは nav の中が送れる。
6. 全体の検索 (部品マスターの節) が、会社ごとの項目のうち検索に使う物も見る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import checks
from seizou.core import fields as company_fields
from seizou.core.views import widget_css
from seizou.core.widgets import IntegerInput, RemoteSelect
from seizou.masters import search as masters_search
from seizou.masters import services as masters_services
from seizou.masters.models import Partner
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def line(user):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}], user, order_date=ORDER_DATE
    )
    return order.lines.get()


# ---------------------------------------------------------------- 1 字を打って探す欄の class


def test_remote_select_has_no_daisy_select_class():
    assert widget_css(RemoteSelect("masters:item_select")) == "w-full"
    assert widget_css(forms.Select()) == "select w-full"


@pytest.mark.django_db
def test_order_form_select_fields_have_no_select_class(logged_in):
    body = logged_in.get(reverse("orders:order_create")).content.decode()
    start = body.index("data-select-url")
    tag = body[body.rindex("<select", 0, start) : body.index(">", start)]
    assert 'class="w-full"' in tag
    assert "select w-full" not in tag


# ---------------------------------------------------------------- 2 知らせの残りの数と桁


@pytest.mark.django_db
def test_r01_warning_tells_remaining_and_plain_numbers(user, line, settings):
    settings.SEIZOU = {"over_shipment": "warn"}
    shipping_services.ship(line, Decimal("4"), SHIPPED_AT, user)
    warnings = checks.run("shipping.ship", {"order_line": line, "qty": Decimal("7")}, user)
    assert len(warnings) == 1
    message = warnings[0].message
    assert "(11)" in message and "(10)" in message
    assert "残りは 6" in message
    assert "10.00" not in message and "11.00" not in message


@pytest.mark.django_db
def test_r06_warning_tells_remaining(user, line, settings):
    settings.SEIZOU = {"over_workorder": "warn"}
    warnings = checks.run("workorders.issue_workorder", {"order_line": line, "qty": Decimal("12")}, user)
    assert len(warnings) == 1
    assert "(12)" in warnings[0].message and "残りは 10" in warnings[0].message


# ---------------------------------------------------------------- 3 整数の欄


def test_integer_input_normalizes_and_keeps_bad_text():
    widget = IntegerInput()
    assert widget.attrs["inputmode"] == "numeric"
    assert widget.value_from_datadict({"n": "３１"}, {}, "n") == "31"
    assert widget.value_from_datadict({"n": "2e"}, {}, "n") == "2e"


@pytest.mark.django_db
def test_closing_day_bad_text_is_an_error_not_saved_empty(logged_in):
    url = reverse("masters:partner_create")
    response = logged_in.post(url, {"code": "C009", "name": "作り物の社", "role": "customer", "closing_day": "2e"})
    assert response.status_code == 200
    body = response.content.decode()
    assert "closing_day" in body and ("整数" in body or "数" in body)
    assert not Partner.objects.exists()


# ---------------------------------------------------------------- 4 Enter の守り


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["orders:order_create", "purchasing:purchaseorder_create", "masters:partner_create"])
def test_forms_have_enter_guard(logged_in, url_name):
    body = logged_in.get(reverse(url_name)).content.decode()
    assert "data-enter-guard" in body
    assert "Enter で保存まで進めない" in body


# ---------------------------------------------------------------- 5 左のメニュー


@pytest.mark.django_db
def test_sidebar_is_viewport_high_and_scrolls_inside(logged_in):
    body = logged_in.get(reverse("core:home")).content.decode()
    start = body.index("<aside")
    tag = body[start : body.index(">", start)]
    assert "h-screen" in tag and "min-h-full" not in tag
    assert 'class="flex-1 overflow-y-auto p-4" aria-label="メニュー"' in body


# ---------------------------------------------------------------- 6 全体の検索と会社ごとの項目


@pytest.mark.django_db
def test_masters_search_uses_company_searchable_fields(user, monkeypatch):
    masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "ダース"}, user)
    assert masters_search.search("ダース").total == 0
    monkeypatch.setattr(company_fields, "search_fields_for", lambda model: ["unit"])
    section = masters_search.search("ダース")
    assert section.total == 1
    assert section.rows[0].cells[0] == "T-200"


@pytest.mark.django_db
def test_masters_search_skips_ext_lookups_without_ext_table(user, monkeypatch):
    masters_services.create_item({"part_no": "T-300", "name": "作り物の軸", "unit": "個"}, user)
    monkeypatch.setattr(company_fields, "search_fields_for", lambda model: ["ext__ext01"])
    assert masters_search.company_lookups() == []
    assert masters_search.search("作り物").total == 1
