"""請求書の横断の検索 (core の検索の画面の約束に乗る。精査の番号 12。本体の決め 11-11 14:2x)。

請求書番号で請求書を引く (取消の請求書も状態「取消」で出す)。取引先で絞ると得意先で絞る。
金額の閲覧の無い人には、合計の枡を core が「—」にする。部署の判定が on なら、部署で請求を開けない人には出さない。
作り物のデータは全部 services で作る。名前と品番は作り物。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.billing import search, services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

User = get_user_model()

PASSWORD = "kumo-tests-2611"
ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
#: 金額の閲覧だけを効かせる (部署と書く権限の判定はしない。tests/test_billing_lump_notice.py と同じ)
NO_MONEY = {"department": "off", "write": "off", "money": "on"}


@pytest.fixture
def admin(db):
    return User.objects.create_superuser("admin", password=PASSWORD)


@pytest.fixture
def item(admin):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, admin)


def _shipment(admin, item, code, name):
    """得意先を作り、1 個 1,250 円の受注を出荷する (請求書の小計 1,250・消費税 125・合計 1,375)。(得意先, 出荷)。"""
    customer = masters_services.create_partner({"code": code, "name": name, "role": "customer"}, admin)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("1"), "unit_price": Decimal("1250")}], admin, order_date=ORDER_DATE
    )
    return customer, shipped(admin, customer, order.lines.get(), "1", SHIPPED_AT)


def _period(invoice) -> str:
    return f"{invoice.period_from:%Y-%m-%d}〜{invoice.period_to:%Y-%m-%d}"


@pytest.fixture
def first(admin, item):
    """得意先 1 つ目の請求書 2 枚。1 枚目を取り消し、同じ出荷で 2 枚目を作る。(得意先, 取消の請求書, 発行の請求書)。"""
    customer, shipment = _shipment(admin, item, "C001", "作り物の得意先")
    cancelled = services.create_invoice(customer, [shipment], admin, issued_on=date(2026, 11, 20))
    services.cancel_invoice(cancelled, admin)
    cancelled.refresh_from_db()
    issued = services.create_invoice(customer, [shipment], admin, issued_on=date(2026, 11, 21))
    assert issued.total == Decimal("1375")
    return customer, cancelled, issued


@pytest.fixture
def second(admin, item):
    """得意先 2 つ目の請求書 1 枚。(得意先, 請求書)。"""
    customer, shipment = _shipment(admin, item, "C002", "作り物の別の得意先")
    return customer, services.create_invoice(customer, [shipment], admin, issued_on=date(2026, 11, 22))


def _page(client, user, params):
    client.force_login(User.objects.get(pk=user.pk))
    response = client.get(reverse("core:search"), params)
    assert response.status_code == 200
    return response


def test_found_by_part_of_invoice_no_including_cancelled(first):
    """(a) 請求書番号の一部 (頭の 1 字を除いた字) で当たり、種類は「請求書」、行の url は請求書の詳細。取消の請求書も状態「取消」で当たる。"""
    customer, cancelled, issued = first
    section = search.search(issued.invoice_no[1:])
    assert (section.kind, section.label) == ("invoices", "請求書")
    assert section.total == 1
    row = section.rows[0]
    assert row.cells == (issued.invoice_no, customer, date(2026, 11, 21), _period(issued), Decimal("1375"), "発行")
    assert row.url == reverse("billing:invoice_detail", args=[issued.pk])
    assert row.match is None

    found = search.search(cancelled.invoice_no[1:])
    assert found.total == 1
    assert found.rows[0].cells[0] == cancelled.invoice_no
    assert found.rows[0].cells[5] == "取消"
    assert found.rows[0].url == reverse("billing:invoice_detail", args=[cancelled.pk])

    # 並びは発行日の新しい順
    both = search.search("IV-")
    assert [row.cells[0] for row in both.rows] == [issued.invoice_no, cancelled.invoice_no]


def test_partner_filter_hides_other_customers(first, second):
    """(b) 取引先で絞ると、ほかの得意先の請求書は出ない。"""
    customer, _cancelled, _issued = first
    _other, other_invoice = second
    assert search.search("IV-").total == 3
    narrowed = search.search("IV-", partner=customer.pk)
    assert narrowed.total == 2
    assert other_invoice.invoice_no not in {row.cells[0] for row in narrowed.rows}


def test_search_page_shows_invoices_chip_and_row(client, admin, first):
    """(c) 横断の検索の画面に「請求書」の chip と、請求書の詳細への行の link が出る。"""
    _customer, _cancelled, issued = first
    response = _page(client, admin, {"q": issued.invoice_no})
    assert any(chip["kind"] == "invoices" and chip["label"] == "請求書" for chip in response.context["chips"])
    body = response.content.decode()
    assert "kind=invoices" in body
    assert 'id="search-invoices"' in body
    assert reverse("billing:invoice_detail", args=[issued.pk]) in body


def test_total_hidden_without_money(settings, client, admin, user, first):
    """(d) 金額の閲覧の無い人 (権限の判定の on) には、合計の枡が「—」で、合計の字が本文に無い (閲覧のある人には出る)。"""
    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    _customer, _cancelled, issued = first

    shown = _page(client, admin, {"q": issued.invoice_no})
    assert "¥1,375" in shown.content.decode()

    hidden = _page(client, user, {"q": issued.invoice_no})
    [section] = hidden.context["sections"]
    assert section["kind"] == "invoices"
    assert [column.label for column in section["columns"]][4] == "合計"
    assert section["rows"][0]["cells"][4]["value"] == "—"
    body = hidden.content.decode()
    assert reverse("billing:invoice_detail", args=[issued.pk]) in body
    assert "¥1,375" not in body and "1,375" not in body


def test_department_without_billing_sees_no_invoices(settings, client, first):
    """(e) 部署の判定が on で、部署に billing の無い人には chip も行も出ない (部署に billing のある人には出る)。"""
    settings.SEIZOU = {"access_control": {"department": "on"}}
    _customer, _cancelled, issued = first
    seizo = accounts_services.create_department("製造", ["workorders", "progress", "masters"], ["workorders"], None)
    accounts_services.issue_account("genba", PASSWORD, [seizo], [], {}, None)
    keiri = accounts_services.create_department("経理", ["billing", "masters"], ["billing"], None)
    accounts_services.issue_account("keiri", PASSWORD, [keiri], [], {}, None)
    detail = reverse("billing:invoice_detail", args=[issued.pk])

    outside = _page(client, User.objects.get(username="genba"), {"q": issued.invoice_no})
    assert "invoices" not in [chip["kind"] for chip in outside.context["chips"]]
    assert "invoices" not in [section["kind"] for section in outside.context["sections"]]
    assert detail not in outside.content.decode()

    inside = _page(client, User.objects.get(username="keiri"), {"q": issued.invoice_no})
    assert "invoices" in [chip["kind"] for chip in inside.context["chips"]]
    assert detail in inside.content.decode()
