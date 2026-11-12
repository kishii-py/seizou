"""発注の横断の検索 (core の検索の画面の約束に乗る。精査の番号 12。本体の決め 11-11 14:2x)。

発注番号で発注を引く。品番の列は明細の 1 行目と「ほか n 件」。取引先で絞ると発注の取引先で絞る。金額の列は出さない。
部署の判定が on なら、部署で発注を開けない人には出さない。
発注は相手に送らずに services で作る (send=False)。名前と品番は作り物。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.masters import services as masters_services
from seizou.purchasing import search, services

User = get_user_model()

PASSWORD = "kumo-tests-2611"


@pytest.fixture
def admin(db):
    return User.objects.create_superuser("admin", password=PASSWORD)


@pytest.fixture
def items(admin):
    return (
        masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, admin),
        masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "個"}, admin),
    )


def _partner(admin, code, name):
    return masters_services.create_partner({"code": code, "name": name, "role": "subcontractor"}, admin)


def _order(admin, partner, items, order_date):
    lines = [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("25")} for item in items]
    return services.create_purchase_order(partner, lines, admin, order_date=order_date, send=False)


@pytest.fixture
def first(admin, items):
    """協力企業 1 つ目の発注 2 件。明細 2 行の発注 (11-02) と、明細 1 行の発注 (11-03)。(協力企業, 2 行の発注, 1 行の発注)。"""
    partner = _partner(admin, "S001", "作り物の協力企業")
    two = _order(admin, partner, items, date(2026, 11, 2))
    one = _order(admin, partner, items[1:], date(2026, 11, 3))
    return partner, two, one


@pytest.fixture
def second(admin, items):
    """協力企業 2 つ目の発注 1 件。(協力企業, 発注)。"""
    partner = _partner(admin, "S002", "作り物の別の協力企業")
    return partner, _order(admin, partner, items[:1], date(2026, 11, 4))


def _page(client, user, params):
    client.force_login(User.objects.get(pk=user.pk))
    response = client.get(reverse("core:search"), params)
    assert response.status_code == 200
    return response


def test_found_by_part_of_po_no(first):
    """(a) 発注番号の一部 (頭の 1 字を除いた字) で当たり、種類は「発注」、行の url は発注の詳細。明細が 2 行なら品番は「<品番> ほか 1 件」。"""
    partner, two, one = first
    section = search.search(two.po_no[1:])
    assert (section.kind, section.label) == ("purchaseorders", "発注")
    assert section.total == 1
    row = section.rows[0]
    assert row.cells == (two.po_no, partner, date(2026, 11, 2), "T-100 ほか 1 件", two.get_status_display())
    assert row.url == reverse("purchasing:purchaseorder_detail", args=[two.pk])
    assert row.match is None
    # 明細が 1 行なら品番だけ
    assert search.search(one.po_no[1:]).rows[0].cells[3] == "T-200"
    # 金額の列は出さない
    assert not any(column.money for column in search.COLUMNS)
    # 並びは発注日の新しい順
    assert [row.cells[0] for row in search.search("PO-").rows] == [one.po_no, two.po_no]


def test_partner_filter_hides_other_partners(first, second):
    """(b) 取引先で絞ると、ほかの取引先の発注は出ない。"""
    partner, _two, _one = first
    _other, other_order = second
    assert search.search("PO-").total == 3
    narrowed = search.search("PO-", partner=partner.pk)
    assert narrowed.total == 2
    assert other_order.po_no not in {row.cells[0] for row in narrowed.rows}


def test_department_without_purchasing_sees_no_purchase_orders(settings, client, first):
    """(c) 部署の判定が on で、部署に purchasing の無い人には chip も行も出ない (部署に purchasing のある人には出る)。"""
    settings.SEIZOU = {"access_control": {"department": "on"}}
    _partner_row, two, _one = first
    seizo = accounts_services.create_department("製造", ["workorders", "progress", "masters"], ["workorders"], None)
    accounts_services.issue_account("genba", PASSWORD, [seizo], [], {}, None)
    kobai = accounts_services.create_department("購買", ["purchasing", "masters"], ["purchasing"], None)
    accounts_services.issue_account("kobai", PASSWORD, [kobai], [], {}, None)
    detail = reverse("purchasing:purchaseorder_detail", args=[two.pk])

    outside = _page(client, User.objects.get(username="genba"), {"q": two.po_no})
    assert "purchaseorders" not in [chip["kind"] for chip in outside.context["chips"]]
    assert "purchaseorders" not in [section["kind"] for section in outside.context["sections"]]
    assert detail not in outside.content.decode()

    inside = _page(client, User.objects.get(username="kobai"), {"q": two.po_no})
    assert "purchaseorders" in [chip["kind"] for chip in inside.context["chips"]]
    assert "purchaseorders" in [section["kind"] for section in inside.context["sections"]]
    assert detail in inside.content.decode()
