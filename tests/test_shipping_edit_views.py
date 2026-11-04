"""出荷の編集と、納品書を取り消す画面 (core の土台の上)。Django の test client で HTML と表の状態を見る。

出荷の詳細の右上に土台が「編集」を出す。発行の納品書に入った出荷の編集は R08 の赤の知らせで保存しない。
納品書の詳細には、発行のときだけ「取り消す」が出る。請求済みの出荷の納品書の取消は赤の知らせで発行のまま。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.models import DeliveryNote, Shipment

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
NEW_SHIPPED_AT = date(2026, 11, 6)
ISSUED_ON = date(2026, 11, 30)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def shipment(user, customer):
    """受注 10 のうち 4 を出荷した物 (納品書はまだ無い)。"""
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return services.ship(order.lines.get(), Decimal("4"), SHIPPED_AT, user, note="作り物の備考")


@pytest.fixture
def note(user, customer, shipment):
    note = services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    return note


def _edit_post(qty, **extra):
    return {"qty": str(qty), "shipped_at": NEW_SHIPPED_AT.isoformat(), "note": "数を直した", **extra}


# --- 出荷の編集 ---


@pytest.mark.django_db
def test_shipment_detail_has_edit_link(logged_in, shipment):
    response = logged_in.get(reverse("shipping:shipment_detail", args=[shipment.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert f'href="/shipping/{shipment.pk}/edit/"' in body
    assert "編集" in body


@pytest.mark.django_db
def test_edit_form_opens_with_current_values(logged_in, shipment):
    response = logged_in.get(reverse("shipping:shipment_update", args=[shipment.pk]))

    assert response.status_code == 200
    form = response.context["form"]
    assert Decimal(str(form["qty"].value())) == Decimal("4")
    assert form["shipped_at"].value() == SHIPPED_AT
    assert form["note"].value() == "作り物の備考"
    assert "order_line" not in form.fields
    body = response.content.decode()
    assert 'value="2026-11-05"' in body
    assert "出荷の編集" in body


@pytest.mark.django_db
def test_edit_saves_and_moves_to_detail(logged_in, shipment):
    response = logged_in.post(reverse("shipping:shipment_update", args=[shipment.pk]), _edit_post(3))

    assert response.status_code == 302
    assert response["Location"] == reverse("shipping:shipment_detail", args=[shipment.pk])
    shipment.refresh_from_db()
    assert shipment.qty == Decimal("3")
    assert shipment.shipped_at == NEW_SHIPPED_AT
    assert shipment.note == "数を直した"
    assert Shipment.objects.count() == 1
    detail = logged_in.get(response["Location"])
    assert "保存しました。" in detail.content.decode()


@pytest.mark.django_db
def test_edit_on_issued_note_is_red(logged_in, shipment, note):
    response = logged_in.post(reverse("shipping:shipment_update", args=[shipment.pk]), _edit_post(3))

    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body and "R08" in body
    assert note.note_no in body
    shipment.refresh_from_db()
    assert shipment.qty == Decimal("4")
    # 入れた値は消えない
    assert response.context["form"]["qty"].value() == "3"


# --- 納品書を取り消す ---


@pytest.mark.django_db
def test_note_detail_cancel_link_only_when_issued(logged_in, user, note):
    cancel_url = f"/shipping/notes/{note.pk}/cancel/"
    issued = logged_in.get(reverse("shipping:deliverynote_detail", args=[note.pk]))
    assert issued.status_code == 200
    assert f'href="{cancel_url}"' in issued.content.decode()
    assert "取り消す" in issued.content.decode()

    services.cancel_delivery_note(note, user)
    cancelled = logged_in.get(reverse("shipping:deliverynote_detail", args=[note.pk]))
    assert cancel_url not in cancelled.content.decode()


@pytest.mark.django_db
def test_cancel_note_on_screen(logged_in, shipment, note):
    url = reverse("shipping:deliverynote_cancel", args=[note.pk])
    page = logged_in.get(url)
    assert page.status_code == 200
    assert "この納品書を取り消します。" in page.content.decode()

    response = logged_in.post(url, {})

    assert response.status_code == 302
    assert response["Location"] == reverse("shipping:deliverynote_detail", args=[note.pk])
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_CANCELLED
    detail = logged_in.get(response["Location"]).content.decode()
    assert "</span>取消</span>" in detail
    # 取消の納品書の詳細に、入っていた出荷が残る
    assert shipment.shipment_no in detail


@pytest.mark.django_db
def test_invoiced_note_cancel_is_red(logged_in, user, customer, shipment, note):
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)

    response = logged_in.post(reverse("shipping:deliverynote_cancel", args=[note.pk]), {})

    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body
    assert invoice.invoice_no in body
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_ISSUED


@pytest.mark.django_db
def test_cancel_note_screen_says_cancel(logged_in, user, customer, shipment, note):
    url = reverse("shipping:deliverynote_cancel", args=[note.pk])
    page = logged_in.get(url)
    assert page.status_code == 200
    body = page.content.decode()
    assert "取り消す</button>" in body
    assert "保存</button>" not in body
    # 欄の無い form なので、土台の「必須」の案内は出ない
    assert "「必須」の欄を埋めて" not in body

    # 請求済みで止まった画面でも、釦の字は「取り消す」のまま
    billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    response = logged_in.post(url, {})
    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body
    assert "取り消す</button>" in body
    assert "保存</button>" not in body
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_ISSUED
