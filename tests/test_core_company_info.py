"""会社の情報 (住所・電話・振込先・登録番号・ロゴ) の services・画面・帳票。作り物の値だけ。

services の update_company (値の整え・字数・記録の 1 行)、設定の画面の「会社の情報」の枠と変更の画面 (門はアカウント管理の権限)、
請求書と納品書の印刷用の HTML の会社の欄を見る。
"""

from __future__ import annotations

import io
import logging
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from seizou.billing import services as billing_services
from seizou.core import services
from seizou.core.models import Company
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from seizou.shipping.reports import delivery_note_context

ADDRESS = "〒000-0000 見本県架空市見本町 1-2-3"
PHONE = "00-0000-0000"
ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)


@pytest.fixture
def admin(db):
    return get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")


@pytest.fixture
def tester(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def manager_client(client, admin):
    client.force_login(admin)
    return client


@pytest.fixture
def plain_client(tester):
    other = Client()
    other.force_login(tester)
    return other


def settings_records(caplog):
    return [r for r in caplog.records if r.name == "seizou.settings"]


def _invoice_and_note(user):
    """得意先 1 つ・出荷 1 つの納品書と請求書 (tests/test_billing_views.py の作り方と同じ)。"""
    customer = masters_services.create_partner(
        {
            "code": "C001",
            "name": "作り物の得意先",
            "role": "customer",
            "closing_day": 20,
            "payment_month": "next",
            "payment_day": 25,
        },
        user,
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}],
        user,
        order_date=ORDER_DATE,
    )
    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), SHIPPED_AT, user)
    note = shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    return invoice, note


def _print_bodies(client, invoice, note):
    """請求書と納品書の印刷用の HTML (順に)。"""
    bodies = []
    for url in (
        reverse("billing:invoice_print", args=[invoice.pk]),
        reverse("shipping:deliverynote_print", args=[note.pk]),
    ):
        response = client.get(url)
        assert response.status_code == 200, url
        bodies.append(response.content.decode())
    return bodies


# ---------------------------------------------------------------- 2a: 住所と電話


def test_update_company_writes_info_and_logs_keys(admin, caplog):
    caplog.set_level(logging.INFO, logger="seizou.settings")

    row = services.update_company(services.company(), {"address": f" {ADDRESS} ", "phone": PHONE}, admin)

    assert (row.address, row.phone) == (ADDRESS, PHONE)
    fresh = Company.objects.get(pk=row.pk)
    assert (fresh.address, fresh.phone) == (ADDRESS, PHONE)
    records = settings_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.INFO
    message = records[0].getMessage()
    assert "会社の情報を変えた: 住所・電話" in message and "(admin)" in message
    assert ADDRESS not in message and PHONE not in message  # 値は記録に残さない

    services.update_company(services.company(), {"address": ADDRESS, "phone": PHONE}, admin)
    assert len(settings_records(caplog)) == 1  # 変わった列が無ければ書かない


def test_update_company_refuses_unknown_key_and_long_text(admin):
    row = services.company()
    name = row.name

    with pytest.raises(KeyError):
        services.update_company(row, {"name": "作り物の別の名前"}, admin)
    with pytest.raises(ValueError, match="住所は 100 字までにしてください。"):
        services.update_company(row, {"address": "あ" * 101}, admin)

    fresh = Company.objects.get(pk=row.pk)
    assert fresh.name == name
    assert fresh.address == ""


def test_company_screen_is_for_account_managers(manager_client, plain_client):
    url = reverse("core:company_update")
    services.update_company(services.company(), {"address": "〒000-0000 見本県架空市見本町 9-9-9"}, None)

    for response in (plain_client.get(url), plain_client.post(url, {"address": ADDRESS, "phone": PHONE})):
        assert response.status_code == 403
        body = response.content.decode()
        assert "この画面は開けません" in body and "アカウント管理の権限" in body
        assert "no-store" in response["Cache-Control"]
    assert Company.objects.get().address == "〒000-0000 見本県架空市見本町 9-9-9"

    response = manager_client.get(url)
    assert response.status_code == 200
    assert 'value="〒000-0000 見本県架空市見本町 9-9-9"' in response.content.decode()

    response = manager_client.post(url, {"address": ADDRESS, "phone": PHONE})
    assert response.status_code == 302
    assert response["Location"] == reverse("core:settings")
    row = Company.objects.get()
    assert (row.address, row.phone) == (ADDRESS, PHONE)


def test_settings_screen_shows_company_info(manager_client, plain_client):
    services.update_company(services.company(), {"address": ADDRESS, "phone": PHONE}, None)
    edit_url = reverse("core:company_update")

    body = manager_client.get(reverse("core:settings")).content.decode()
    assert 'id="company-info-title"' in body
    assert ADDRESS in body and PHONE in body
    assert f'href="{edit_url}"' in body

    body = plain_client.get(reverse("core:settings")).content.decode()
    assert 'id="company-info-title"' in body and ADDRESS in body
    assert edit_url not in body


def test_invoice_and_delivery_note_show_address_and_phone(manager_client, admin):
    services.update_company(services.company(), {"address": ADDRESS, "phone": PHONE}, admin)
    invoice, note = _invoice_and_note(admin)

    for body in _print_bodies(manager_client, invoice, note):
        assert ADDRESS in body
        assert f"電話 {PHONE}" in body


# ---------------------------------------------------------------- 2b: 振込先 (請求書だけ)

BANK = "作り物銀行 見本支店 普通 0000000 名義 ミホンノカイシャ"


def test_invoice_shows_bank_and_delivery_note_does_not(manager_client, admin):
    services.update_company(services.company(), {"address": ADDRESS, "phone": PHONE, "bank_account": BANK}, admin)
    invoice, note = _invoice_and_note(admin)

    invoice_body, note_body = _print_bodies(manager_client, invoice, note)

    assert f"振込先 {BANK}" in invoice_body
    assert "振込先" not in note_body and BANK not in note_body
    assert ADDRESS in note_body and f"電話 {PHONE}" in note_body
    company = delivery_note_context(note)["company"]
    assert "bank_account" not in company
    assert (company["name"], company["address"], company["phone"]) == (services.company().name, ADDRESS, PHONE)


# ---------------------------------------------------------------- 2c: ロゴ (PNG か JPEG。画面からだけ)


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def png_upload(name="ロゴ.png") -> SimpleUploadedFile:
    """試験用の小さな PNG (4×3。tests/test_manuals_views.py の png_upload と同じ作り)。"""
    buffer = io.BytesIO()
    Image.new("RGB", (4, 3), (30, 120, 200)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def test_update_company_saves_and_clears_logo(admin, media, caplog):
    caplog.set_level(logging.INFO, logger="seizou.settings")

    services.update_company(services.company(), {"logo": png_upload("ロゴ.PNG")}, admin)
    first = Company.objects.get().logo.name
    assert first.startswith("company/") and first.endswith(".png")  # 名前は logo + 小文字の拡張子
    assert (media / first).is_file()
    assert "会社の情報を変えた: ロゴ" in settings_records(caplog)[-1].getMessage()

    services.update_company(services.company(), {"logo": png_upload()}, admin)
    second = Company.objects.get().logo.name
    assert second.startswith("company/") and second != first
    assert (media / first).is_file()  # 差し替えた古いファイルは置き場に残る

    count = len(settings_records(caplog))
    services.update_company(services.company(), {"logo": Company.objects.get().logo}, admin)  # 今のファイル
    services.update_company(services.company(), {"logo": None}, admin)
    assert Company.objects.get().logo.name == second
    assert len(settings_records(caplog)) == count  # 何も変わらず、記録も増えない

    services.update_company(services.company(), {"logo": False}, admin)  # 「クリア」
    assert Company.objects.get().logo.name == ""
    assert (media / second).is_file()


def test_company_screen_refuses_non_image_logo(manager_client, media):
    url = reverse("core:company_update")
    not_image = SimpleUploadedFile("ロゴ.png", "絵でない字".encode(), content_type="image/png")
    svg = SimpleUploadedFile(
        "ロゴ.svg", b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="3"></svg>', content_type="image/svg+xml"
    )

    for upload in (not_image, svg):
        response = manager_client.post(url, {"address": ADDRESS, "phone": PHONE, "bank_account": "", "logo": upload})
        assert response.status_code == 200, upload.name
        assert 'id="id_logo_error"' in response.content.decode(), upload.name

    row = Company.objects.get()
    assert not row.logo
    assert row.address == ""  # 誤りの欄があれば何も保存しない


def test_reports_show_logo_only_when_set(manager_client, admin, media):
    invoice, note = _invoice_and_note(admin)
    name = services.company().name

    for body in _print_bodies(manager_client, invoice, note):
        assert 'class="report-logo"' not in body
        assert name in body

    response = manager_client.post(
        reverse("core:company_update"), {"address": ADDRESS, "phone": PHONE, "bank_account": "", "logo": png_upload()}
    )
    assert response.status_code == 302

    for body in _print_bodies(manager_client, invoice, note):
        assert 'class="report-logo"' in body
        assert 'src="/media/company/' in body
    settings_body = manager_client.get(reverse("core:settings")).content.decode()
    assert '<img src="/media/company/' in settings_body


# ---------------------------------------------------------------- 2d: 登録番号 (請求書だけ・形の違う番号は止める)

REGISTRATION_NO = "T0000000000001"


def test_update_company_writes_registration_no_and_logs_label_only(admin, caplog):
    caplog.set_level(logging.INFO, logger="seizou.settings")

    row = services.update_company(services.company(), {"registration_no": f" {REGISTRATION_NO} "}, admin)

    assert row.registration_no == REGISTRATION_NO
    assert Company.objects.get().registration_no == REGISTRATION_NO
    message = settings_records(caplog)[-1].getMessage()
    assert "会社の情報を変えた: 登録番号 (admin)" in message
    assert REGISTRATION_NO not in message


def test_update_company_refuses_too_long_registration_no(admin):
    services.update_company(services.company(), {"registration_no": REGISTRATION_NO}, admin)
    with pytest.raises(ValueError, match="登録番号は 14 字までにしてください。"):
        services.update_company(services.company(), {"registration_no": "T" + "1" * 14}, admin)
    assert Company.objects.get().registration_no == REGISTRATION_NO


# 全角は T0000000000001 を全角にした 14 字 (字数の誤りでなく、形の誤りで止まることを見る)
@pytest.mark.parametrize("text", ["t0000000000001", "0000000000001", "T000000000001", "Ｔ" + "０" * 12 + "１"])
def test_update_company_refuses_wrong_registration_no(admin, text):
    services.update_company(services.company(), {"registration_no": REGISTRATION_NO}, admin)
    with pytest.raises(ValueError, match="登録番号は T と 13 桁の数字で入れてください。"):
        services.update_company(services.company(), {"registration_no": text}, admin)
    assert Company.objects.get().registration_no == REGISTRATION_NO


def test_update_company_clears_registration_no_with_empty_text(admin):
    services.update_company(services.company(), {"registration_no": REGISTRATION_NO}, admin)
    services.update_company(services.company(), {"registration_no": ""}, admin)
    assert Company.objects.get().registration_no == ""


@pytest.mark.parametrize("text", ["T" + "０" * 13, REGISTRATION_NO + "\n"])
def test_check_registration_no_uses_ascii_digits_and_fullmatch(text):
    with pytest.raises(ValueError):
        services.check_registration_no(text)
    services.check_registration_no(REGISTRATION_NO)
    services.check_registration_no("")


def test_invoice_shows_registration_no_under_name_and_delivery_note_does_not(manager_client, admin):
    services.update_company(services.company(), {"address": ADDRESS, "phone": PHONE, "registration_no": REGISTRATION_NO}, admin)
    invoice, note = _invoice_and_note(admin)

    invoice_body, note_body = _print_bodies(manager_client, invoice, note)

    line = f"登録番号 {REGISTRATION_NO}"
    assert line in invoice_body
    assert invoice_body.index('class="report-company-name"') < invoice_body.index(line) < invoice_body.index(ADDRESS)
    assert "登録番号" not in note_body and REGISTRATION_NO not in note_body
    assert "registration_no" not in delivery_note_context(note)["company"]


def test_invoice_without_registration_no_has_no_line(manager_client, admin):
    services.update_company(services.company(), {"address": ADDRESS, "phone": PHONE}, admin)
    invoice, note = _invoice_and_note(admin)

    invoice_body, _note_body = _print_bodies(manager_client, invoice, note)

    assert "登録番号" not in invoice_body


def _registration_dd(body):
    """設定の画面の「登録番号」の dt の直後の dd の字。"""
    rest = body.split(">登録番号</dt>", 1)[1]
    return rest.split(">", 1)[1].split("</dd>", 1)[0]


def test_settings_screen_shows_registration_no_or_none(manager_client):
    services.company()
    assert _registration_dd(manager_client.get(reverse("core:settings")).content.decode()) == "なし"

    services.update_company(services.company(), {"registration_no": REGISTRATION_NO}, None)
    assert _registration_dd(manager_client.get(reverse("core:settings")).content.decode()) == REGISTRATION_NO


def test_company_screen_saves_and_refuses_registration_no(manager_client):
    url = reverse("core:company_update")
    body = manager_client.get(url).content.decode()
    assert 'name="registration_no"' in body and 'maxlength="14"' in body

    response = manager_client.post(url, {"address": ADDRESS, "phone": PHONE, "registration_no": REGISTRATION_NO})
    assert response.status_code == 302
    assert response["Location"] == reverse("core:settings")
    assert Company.objects.get().registration_no == REGISTRATION_NO

    response = manager_client.post(url, {"address": ADDRESS, "phone": PHONE, "registration_no": "t0000000000001"})
    assert response.status_code == 200
    assert "T と 13 桁の数字で入れてください。" in response.content.decode()
    assert Company.objects.get().registration_no == REGISTRATION_NO
