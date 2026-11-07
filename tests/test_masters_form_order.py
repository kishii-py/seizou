"""部品・取引先・工程の入力の欄の並び (初見テスト 5 回目の V35。候補 C28 の 1)。

部品の編集は、使用中の欄 (編集だけに出す。N135) を会社ごとの項目の後ろ (最後) に置き、
並びを「新規登録と同じ並び + 最後に使用中」にする。form の class の属性 last_fields を入力の土台の get_form が読む。
試験の DB に拡張の表 (fields.ItemExt) は無いので、会社ごとの項目は登録だけをする (値は読めず空のまま)。
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import fields as company_fields
from seizou.masters.models import Item, Partner, Process

# 2 社の答え (examples/companies/*.yaml) の部品の会社ごとの項目と同じ形 (作り物)
ITEM_FIELDS = {
    "a_machining": [{"label": "材質"}, {"label": "図面の版"}],
    "b_plating": [{"label": "処理の種類"}, {"label": "膜厚 (μm)", "type": "number"}],
}


@pytest.fixture
def client_logged_in(client, db):
    user = get_user_model().objects.create_user(username="tester", password="tests-only")
    client.force_login(user)
    return client


@pytest.fixture
def no_registration(monkeypatch):
    """会社ごとの項目の登録を空から始める (ほかの試験の登録を残さない)。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


def field_names(client, url_name, *args):
    response = client.get(reverse(url_name, args=args))
    assert response.status_code == 200
    return list(response.context["form"].fields)


@pytest.mark.django_db
@pytest.mark.parametrize("company", sorted(ITEM_FIELDS))
def test_item_update_order_is_create_order_then_active(client_logged_in, no_registration, company):
    company_fields.register("masters.Item", "fields.ItemExt", ITEM_FIELDS[company])
    item = Item.objects.create(part_no="P-1001", name="作り物のブラケット", unit="個")

    create = field_names(client_logged_in, "masters:item_create")
    update = field_names(client_logged_in, "masters:item_update", item.pk)

    assert "active" not in create
    assert create[-2:] == ["ext__ext01", "ext__ext02"]  # 新規登録は備考の後ろに会社ごとの項目 (今のまま)
    assert update == create + ["active"]


@pytest.mark.django_db
def test_item_update_without_company_fields_keeps_active_last(client_logged_in, no_registration):
    item = Item.objects.create(part_no="P-1001", name="作り物のブラケット")
    create = field_names(client_logged_in, "masters:item_create")
    assert field_names(client_logged_in, "masters:item_update", item.pk) == create + ["active"]


@pytest.mark.django_db
def test_partner_and_process_update_order_stays(client_logged_in, no_registration):
    # 部品の会社ごとの項目を登録しても、取引先と工程の並びは今のまま (最後が使用中)
    company_fields.register("masters.Item", "fields.ItemExt", ITEM_FIELDS["b_plating"])
    partner = Partner.objects.create(code="C001", name="作り物の得意先", role=Partner.ROLE_CUSTOMER)
    process = Process.objects.create(code="CUT", name="切断", sort_order=1)

    assert field_names(client_logged_in, "masters:partner_update", partner.pk) == [
        "code",
        "name",
        "short_name",
        "role",
        "billing_method",
        "closing_day",
        "payment_month",
        "payment_day",
        "rounding",
        "tax_mode",
        "tax_rounding",
        "note",
        "active",
    ]
    assert field_names(client_logged_in, "masters:process_update", process.pk) == [
        "code",
        "name",
        "sort_order",
        "use_in_plan",
        "active",
    ]


@pytest.mark.django_db
def test_drawing_restore_order_stays(client_logged_in, no_registration):
    # 属性 last_fields の無い form (図面を版に戻す) は今の並び (戻す版・備考の後ろに会社ごとの項目) のまま
    company_fields.register("masters.Item", "fields.ItemExt", ITEM_FIELDS["a_machining"])
    item = Item.objects.create(part_no="P-1001", name="作り物のブラケット")
    assert field_names(client_logged_in, "masters:item_drawing_restore", item.pk) == [
        "version",
        "note",
        "ext__ext01",
        "ext__ext02",
    ]
