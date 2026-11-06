"""出荷と納品書の詳細の頭の右の緑のボタン = その伝票で次にする操作 (UX-2 a。11-06 22:1x。初見テスト R5 の候補 C93)。

出荷: 納品書が無いか取消の納品書の出荷は、緑の「納品書作成」(納品書の小窓)。そのとき「編集」は白にして副のボタンの終わりに置く。
納品書の権限が無い人 (編集の判定が on のとき) には「納品書作成」を出さない。
作り物のデータは services で作る (受注と出荷は tests/test_core_screen_polish.py の物を借りる)。
"""

import pytest
from django.urls import reverse

from seizou.shipping import services as shipping_services
from tests import test_core_screen_polish
from tests.test_core_access import account
from tests.test_core_screen_polish import _delivery_note, _shipment
from tests.test_detail_header import _actions

# 画面の小さな直しの試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
user = test_core_screen_polish.user
customer = test_core_screen_polish.customer
item = test_core_screen_polish.item
order = test_core_screen_polish.order


def _shipment_head(client, shipment):
    response = client.get(reverse("shipping:shipment_detail", args=[shipment.pk]))
    assert response.status_code == 200
    return _actions(response.content.decode())


def _note_link(shipment):
    detail = reverse("shipping:shipment_detail", args=[shipment.pk])
    return f'{reverse("shipping:deliverynote_create")}?shipments={shipment.pk}&amp;next={detail}'


def _assert_note_green(head, shipment):
    link = _note_link(shipment)
    green = f'<a class="btn btn-primary" href="{link}" data-modal-form="{link}">'
    white_edit = f'<a class="btn btn-outline border-base-300" href="{reverse("shipping:shipment_update", args=[shipment.pk])}'
    assert green in head and "納品書作成" in head
    assert white_edit in head
    # 並びは ラベル → PDF → 白の「編集」→ 緑の「納品書作成」
    assert head.index("PDF</a>") < head.index(white_edit) < head.index(green)
    assert head.count("btn-primary") == 1


# ---------------------------------------------------------------- 出荷


@pytest.mark.django_db
def test_shipment_without_note_has_green_note_button(client, user, order):
    shipment = _shipment(user, order)
    client.force_login(user)

    _assert_note_green(_shipment_head(client, shipment), shipment)


@pytest.mark.django_db
def test_shipment_in_cancelled_note_has_green_note_button(client, user, order):
    shipment = _shipment(user, order)
    note = _delivery_note(user, order, [shipment])
    shipping_services.cancel_delivery_note(note, user)
    shipment.refresh_from_db()
    client.force_login(user)

    _assert_note_green(_shipment_head(client, shipment), shipment)


@pytest.mark.django_db
def test_shipment_in_issued_note_keeps_green_edit(client, user, order):
    shipment = _shipment(user, order)
    _delivery_note(user, order, [shipment])
    client.force_login(user)

    head = _shipment_head(client, shipment)

    assert "納品書作成" not in head
    assert f'<a class="btn btn-primary" href="{reverse("shipping:shipment_update", args=[shipment.pk])}' in head
    assert head.count("btn-primary") == 1


@pytest.mark.django_db
def test_note_button_follows_note_permission(settings, client, user, order):
    settings.SEIZOU = {"access_control": {"write": "on"}}
    shipment = _shipment(user, order)
    detail = reverse("shipping:shipment_detail", args=[shipment.pk])

    # 出荷の権限の無い人: 詳細は開けるが、押すと断られる「納品書作成」を出さない
    client.force_login(account("eigyo", permissions=["orders"]))
    response = client.get(detail)
    assert response.status_code == 200
    body = response.content.decode()
    assert "納品書作成" not in body
    assert reverse("shipping:deliverynote_create") not in body

    # 出荷の権限のある人: 緑で出る
    client.force_login(account("shukka", permissions=["shipping"]))
    _assert_note_green(_shipment_head(client, shipment), shipment)
