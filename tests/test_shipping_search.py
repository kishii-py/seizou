"""出荷の横断の検索 (core の検索の画面の約束に乗る。精査の番号 12。本体の決め 11-11 14:2x)。

出荷番号と納品書番号で出荷を引く。取引先で絞ると受注の得意先で絞る。部署の判定が on なら、部署で出荷を開けない人には出さない。
名前と品番は作り物。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import search, services

User = get_user_model()

PASSWORD = "kumo-tests-2611"
ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def admin(db):
    return User.objects.create_superuser("admin", password=PASSWORD)


@pytest.fixture
def item(admin):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, admin)


def _line(admin, item, code, name):
    customer = masters_services.create_partner({"code": code, "name": name, "role": "customer"}, admin)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}], admin, order_date=ORDER_DATE
    )
    return customer, order.lines.get()


@pytest.fixture
def first(admin, item):
    """得意先 1 つ目の受注の明細に、出荷 3 つ (2 つを納品書にまとめる)。(得意先, 納品書, まとめた出荷 2 つ, まとめない出荷)。"""
    customer, line = _line(admin, item, "C001", "作り物の得意先")
    a = services.ship(line, Decimal("2"), date(2026, 11, 3), admin)
    b = services.ship(line, Decimal("3"), date(2026, 11, 4), admin)
    loose = services.ship(line, Decimal("1"), date(2026, 11, 5), admin)
    note = services.create_delivery_note(customer, [a, b], admin)
    return customer, note, [a, b], loose


@pytest.fixture
def second(admin, item):
    """得意先 2 つ目の出荷 1 つ。(得意先, 出荷)。"""
    customer, line = _line(admin, item, "C002", "作り物の別の得意先")
    return customer, services.ship(line, Decimal("4"), date(2026, 11, 6), admin)


def _page(client, user, params):
    client.force_login(User.objects.get(pk=user.pk))
    response = client.get(reverse("core:search"), params)
    assert response.status_code == 200
    return response


def test_found_by_part_of_shipment_no(first):
    """(a) 出荷番号の一部 (頭の 1 字を除いた字) で当たり、種類は「出荷」、行の url は出荷の詳細。納品書に入っていなければ納品書の列は空。"""
    _customer, _note, _shipments, loose = first
    section = search.search(loose.shipment_no[1:])
    assert (section.kind, section.label) == ("shipments", "出荷")
    assert section.total == 1
    row = section.rows[0]
    assert row.cells[0] == loose.shipment_no
    assert row.cells[2:5] == ("T-100", Decimal("1"), date(2026, 11, 5))
    assert row.cells[5] == ""
    assert row.url == reverse("shipping:shipment_detail", args=[loose.pk])
    assert row.match is None


def test_found_by_delivery_note_no(first):
    """(b) 納品書番号で、その納品書に入った出荷が全部当たり、納品書の列にその番号が出る。入っていない出荷は当たらない。"""
    _customer, note, shipments, loose = first
    section = search.search(note.note_no)
    assert section.total == 2
    assert {row.cells[0] for row in section.rows} == {s.shipment_no for s in shipments}
    assert loose.shipment_no not in {row.cells[0] for row in section.rows}
    assert all(row.cells[5] == note.note_no for row in section.rows)
    # 並びは出荷日の新しい順
    assert [row.cells[0] for row in section.rows] == [shipments[1].shipment_no, shipments[0].shipment_no]


def test_partner_filter_hides_other_customers(first, second):
    """(c) 取引先で絞ると、ほかの得意先の出荷は出ない。"""
    customer, _note, _shipments, _loose = first
    _other, other_shipment = second
    assert search.search("SH-").total == 4
    narrowed = search.search("SH-", partner=customer.pk)
    assert narrowed.total == 3
    assert other_shipment.shipment_no not in {row.cells[0] for row in narrowed.rows}


def test_search_page_shows_shipments_chip_and_row(client, admin, first):
    """(d) 横断の検索の画面に「出荷」の chip と、出荷の詳細への行の link が出る。"""
    _customer, note, (a, _b), _loose = first
    response = _page(client, admin, {"q": note.note_no})
    assert any(chip["kind"] == "shipments" and chip["label"] == "出荷" for chip in response.context["chips"])
    body = response.content.decode()
    assert "kind=shipments" in body
    assert 'id="search-shipments"' in body
    assert reverse("shipping:shipment_detail", args=[a.pk]) in body


def test_department_without_shipping_sees_no_shipments(settings, client, first):
    """(e) 部署の判定が on で、部署に shipping の無い人には chip も行も出ない (部署に shipping のある人には出る)。"""
    settings.SEIZOU = {"access_control": {"department": "on"}}
    _customer, note, (a, _b), _loose = first
    seizo = accounts_services.create_department("製造", ["workorders", "progress", "masters"], ["workorders"], None)
    accounts_services.issue_account("genba", PASSWORD, [seizo], [], {}, None)
    sales = accounts_services.create_department("受注と出荷", ["orders", "shipping", "masters"], ["orders"], None)
    accounts_services.issue_account("jimu", PASSWORD, [sales], [], {}, None)
    detail = reverse("shipping:shipment_detail", args=[a.pk])

    outside = _page(client, User.objects.get(username="genba"), {"q": note.note_no})
    assert "shipments" not in [chip["kind"] for chip in outside.context["chips"]]
    assert "shipments" not in [section["kind"] for section in outside.context["sections"]]
    assert detail not in outside.content.decode()

    inside = _page(client, User.objects.get(username="jimu"), {"q": note.note_no})
    assert "shipments" in [chip["kind"] for chip in inside.context["chips"]]
    assert detail in inside.content.decode()
