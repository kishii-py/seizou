"""指示書の横断の検索 (core の検索の画面の約束に乗る。精査の番号 12 の指示書の分。本体の決め 11-11 14:2x)。

指示書番号だけで指示書を引く (品番では探さない。部品マスターと受注の結果と重なるため)。取引先で絞ると受注の得意先で絞る。
部署の判定が on なら、部署で指示書を開けない人には出さない。名前と品番は作り物。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.workorders import services

User = get_user_model()

PASSWORD = "kumo-tests-2611"
ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def admin(db):
    return User.objects.create_superuser("admin", password=PASSWORD)


@pytest.fixture
def item(admin):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, admin)


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1)


def _issue(admin, item, cut, code, name, count):
    """得意先を 1 つ作り、その受注の明細に指示書を count 本発行する。(得意先, 指示書の並び)。"""
    customer = masters_services.create_partner({"code": code, "name": name, "role": "customer"}, admin)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}], admin, order_date=ORDER_DATE
    )
    line = order.lines.get()
    workorders = [
        services.issue_workorder(line, Decimal(n + 2), [{"process": cut, "planned_qty": Decimal(n + 2)}], admin)
        for n in range(count)
    ]
    return customer, workorders


@pytest.fixture
def first(admin, item, cut):
    """得意先 1 つ目の指示書 2 本。"""
    return _issue(admin, item, cut, "C001", "作り物の得意先", 2)


@pytest.fixture
def second(admin, item, cut):
    """得意先 2 つ目の指示書 1 本。"""
    return _issue(admin, item, cut, "C002", "作り物の別の得意先", 1)


def _page(client, user, params):
    client.force_login(User.objects.get(pk=user.pk))
    response = client.get(reverse("core:search"), params)
    assert response.status_code == 200
    return response


def _search():
    from seizou.workorders import search

    return search


def test_found_by_part_of_wo_no(first):
    """(a) 指示書番号の一部 (頭の 1 字を除いた字) で当たり、種類は「指示書」、行の url は指示書の詳細。"""
    search = _search()
    _customer, (one, _two) = first
    section = search.search(one.wo_no[1:])
    assert (section.kind, section.label) == ("workorders", "指示書")
    assert section.total == 1
    row = section.rows[0]
    assert row.cells == (one.wo_no, "T-100", "作り物の軸", Decimal("2"), one.issued_on, "未着手")
    assert row.url == reverse("workorders:workorder_detail", args=[one.pk])
    assert row.match is None
    # 探す項目は指示書番号だけ (品番と品名では当たらない)
    assert search.search_labels() == ["指示書番号"]
    assert search.search("T-100").total == 0
    assert search.search("作り物の軸").total == 0


def test_found_by_other_width_and_newest_first(admin, first):
    """全角の字でも当たる (語の揺れは core の search_q)。並びは発行日の新しい順 (同じ日は後に発行した方が先)。状態は一覧と同じ字。"""
    search = _search()
    _customer, (one, two) = first
    services.cancel_workorder(two, admin, reason="作り物の取消")
    wide = "".join(chr(ord(ch) + 0xFEE0) if "!" <= ch <= "~" else ch for ch in "WO-")
    section = search.search(wide)
    assert [row.cells[0] for row in section.rows] == [two.wo_no, one.wo_no]
    assert [row.cells[5] for row in section.rows] == ["取消", "未着手"]


def test_partner_filter_hides_other_customers(first, second):
    """(b) 取引先で絞ると、ほかの得意先の受注の指示書は出ない。"""
    search = _search()
    customer, workorders = first
    _other, (other,) = second
    assert search.search("WO-").total == 3
    narrowed = search.search("WO-", partner=customer.pk)
    assert narrowed.total == 2
    assert {row.cells[0] for row in narrowed.rows} == {wo.wo_no for wo in workorders}
    assert other.wo_no not in {row.cells[0] for row in narrowed.rows}


def test_search_reads_rows_without_query_per_row(first, second, django_assert_max_num_queries):
    """状態の字 (実績から出す) のために行ごとに工程を読まない (件数・1 ページ・工程の 3 回)。"""
    search = _search()
    with django_assert_max_num_queries(3):
        section = search.search("WO-")
    assert len(section.rows) == 3


def test_search_page_shows_workorders_chip_and_row(client, admin, first):
    """(c) 横断の検索の画面に「指示書」の chip と、指示書の詳細への行の link が出る。"""
    _customer, (one, _two) = first
    response = _page(client, admin, {"q": one.wo_no})
    assert any(chip["kind"] == "workorders" and chip["label"] == "指示書" for chip in response.context["chips"])
    body = response.content.decode()
    assert "kind=workorders" in body
    assert 'id="search-workorders"' in body
    assert reverse("workorders:workorder_detail", args=[one.pk]) in body


def test_department_without_workorders_sees_no_workorders(settings, client, first):
    """(d) 部署の判定が on で、部署に workorders の無い人には chip も行も出ない (部署に workorders のある人には出る)。"""
    settings.SEIZOU = {"access_control": {"department": "on"}}
    _customer, (one, _two) = first
    sales = accounts_services.create_department("受注", ["orders", "masters"], ["orders"], None)
    accounts_services.issue_account("jimu", PASSWORD, [sales], [], {}, None)
    seizo = accounts_services.create_department("製造", ["workorders", "progress", "masters"], ["workorders"], None)
    accounts_services.issue_account("genba", PASSWORD, [seizo], [], {}, None)
    detail = reverse("workorders:workorder_detail", args=[one.pk])

    outside = _page(client, User.objects.get(username="jimu"), {"q": one.wo_no})
    assert "workorders" not in [chip["kind"] for chip in outside.context["chips"]]
    assert "workorders" not in [section["kind"] for section in outside.context["sections"]]
    assert "指示書" not in [row["label"] for row in outside.context["searched_fields"]]
    assert detail not in outside.content.decode()

    inside = _page(client, User.objects.get(username="genba"), {"q": one.wo_no})
    assert "workorders" in [chip["kind"] for chip in inside.context["chips"]]
    assert detail in inside.content.decode()
