"""小窓の「編集」(N77 の 6): 編集の権限のある人には、小窓の「詳細を見る」の隣に編集の link を出す。

判定は詳細の画面と同じ can_write_model。部品と取引先の小窓 (masters/_modal.html は core を extends する) を
Django の test client の htmx の要求で見る。編集の権限を有効にする形は tests/test_core_access_views.py を写した。
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.masters import services as masters_services

User = get_user_model()

PASSWORD = "kumo-tests-2611"
WRITE_ON = {"access_control": {"department": "off", "write": "on", "money": "off"}}


@pytest.fixture
def admin(db):
    return User.objects.create_superuser("admin", password=PASSWORD)


@pytest.fixture
def shaft(admin):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, admin)


@pytest.fixture
def customer(admin):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, admin)


def modal(client, url_name, pk) -> str:
    response = client.get(reverse(url_name, args=[pk]), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_modal_shows_edit_link_for_writers(client, admin, shaft, customer):
    client.force_login(admin)

    item = modal(client, "masters:item_modal", shaft.pk)
    assert f'href="{reverse("masters:item_update", args=[shaft.pk])}"' in item
    assert "編集</a>" in item
    assert item.count("btn-primary") == 1  # 主色は「詳細を見る」だけ
    assert item.index("編集</a>") < item.index("詳細を見る")

    partner = modal(client, "masters:partner_modal", customer.pk)
    assert f'href="{reverse("masters:partner_update", args=[customer.pk])}"' in partner
    assert partner.count("btn-primary") == 1


@pytest.mark.django_db
def test_modal_hides_edit_link_without_permission(settings, client, shaft):
    settings.SEIZOU = WRITE_ON
    accounts_services.issue_account("genba", PASSWORD, [], [], {}, None)
    client.force_login(User.objects.get(username="genba"))

    item = modal(client, "masters:item_modal", shaft.pk)
    assert reverse("masters:item_update", args=[shaft.pk]) not in item
    assert "編集</a>" not in item
    assert f'href="{reverse("masters:item_detail", args=[shaft.pk])}"' in item  # 詳細を見るは今のまま
