"""検索の揺れ・絞り込みの案内・詳細の部品の行 (初見テスト 2 回目の残り 3 件。11-03 昼)。

- 検索は全角と半角・ひらがなとカタカナを同じに扱う (一覧の検索と候補の口)。
- 絞り込みの案内は辿る欄 (customer__name) の末尾の名前で、重なりを消す。
- 詳細で、受注の明細のような相手の表が部品を持つなら「部品」の行を足し、小窓で開ける (E-05)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from seizou.core import fields as company_fields
from seizou.core.views import BaseListView, lookup_label, search_variants
from seizou.masters import services as masters_services
from seizou.masters.models import Item, Partner
from seizou.orders import services as orders_services
from seizou.orders.models import Order, OrderLine
from seizou.workorders import services as workorders_services
from seizou.masters.models import Process


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


def test_search_variants_fold_width_and_kana():
    assert "シャフト" in search_variants("ｼｬﾌﾄ")
    assert "シャフト" in search_variants("しゃふと")
    assert "しゃふと" in search_variants("シャフト")
    assert "WK-1001" in search_variants("ＷＫ－１００１")
    # 重ねない
    assert search_variants("abc") == ["abc"]


@pytest.mark.django_db
def test_list_search_matches_other_width_and_kana(client_logged_in):
    Item.objects.create(part_no="WK-1002", name="シャフト 12", unit="個")
    Item.objects.create(part_no="WK-1003", name="ブラケット", unit="個")

    for term in ("しゃふと", "ｼｬﾌﾄ", "ＷＫ－１００２"):
        body = client_logged_in.get(reverse("masters:item_list"), {"q": term}, HTTP_HX_REQUEST="true").content.decode()
        assert "WK-1002" in body and "WK-1003" not in body, term


@pytest.mark.django_db
def test_select_endpoint_matches_kana(client_logged_in):
    Item.objects.create(part_no="WK-1002", name="シャフト 12", unit="個")

    data = client_logged_in.get(reverse("masters:item_select"), {"q": "しゃふと"}).json()

    assert [row["text"] for row in data["items"]] == ["WK-1002 シャフト 12"]


@pytest.mark.django_db
def test_lookup_label_uses_the_last_field_and_hint_drops_duplicates(client_logged_in):
    assert lookup_label(Order, "customer__name") == "名前"
    assert lookup_label(Order, "customer__code") == "取引先コード"
    assert lookup_label(OrderLine, "order__order_no") == "受注番号"
    assert lookup_label(Order, "order_no") == "受注番号"
    assert lookup_label(Order, "nope__x") == "nope"

    body = client_logged_in.get(reverse("orders:order_list")).content.decode()
    hint = body.split('id="list-q"')[1].split('placeholder="')[1].split("で絞り込む")[0]
    labels = hint.split("・")
    assert len(labels) == len(set(labels)), hint
    assert "受注番号" in labels and "取引先コード" in labels


class _CustomerNamedOrderList(BaseListView):
    """search_fields に (lookup, 見出し) の組を書いた作り物の受注の一覧 (候補 C36 の 7。前例 tests/test_core_list_filters.py の _OrderList)。"""

    model = Order
    screen_name = "作り物の受注の一覧"
    columns = ("order_no", "customer", "order_date")
    search_fields = ("order_no", ("customer__name", "得意先の名前"))
    ordering = ("order_date", "pk")
    template_name = "core/list.html"


@pytest.mark.django_db
def test_search_fields_pair_names_the_hint_and_still_filters(user, monkeypatch):
    """組の見出しは案内にそのまま出て、絞りは組の lookup で行う。字の lookup は今のまま (初見テスト R5 の U162。候補 C36 の 7)。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})
    north = masters_services.create_partner({"code": "C001", "name": "作り物の北の得意先", "role": "customer"}, user)
    south = masters_services.create_partner({"code": "C002", "name": "作り物の南の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    line = [{"item": item, "qty": Decimal("1"), "unit_price": Decimal("100")}]
    north_order = orders_services.create_order(north, line, user, order_date=date(2026, 11, 2))
    orders_services.create_order(south, line, user, order_date=date(2026, 11, 3))

    def call(**params):
        request = RequestFactory().get("/", params)
        request.user = user
        return _CustomerNamedOrderList.as_view()(request)

    response = call()
    view = response.context_data["view"]
    assert view.search_lookups()[:2] == ["order_no", "customer__name"]
    assert response.context_data["search_hint"].startswith("受注番号・得意先の名前")
    assert "名前" not in response.context_data["search_hint"].split("・")

    found = call(q="北")
    assert [row["obj"] for row in found.context_data["rows"]] == [north_order]


@pytest.mark.django_db
def test_workorder_detail_links_the_part_through_the_order_line(client_logged_in, user):
    partner = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        partner, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}], user, order_date=date(2026, 11, 2)
    )
    cut = Process.objects.create(code="CUT", name="切断", sort_order=1)
    workorder = workorders_services.issue_workorder(order.lines.get(), Decimal("2"), [{"process": cut, "planned_qty": 2}], user)

    body = client_logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk])).content.decode()

    assert "<dt" in body and "部品" in body
    assert reverse("masters:item_modal", args=[item.pk]) in body


@pytest.mark.django_db
def test_detail_with_its_own_item_field_does_not_duplicate_the_row(client_logged_in):
    item = Item.objects.create(part_no="WK-1002", name="シャフト 12", unit="個")
    partner = Partner.objects.create(code="C001", name="作り物の得意先", role="customer")
    from seizou.masters.models import PartnerItem

    PartnerItem.objects.create(item=item, partner=partner, partner_part_no="RB-1")
    body = client_logged_in.get(reverse("masters:item_detail", args=[item.pk])).content.decode()

    assert body.count('<dt class="text-base-content/70">部品</dt>') == 0
