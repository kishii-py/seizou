"""会社の情報 (住所・電話・振込先・ロゴ) の services・画面・帳票。作り物の値だけ。

services の update_company (値の整え・字数・記録の 1 行)、設定の画面の「会社の情報」の枠と変更の画面 (門はアカウント管理の権限)、
請求書と納品書の印刷用の HTML の会社の欄を見る。
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.core import services
from seizou.core.models import Company
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

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
