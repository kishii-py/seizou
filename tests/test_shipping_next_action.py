"""出荷と納品書の詳細の頭の右の緑のボタン = その伝票で次にする操作 (UX-2 a。11-06 22:1x。初見テスト R5 の候補 C93)。

出荷: 納品書が無いか取消の納品書の出荷は、緑の「納品書作成」(納品書の小窓)。そのとき「編集」は白にして副のボタンの終わりに置く。
納品書の権限が無い人 (編集の判定が on のとき) には「納品書作成」を出さない。
納品書: 発行の納品書で、発行の請求書に入っていない出荷があれば、「⋯」の右に緑の「請求書作成」(画面の全体へ移る)。
全部の出荷が請求書に入った納品書には出さない (C03-1 a)。判定は DeliveryNote.has_unbilled_shipments。
作り物のデータは services で作る (受注と出荷は tests/test_core_screen_polish.py の物を借りる)。
"""

from datetime import date

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.shipping import services as shipping_services
from seizou.shipping.models import DeliveryNote
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


# ---------------------------------------------------------------- 納品書

ISSUED_ON = date(2026, 11, 30)


def _note_body(client, note):
    response = client.get(reverse("shipping:deliverynote_detail", args=[note.pk]))
    assert response.status_code == 200
    return response.content.decode()


def _invoice_green(note):
    detail = reverse("shipping:deliverynote_detail", args=[note.pk])
    return f'<a class="btn btn-primary" href="{reverse("billing:invoice_create")}?customer={note.customer_id}&amp;next={detail}">'


def _unbilled(note):
    """cached_property なので、読み直した納品書で見る。"""
    return DeliveryNote.objects.get(pk=note.pk).has_unbilled_shipments


def _assert_invoice_green(body, note):
    head = _actions(body)
    green = _invoice_green(note)
    assert green in head and "請求書作成" in head
    assert head.index("data-detail-more") < head.index(green)
    assert body.count("btn-primary") == 1


@pytest.mark.django_db
def test_unbilled_note_has_green_invoice_button(client, user, order):
    note = _delivery_note(user, order, [_shipment(user, order)])
    client.force_login(user)

    _assert_invoice_green(_note_body(client, note), note)
    assert _unbilled(note) is True


@pytest.mark.django_db
def test_invoice_button_follows_billed_shipments(client, user, order):
    first = _shipment(user, order, "4")
    second = _shipment(user, order, "6")
    note = _delivery_note(user, order, [first, second])
    client.force_login(user)

    # 2 つの出荷のうち 1 つだけを請求書に入れた納品書: まだ緑
    billing_services.create_invoice(order.customer, [first], user, issued_on=ISSUED_ON)
    _assert_invoice_green(_note_body(client, note), note)
    assert _unbilled(note) is True

    # 全部を請求書に入れた納品書: 「請求書作成」も請求書の新規登録の道も出さない
    invoice = billing_services.create_invoice(order.customer, [second], user, issued_on=ISSUED_ON)
    body = _note_body(client, note)
    assert "請求書作成" not in body
    assert reverse("billing:invoice_create") not in body
    assert body.count("btn-primary") == 0
    assert _unbilled(note) is False

    # その請求書を取り消すと、入っていた出荷はまた請求できるので緑に戻る
    billing_services.cancel_invoice(invoice, user)
    _assert_invoice_green(_note_body(client, note), note)
    assert _unbilled(note) is True


@pytest.mark.django_db
def test_cancelled_note_has_no_invoice_button(client, user, order):
    note = _delivery_note(user, order, [_shipment(user, order)])
    shipping_services.cancel_delivery_note(note, user)
    client.force_login(user)

    body = _note_body(client, note)

    assert "請求書作成" not in body
    assert body.count("btn-primary") == 0
