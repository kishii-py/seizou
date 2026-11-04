"""部品マスターと取引先の画面 (core の土台の上)。"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters.models import Item, Partner


@pytest.fixture
def client_logged_in(client, db):
    user = get_user_model().objects.create_user(username="tester", password="tests-only")
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_item_list_requires_login(client):
    response = client.get(reverse("masters:item_list"))
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


@pytest.mark.django_db
def test_item_list_renders_columns_and_rows(client_logged_in):
    Item.objects.create(part_no="P-1001", name="ブラケット", unit="個")
    response = client_logged_in.get(reverse("masters:item_list"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "部品マスター" in body and "自社品番" in body and "P-1001" in body
    # 品目の名前は小窓を開く link (master_link) になる
    assert reverse("masters:item_modal", args=[Item.objects.get().pk]) in body


@pytest.mark.django_db
def test_item_list_search_and_htmx_rows(client_logged_in):
    Item.objects.create(part_no="P-1001", name="ブラケット")
    Item.objects.create(part_no="P-2002", name="シャフト")
    response = client_logged_in.get(reverse("masters:item_list"), {"q": "シャフト"}, HTTP_HX_REQUEST="true")
    body = response.content.decode()
    assert "P-2002" in body and "P-1001" not in body
    assert "<html" not in body  # htmx には行の部分だけ


@pytest.mark.django_db
def test_item_create_goes_through_service_and_redirects(client_logged_in):
    response = client_logged_in.post(
        reverse("masters:item_create"),
        {"part_no": "P-3003", "name": "カバー", "unit": "個", "standard_price": "120.00"},
    )
    item = Item.objects.get(part_no="P-3003")
    assert response.status_code == 302
    assert response["Location"] == reverse("masters:item_detail", args=[item.pk])


@pytest.mark.django_db
def test_item_create_duplicate_part_no_is_red_notice(client_logged_in):
    Item.objects.create(part_no="P-1001", name="ブラケット")
    response = client_logged_in.post(reverse("masters:item_create"), {"part_no": "P-1001", "name": "別の物"})
    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body and "既にあります" in body
    assert Item.objects.count() == 1


@pytest.mark.django_db
def test_item_modal_and_detail(client_logged_in):
    item = Item.objects.create(part_no="P-1001", name="ブラケット")
    # 小窓の中身は htmx の要求 (HX-Request) に返す。htmx でない要求は詳細へ移す (新しいタブで開いた時)。
    modal = client_logged_in.get(reverse("masters:item_modal", args=[item.pk]), HTTP_HX_REQUEST="true")
    assert modal.status_code == 200
    assert "詳細を見る" in modal.content.decode()
    detail = client_logged_in.get(reverse("masters:item_detail", args=[item.pk]))
    assert detail.status_code == 200
    assert "取引先ごとの品番" in detail.content.decode()


@pytest.mark.django_db
def test_partner_select_returns_candidates(client_logged_in):
    Partner.objects.create(code="C001", name="作り物の得意先", role="customer")
    response = client_logged_in.get(reverse("masters:partner_select"), {"q": "作り物"})
    data = response.json()
    assert data["items"][0]["text"].startswith("C001") or "作り物" in data["items"][0]["text"]
    assert data["more"] is False


@pytest.mark.django_db
def test_partner_select_narrows_by_role(client_logged_in):
    """GET の role で取引先の種類を絞る (精査 A9 の 4 節の 1)。role が無い・知らない値なら全部。"""
    customer = Partner.objects.create(code="C001", name="作り物の得意先", role="customer")
    subcontractor = Partner.objects.create(code="S001", name="作り物の協力企業", role="subcontractor")
    both = Partner.objects.create(code="B001", name="作り物の両方", role="both")

    def ids(**params):
        return {row["id"] for row in client_logged_in.get(reverse("masters:partner_select"), params).json()["items"]}

    assert ids(role="customer") == {customer.pk, both.pk}
    assert ids(role="supplier") == {subcontractor.pk, both.pk}
    assert ids() == {customer.pk, subcontractor.pk, both.pk}
    assert ids(role="unknown") == {customer.pk, subcontractor.pk, both.pk}
    # 字の検索と一緒に効く
    assert ids(role="customer", q="作り物の協力企業") == set()


@pytest.mark.django_db
def test_menu_lists_masters_screens(client_logged_in):
    response = client_logged_in.get(reverse("core:home"))
    body = response.content.decode()
    assert reverse("masters:item_list") in body and reverse("masters:partner_list") in body


@pytest.mark.django_db
def test_item_update_form_opens_with_current_values(client_logged_in):
    """編集の画面は、今の値が入った状態で開く (ServiceModelForm に __init__ を足したときに空で開いた不具合の再発を止める)。"""
    item = Item.objects.create(part_no="P-7007", name="作り物の軸受")
    response = client_logged_in.get(reverse("masters:item_update", args=[item.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'value="P-7007"' in body and 'value="作り物の軸受"' in body
