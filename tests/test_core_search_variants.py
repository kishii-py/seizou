"""検索の揺れの続き (N77 の 3): 英数字の後の長音とダッシュを - と同じに扱う。注残の盤を受注番号でも探す。

search_variants は全部の一覧の検索・候補の口 (BaseSelectView)・横断検索 (部品の search.py が search_q を呼ぶ) が通る。
db の要らない物は関数を直に呼び、画面は Django の test client で見る。名前は作り物だけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core.views import SEARCH_VARIANTS_MAX, search_variants
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="tests-only-2611")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": "WK-2001", "name": "作り物の段付き軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "SB-2002", "name": "作り物のニッケルめっき処理 軸", "unit": "個"}, user),
    ]


def part_nos(response) -> list[str]:
    assert response.status_code == 200
    return [row["obj"].part_no for row in response.context["rows"]]


def test_dash_variants():
    for term in ("WKー1001", "WK‐1001", "WK−1001", "ＷＫｰ１００１", "WK–1001", "WK―1001"):
        assert "WK-1001" in search_variants(term), term
    # 直前が英数字でない長音は変えない (「メーター」の揺れを増やさない)
    assert not any("-" in variant for variant in search_variants("メーター"))
    assert "めーたー" in search_variants("メーター")
    # 今の約束のまま (重ねない・全角の「－」は NFKC で当たる)
    assert search_variants("abc") == ["abc"]
    assert "WK-1001" in search_variants("ＷＫ－１００１")
    assert len(search_variants("WKー1001")) <= SEARCH_VARIANTS_MAX


@pytest.mark.django_db
def test_list_and_cross_search_find_mixed_kana_and_dash(logged_in, items):
    item_list = reverse("masters:item_list")
    assert part_nos(logged_in.get(item_list, {"q": "WKー2001"})) == ["WK-2001"]
    assert part_nos(logged_in.get(item_list, {"q": "wk−2001"})) == ["WK-2001"]

    cross = logged_in.get(reverse("core:search"), {"q": "WKー2001"})
    assert cross.status_code == 200
    section = next(section for section in cross.context["sections"] if section["kind"] == "items")
    assert "WK-2001" in cross.content.decode() and section["total"] == 1

    picked = logged_in.get(reverse("masters:item_select"), {"q": "WKー2001"}).json()
    assert [item["text"].split()[0] for item in picked["items"]] == ["WK-2001"]


@pytest.mark.django_db
def test_orderline_search_matches_order_numbers(logged_in, user, customer, items):
    lines = [{"item": items[0], "qty": Decimal("3"), "unit_price": Decimal("100")}]
    first = orders_services.create_order(customer, lines, user, order_date=date(2026, 11, 2))
    second = orders_services.create_order(
        customer, lines, user, order_date=date(2026, 11, 3), customer_order_no="作り物-PO-77"
    )
    board = reverse("orders:orderline_list")

    def order_nos(q):
        response = logged_in.get(board, {"q": q})
        assert response.status_code == 200
        return [row["obj"].order.order_no for row in response.context["rows"]]

    assert order_nos(first.order_no) == [first.order_no]
    assert order_nos("作り物-PO-77") == [second.order_no]
    assert order_nos("作り物-PO−77") == [second.order_no]  # 全角でないマイナス (−) も - と同じ
