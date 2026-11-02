"""業務マニュアルの紐付けの欄 (部品か取引先の 1 件。見る画面の帯に印)。記録は masters の services で作る。"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.manuals import services
from seizou.masters import services as masters_services


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def partner(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def manual(user):
    return services.create_manual(None, "軸の段取り", "steps", user)


def edit_data(**extra):
    return {"title": "軸の段取り", "format": "steps", "folder": "", "body": "## 準備", **extra}


def test_edit_form_links_item_and_detail_shows_badge(client_logged_in, manual, item):
    url = reverse("manuals:manual_update", args=[manual.pk])
    page = client_logged_in.get(url).content.decode()
    assert f'data-select-url="{reverse("masters:item_select")}"' in page
    assert f'data-select-url="{reverse("masters:partner_select")}"' in page
    response = client_logged_in.post(url, edit_data(linked_item=item.pk))
    assert response.status_code == 302
    manual.refresh_from_db()
    assert manual.linked == item
    assert manual.linked_label() == "T-100 作り物の軸"
    detail = client_logged_in.get(reverse("manuals:manual_detail", args=[manual.pk])).content.decode()
    assert "紐付け: 部品 T-100 作り物の軸" in detail
    assert f'href="{reverse("masters:item_detail", args=[item.pk])}"' in detail
    assert "紐付け先の番号" not in detail
    again = client_logged_in.get(url).content.decode()
    assert f'<option value="{item.pk}" selected>' in again
    # 欄を空で送ると外れる。
    client_logged_in.post(url, edit_data())
    manual.refresh_from_db()
    assert manual.linked_id is None and manual.linked_label() == ""


def test_link_partner_and_refuse_both(client_logged_in, manual, item, partner):
    url = reverse("manuals:manual_update", args=[manual.pk])
    both = client_logged_in.post(url, edit_data(linked_item=item.pk, linked_partner=partner.pk))
    assert both.status_code == 200
    assert "紐付けは 1 件だけです。" in both.content.decode()
    manual.refresh_from_db()
    assert manual.linked_id is None
    client_logged_in.post(url, edit_data(linked_partner=partner.pk))
    manual.refresh_from_db()
    assert manual.linked == partner
    detail = client_logged_in.get(reverse("manuals:manual_detail", args=[manual.pk])).content.decode()
    assert "紐付け: 取引先 作り物の得意先" in detail


def test_link_rules_in_services(manual, item, user):
    with pytest.raises(ValueError, match="紐付けられない記録です"):
        services.update_manual(manual, {"linked": manual}, user)
    with pytest.raises(ValueError, match="紐付けられない記録です"):
        services.update_manual(manual, {"linked": "T-100"}, user)
    linked = services.update_manual(manual, {"linked": item}, user)
    assert linked.linked == item
    # 紐付け先が消えたら「(削除された記録)」。
    linked.linked_id = 999999
    assert linked.linked_label() == "(削除された記録)"
