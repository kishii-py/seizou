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
    # 白い「編集」も ?next= で開いた詳細へ戻す (288 回目 = C63 の土台の「編集」と同じ形。本体 11-07)
    assert f'href="{reverse("shipping:shipment_update", args=[shipment.pk])}?next={reverse("shipping:shipment_detail", args=[shipment.pk])}"' in body
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
    assert 'data-notice="stop"' in body and "R08" in body
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
    assert "取消</a>" in issued.content.decode()

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
    assert 'data-notice="stop"' in body
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

    # 請求済みで止まった画面は、赤の知らせと黄の断りだけで、取り消すボタンを出さない (初見テスト R5 の候補 C03。依頼書 N196)
    billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    response = logged_in.post(url, {})
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body
    assert "取り消す</button>" not in body
    assert "保存</button>" not in body
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_ISSUED


# --- 請求済みの納品書の取消は、押す前に断りの文と link を出す (初見テスト R5 の候補 C03。依頼書 N196) ---

#: 入力のカード (core/_form_card.html) の form の頭 (画面の全体には上の帯のログアウトの form もあるので、class まで見る)
FORM_CARD = '<form method="post" class="rounded-xl'


@pytest.mark.django_db
def test_invoiced_note_cancel_screen_refuses_before_press(logged_in, user, customer, shipment, note):
    """全体の画面を GET すると、黄の断りと出荷・請求書の link と「納品書へ戻る」が出て、form と取り消すボタンは出ない。"""
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)

    page = logged_in.get(reverse("shipping:deliverynote_cancel", args=[note.pk]))

    assert page.status_code == 200
    body = page.content.decode()
    warning = body[body.index("alert-warning") :]
    warning = warning[: warning.index("</div>")]
    assert '<span class="font-semibold">取り消せません。</span>' in warning
    assert services.cancel_refusal(note).partition("。")[2].strip() in warning
    assert "この納品書を取り消します。" not in body
    assert FORM_CARD not in body
    assert f"{note.note_no} を取り消す</button>" not in body
    # 請求書の詳細の href は閉じの " まで見ない (入金の枠への行き先が後で付いても通るように)
    assert f'href="{reverse("billing:invoice_detail", args=[invoice.pk])}' in body
    assert f'href="{reverse("shipping:shipment_detail", args=[shipment.pk])}"' in body
    assert f'href="{reverse("shipping:deliverynote_detail", args=[note.pk])}"' in body
    assert "納品書へ戻る" in body
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_ISSUED


@pytest.mark.django_db
def test_invoiced_note_cancel_post_shows_refusal_without_button(logged_in, user, customer, shipment, note):
    """URL を直に開いて POST すると、赤の知らせの下に同じ黄の断りが出て、確かめの文と送信のボタンは残らない。"""
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)

    response = logged_in.post(reverse("shipping:deliverynote_cancel", args=[note.pk]), {})

    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body
    assert "alert-warning" in body
    assert "この納品書を取り消します。" not in body
    assert FORM_CARD not in body
    assert f"{note.note_no} を取り消す</button>" not in body
    assert f'href="{reverse("billing:invoice_detail", args=[invoice.pk])}' in body
    note.refresh_from_db()
    assert note.status == DeliveryNote.STATUS_ISSUED


# --- 納品書の詳細のタブの上の、請求書の link の行 (初見テスト R5 の候補 C03 の 2 晩目。依頼書 N196) ---


@pytest.mark.django_db
def test_invoiced_note_detail_links_to_invoice(logged_in, user, customer, shipment, note):
    """請求済みの納品書の詳細には、請求書の番号の link と「で請求済みです (出荷 n 件)。」が出る。主の色のボタンは出ない。"""
    detail = reverse("shipping:deliverynote_detail", args=[note.pk])
    plain = logged_in.get(detail).content.decode()
    assert "請求済み" not in plain
    assert 'id="deliverynote-invoices"' not in plain

    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    body = logged_in.get(detail).content.decode()

    assert f'href="{reverse("billing:invoice_detail", args=[invoice.pk])}' in body
    assert invoice.invoice_no in body
    assert "で請求済みです (出荷 1 件)。" in body
    assert "btn-primary" not in body
    # タブの上 (土台の block main) に出る
    assert body.index('id="deliverynote-invoices"') < body.index('role="tablist"')

    # 請求書を取り消すと、行は消える
    billing_services.cancel_invoice(invoice, user)
    assert "請求済み" not in logged_in.get(detail).content.decode()


@pytest.mark.django_db
def test_note_detail_lists_each_invoice_with_its_shipment_count(logged_in, user, customer, shipment):
    """出荷が別々の請求書に入っていれば、請求書ごとに 1 行ずつ、その請求書に入った出荷の数で出す。"""
    second = services.ship(shipment.order_line, Decimal("3"), SHIPPED_AT, user)
    # 3 つ目は前の月の出荷 (別の締め期間。同じ得意先の同じ締め期間の請求書は 1 枚だけ = R23)
    third = services.ship(shipment.order_line, Decimal("2"), date(2026, 10, 5), user)
    note = services.create_delivery_note(customer, [shipment, second, third], user)
    for row in (shipment, second, third):
        row.refresh_from_db()
    first_invoice = billing_services.create_invoice(customer, [shipment, second], user, issued_on=ISSUED_ON)
    second_invoice = billing_services.create_invoice(customer, [third], user, issued_on=ISSUED_ON)

    body = logged_in.get(reverse("shipping:deliverynote_detail", args=[note.pk])).content.decode()

    first_link = f'href="{reverse("billing:invoice_detail", args=[first_invoice.pk])}'
    second_link = f'href="{reverse("billing:invoice_detail", args=[second_invoice.pk])}'
    assert first_link in body and second_link in body
    assert body.count("で請求済みです (出荷 2 件)。") == 1
    assert body.count("で請求済みです (出荷 1 件)。") == 1
    assert body.index(first_link) < body.index(second_link)


# --- 出荷の編集の頭の 1 行 (初見テスト R5・候補 C66 の 1) ---


def _update_head(shipment) -> str:
    """出荷の編集の入力の頭の 1 行 (入金の記録の頭と同じ形)。受注の明細の字は OrderLine の短い形 (受注番号-行の番号 品番 品名)。"""
    order_no = shipment.order_line.order.order_no
    return (
        '<p class="mb-4 text-sm text-base-content/70">'
        f'<span class="font-mono">{shipment.shipment_no}</span> ({order_no}-1 T-100 作り物の軸・作り物の得意先)。</p>'
    )


@pytest.mark.django_db
def test_update_form_shows_head_line_above_fields(logged_in, shipment):
    """出荷の編集の入力の上に、出荷番号と受注の明細と得意先の 1 行が出る。

    数量の欄 (name="qty") より前にあることを見る。トップの帯のログアウトの form が先にあるので、<form method="post" の字では比べない。
    """
    response = logged_in.get(reverse("shipping:shipment_update", args=[shipment.pk]))

    assert response.status_code == 200
    html = response.content.decode()
    head = _update_head(shipment)
    assert head in html
    assert html.index(head) < html.index('name="qty"')
    assert "shipping/shipment_update_form.html" in [t.name for t in response.templates]


@pytest.mark.django_db
def test_update_form_keeps_head_line_when_stopped(logged_in, shipment, note):
    """発行の納品書に入った出荷を直そうとして止まった画面でも、頭の 1 行が出ている (保存はされない)。"""
    response = logged_in.post(reverse("shipping:shipment_update", args=[shipment.pk]), _edit_post(3))

    assert response.status_code == 200
    shipment.refresh_from_db()
    assert shipment.qty == Decimal("4")
    assert _update_head(shipment) in response.content.decode()
