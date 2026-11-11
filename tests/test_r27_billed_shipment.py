"""R27: 請求済みの出荷 (請求書に出荷の行で入った出荷・一式の行で請求した明細の出荷・請求書の外で入金を受け取った出荷) を
直すか消そうとしたら (標準のポカよけ。会社の設定 billed_shipment。本人の決め 11-11 12:5x〜13:0x)。

出荷の編集 (shipping.update_shipment) と出荷の削除 (shipping.delete_shipment) の画面を、設定の 3 つの値 (warn・stop・off) で回す。
知らせるのときは黄の知らせと「このまま保存」が出て、confirmed=1 の 2 回目で直る (消える)。請求書の明細の写しは変わらない。
services は請求済みの出荷を断らない (新しい断る処理は無い)。services が断る出荷 (発行の納品書に入っている = R08・
delete_refusal が断る出荷) には R27 の黄を出さず、今のまま赤だけを出す (黄の後に赤を出さない)。
作り物のデータは全部 services で作る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.billing.models import InvoiceLine
from seizou.core import checks
from seizou.core.exceptions import CheckViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.models import Shipment
from tests.rules_helpers import rules_md_text

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
NEW_SHIPPED_AT = date(2026, 11, 6)
ISSUED_ON = date(2026, 11, 30)
UPDATE = "shipping.update_shipment"
DELETE = "shipping.delete_shipment"


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, qty="10", price_basis="unit"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100"), "price_basis": price_basis}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def line(user, customer, item):
    return _line(user, customer, item)


@pytest.fixture
def invoiced(user, customer, line):
    """受注 10 のうち 4 を出荷し、納品書を作らずに請求書に入れた出荷と、その請求書。"""
    shipment = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    return shipment, invoice


@pytest.fixture
def lump_billed(user, customer, item):
    """一式 300 を 60 と 40 で出荷し (納品書なし)、受注の取消でなく完了の印で終えて、一式の行 (金額 1000) で請求した明細。

    受注を取り消すと直すときに R05 の赤で止まるので、完了の印 (RULES.md R25) で終える。出荷は納品書に入れない (R08 の赤を避ける)。
    返す物は (明細, 1 つ目の出荷, 2 つ目の出荷, 請求書)。
    """
    lump = _line(user, customer, item, qty="300", price_basis="lump")
    first = services.ship(lump, Decimal("60"), SHIPPED_AT, user)
    second = services.ship(lump, Decimal("40"), SHIPPED_AT, user)
    orders_services.mark_completed(lump, user)
    lump.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [], user, issued_on=ISSUED_ON, lumps=[(lump, "1000")])
    return lump, first, second, invoice


def _edit_url(shipment):
    return reverse("shipping:shipment_update", args=[shipment.pk])


def _delete_url(shipment):
    return reverse("shipping:shipment_delete", args=[shipment.pk])


def _edit_post(qty, **extra):
    return {"qty": str(qty), "shipped_at": NEW_SHIPPED_AT.isoformat(), "note": "数を直した", **extra}


def _codes(warnings):
    return [warning.code for warning in warnings]


def _assert_warned(response, *texts):
    """黄の知らせ (R27) と「このまま保存」が出て、赤は出ていない。"""
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="check"' in body and "R27" in body and "このまま保存" in body
    assert 'name="confirmed" value="1"' in body
    assert 'data-notice="stop"' not in body
    for text in texts:
        assert text in body


def test_r27_row_in_rules_md_and_setting_key():
    assert "請求済みの出荷" in rules_md_text("R27")
    assert checks.setting_key_for("R27") == "billed_shipment"
    assert "R27" in [check.name for check in checks.registered(UPDATE)]
    assert "R27" in [check.name for check in checks.registered(DELETE)]


# --- (a) 知らせる (既定) ---


@pytest.mark.django_db
def test_r27_text_for_invoiced_shipment(settings, user, invoiced):
    settings.SEIZOU = {}
    shipment, invoice = invoiced
    warnings = checks.run(UPDATE, {"object": shipment, "qty": Decimal("3")}, user)
    assert _codes(warnings) == ["R27"]
    assert warnings[0].message == (
        f"出荷 {shipment.shipment_no} は請求書 {invoice.invoice_no} に入っています。数量を直すと、請求書の数量 (4) と違ってきます。"
        "請求書も直すときは、請求書を取り消して作り直してください。"
    )


@pytest.mark.django_db
def test_r27_warn_on_edit_screen_then_confirm(settings, logged_in, invoiced):
    """(a) 納品書を作らずに請求書に入れた出荷を直すと、黄の知らせと「このまま保存」。もう一度押すと直り、請求書の明細の数量は変わらない。"""
    settings.SEIZOU = {}
    shipment, invoice = invoiced

    first = logged_in.post(_edit_url(shipment), _edit_post(3))
    _assert_warned(first, invoice.invoice_no, "違ってきます")
    shipment.refresh_from_db()
    assert shipment.qty == Decimal("4")

    second = logged_in.post(_edit_url(shipment), _edit_post(3, confirmed="1"))
    assert second.status_code == 302
    shipment.refresh_from_db()
    assert shipment.qty == Decimal("3")
    assert InvoiceLine.objects.get(invoice=invoice, shipment=shipment).qty == Decimal("4")


# --- (b) 止める・通す ---


@pytest.mark.django_db
def test_r27_stop_on_edit_screen_is_red_and_not_saved(settings, user, logged_in, invoiced):
    settings.SEIZOU = {"billed_shipment": "stop"}
    shipment, invoice = invoiced

    with pytest.raises(CheckViolation) as caught:
        checks.run(UPDATE, {"object": shipment, "qty": Decimal("3")}, user)
    assert caught.value.code == "R27"
    assert caught.value.message.startswith("保存できません。出荷 ")

    response = logged_in.post(_edit_url(shipment), _edit_post(3))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body and "R27" in body
    assert "このまま保存" not in body
    # 「このまま保存」の印を付けて送っても、止めるの設定では直さない
    again = logged_in.post(_edit_url(shipment), _edit_post(3, confirmed="1"))
    assert again.status_code == 200
    shipment.refresh_from_db()
    assert shipment.qty == Decimal("4")


@pytest.mark.django_db
def test_r27_off_on_edit_screen_saves(settings, logged_in, invoiced):
    settings.SEIZOU = {"billed_shipment": "off"}
    shipment, _invoice = invoiced

    response = logged_in.post(_edit_url(shipment), _edit_post(3))

    assert response.status_code == 302
    shipment.refresh_from_db()
    assert shipment.qty == Decimal("3")


# --- (c) 一式の行で請求した明細の出荷 ---


@pytest.mark.django_db
def test_r27_lump_billed_shipment_edit_and_delete(settings, user, logged_in, lump_billed):
    """(c) 一式の行で請求した明細の、明細を出し終えていない出荷を直すと知らせる。消そうとしても知らせ、「このまま保存」で消える。"""
    settings.SEIZOU = {}
    lump, first, second, invoice = lump_billed
    assert not first.finishes_line and not second.finishes_line
    assert services.delete_refusal(second) == ""

    warnings = checks.run(UPDATE, {"object": first, "qty": Decimal("50")}, user)
    assert [warning.message for warning in warnings] == [
        f"出荷 {first.shipment_no} の受注の明細は、請求書 {invoice.invoice_no} の一式の行で請求済みです。"
        "出荷を直すと、一式の行の数量 (100) と出荷の合計が違ってきます。"
        "請求書も直すときは、請求書を取り消し、一式の金額を決め直して載せ直してください。"
    ]

    edited = logged_in.post(_edit_url(first), _edit_post(50))
    _assert_warned(edited, invoice.invoice_no, "一式の行で請求済みです")
    assert logged_in.post(_edit_url(first), _edit_post(50, confirmed="1")).status_code == 302
    first.refresh_from_db()
    assert first.qty == Decimal("50")

    deleting = logged_in.post(_delete_url(second), {})
    _assert_warned(deleting, invoice.invoice_no, "出荷を消すと、一式の行の数量 (100) と出荷の合計が違ってきます。")
    assert Shipment.objects.filter(pk=second.pk).exists()
    assert logged_in.post(_delete_url(second), {"confirmed": "1"}).status_code == 302
    assert not Shipment.objects.filter(pk=second.pk).exists()
    # 一式の行の数量 (写し) は変わらない。食い違いは請求書の側から読める (billing.services.changed_lines)。
    assert InvoiceLine.objects.get(invoice=invoice, order_line=lump).qty == Decimal("100")
    assert [(billed, now) for _row, billed, now in billing_services.changed_lines(invoice)] == [(Decimal("100"), Decimal("50"))]


# --- (d) 請求書の外で入金を受け取った出荷 ---


@pytest.mark.django_db
def test_r27_outside_paid_shipment_edit_warns(settings, user, logged_in, line):
    settings.SEIZOU = {}
    shipment = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    billing_services.record_outside_payment(shipment, NEW_SHIPPED_AT, Decimal("400"), user)

    warnings = checks.run(UPDATE, {"object": shipment, "qty": Decimal("3")}, user)
    assert [warning.message for warning in warnings] == [
        f"出荷 {shipment.shipment_no} には、請求書の外で受け取った入金 (2026-11-06) が記録されています。"
        "数量を直すと、受け取った代金と出荷が違ってきます。入金も直すときは、出荷の詳細でその入金を取り消してから記録し直してください。"
    ]
    _assert_warned(logged_in.post(_edit_url(shipment), _edit_post(3)), "請求書の外で受け取った入金 (2026-11-06)")


# --- (e) delete_refusal が断る出荷には黄を出さない ---


@pytest.mark.django_db
def test_r27_invoice_line_shipment_delete_stays_red(settings, user, logged_in, invoiced):
    """(e) 請求書の明細が指す出荷を消そうとしたときは、R27 の黄を出さずに、今のまま delete_refusal の赤で断る。"""
    settings.SEIZOU = {}
    shipment, _invoice = invoiced
    assert services.delete_refusal(shipment)
    assert checks.run(DELETE, {"object": shipment}, user) == []

    response = logged_in.post(_delete_url(shipment), {})

    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="check"' not in body and "このまま保存" not in body
    assert 'data-notice="stop"' in body and "削除できません。" in body
    assert Shipment.objects.filter(pk=shipment.pk).exists()


# --- (f) 請求済みでない出荷 ---


@pytest.mark.django_db
def test_r27_silent_after_invoice_cancelled_and_for_unbilled(settings, user, logged_in, line, invoiced):
    """(f) 請求書を取り消した後の出荷と、請求書に入っていない出荷・入金を取り消した出荷を直すときは何も出ない。"""
    settings.SEIZOU = {"billed_shipment": "stop"}
    shipment, invoice = invoiced
    billing_services.cancel_invoice(invoice, user)
    plain = services.ship(line, Decimal("1"), SHIPPED_AT, user)
    paid = services.ship(line, Decimal("1"), SHIPPED_AT, user)
    payment = billing_services.record_outside_payment(paid, NEW_SHIPPED_AT, Decimal("100"), user)
    billing_services.cancel_payment(payment, user)

    for target in (shipment, plain, paid):
        assert services.billed_by(target) is None
        assert checks.run(UPDATE, {"object": target, "qty": Decimal("1")}, user) == []
    assert checks.run(DELETE, {"object": plain}, user) == []
    response = logged_in.post(_edit_url(shipment), _edit_post(3))
    assert response.status_code == 302


# --- (g) services は断らない ---


@pytest.mark.django_db
def test_r27_update_service_does_not_refuse_billed_shipment(user, invoiced):
    """(g) services の update_shipment を直に呼ぶと、請求済みの出荷でも断らずに直る (新しい断る処理が無い事)。"""
    shipment, _invoice = invoiced
    assert services.billed_by(shipment)[0] == "line"
    updated = services.update_shipment(shipment, Decimal("2"), SHIPPED_AT, user)
    assert updated.qty == Decimal("2")


@pytest.mark.django_db
def test_r27_delete_service_does_not_refuse_lump_billed_shipment(user, lump_billed):
    """(g) services の delete_shipment も、一式の行で請求した明細の出荷 (delete_refusal が断らない出荷) を断らずに消す。"""
    _lump, first, _second, _invoice = lump_billed
    assert services.billed_by(first)[0] == "lump"
    services.delete_shipment(first, user)
    assert not Shipment.objects.filter(pk=first.pk).exists()


# --- (k) 発行の納品書に入った請求済みの出荷は R08 の赤だけ ---


@pytest.mark.django_db
def test_r27_shipment_in_issued_note_shows_only_r08(settings, user, logged_in, customer, line):
    """(k) 発行の納品書に入った請求済みの出荷を直すと、R27 の黄は出ずに、今のまま R08 の赤だけが出る。"""
    settings.SEIZOU = {}
    shipment = services.ship(line, Decimal("4"), SHIPPED_AT, user)
    services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    assert services.billed_by(shipment)[0] == "line"
    assert checks.run(UPDATE, {"object": shipment, "qty": Decimal("3")}, user) == []

    response = logged_in.post(_edit_url(shipment), _edit_post(3))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="check"' not in body and "R27" not in body
    assert 'data-notice="stop"' in body and "R08" in body
    shipment.refresh_from_db()
    assert shipment.qty == Decimal("4")
